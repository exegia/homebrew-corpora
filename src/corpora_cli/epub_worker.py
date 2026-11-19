"""Run the core EPUB checker/exporter for one sidecar job."""

from __future__ import annotations

import argparse
import hashlib
import json
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

MAX_WORKER_FRAME_BYTES = 1024 * 1024
MAX_DIAGNOSTIC_TEXT_BYTES = MAX_WORKER_FRAME_BYTES - 4096


class WorkerFailureError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _write_event(event: dict[str, Any]) -> None:
    encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) + 1 > MAX_WORKER_FRAME_BYTES:
        raise WorkerFailureError(
            "diagnostic_too_large", "A diagnostic exceeds the protocol frame limit."
        )
    sys.stdout.buffer.write(encoded + b"\n")
    sys.stdout.buffer.flush()


def _read_command(command: list[str], *, output_limit: int) -> tuple[int, bytes]:
    """Capture small converter output while leaving diagnostics private."""
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise WorkerFailureError(
            "runtime_unavailable", "The EPUB conversion tools could not start."
        ) from error

    assert process.stdout is not None
    captured = bytearray()
    try:
        while chunk := process.stdout.read(64 * 1024):
            if len(captured) + len(chunk) > output_limit:
                _stop_command(process)
                raise WorkerFailureError(
                    "diagnostic_too_large", "The EPUB validator report exceeds the supported limit."
                )
            captured.extend(chunk)
        return process.wait(), bytes(captured)
    except KeyboardInterrupt:
        # The sidecar signals this worker and all descendants as one process
        # group. Each Python converter layer waits for its own child here.
        process.wait()
        raise
    finally:
        if not process.stdout.closed:
            process.stdout.close()


def _stop_command(process: subprocess.Popen[bytes]) -> None:
    try:
        process.send_signal(signal.SIGINT)
    except ProcessLookupError:
        pass
    if process.stdout is not None:
        process.stdout.close()
    try:
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    for signum in (signal.SIGTERM, signal.SIGKILL):
        try:
            process.send_signal(signum)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
            return
        except subprocess.TimeoutExpired:
            continue
    process.wait()


class _JsonReader:
    """Read only diagnostics from the core report without retaining huge text fields."""

    def __init__(self, stream: Any) -> None:
        self.stream = stream

    def _peek(self) -> bytes:
        value = self.stream.peek(1)[:1]
        return value

    def _take(self) -> bytes:
        value = self.stream.read(1)
        return value

    def _space(self) -> None:
        while self._peek() in (b" ", b"\t", b"\r", b"\n"):
            self._take()

    def _expect(self, token: bytes) -> None:
        self._space()
        if self._take() != token:
            self._invalid()

    def _invalid(self) -> None:
        raise WorkerFailureError(
            "invalid_checker_report", "The EPUB validator returned an invalid report."
        )

    def _string(self, *, limit: int, ignore_oversize: bool = False) -> str | None:
        self._expect(b'"')
        raw = bytearray(b'"')
        escaped = False
        too_large = False
        while True:
            char = self._take()
            if not char:
                self._invalid()
            if not too_large:
                raw.extend(char)
                if len(raw) > limit:
                    if not ignore_oversize:
                        raise WorkerFailureError(
                            "diagnostic_too_large",
                            "A diagnostic exceeds the protocol frame limit.",
                        )
                    too_large = True
                    raw.clear()
            value = char[0]
            if escaped:
                escaped = False
            elif value == 0x5C:
                escaped = True
            elif value == 0x22:
                break
        if too_large:
            return None
        try:
            decoded = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkerFailureError(
                "invalid_checker_report", "The EPUB validator returned an invalid report."
            ) from error
        if not isinstance(decoded, str):
            self._invalid()
        return decoded

    def _skip_string(self) -> None:
        self._expect(b'"')
        escaped = False
        while True:
            char = self._take()
            if not char:
                self._invalid()
            value = char[0]
            if escaped:
                escaped = False
            elif value == 0x5C:
                escaped = True
            elif value == 0x22:
                return

    def _fields(self):
        self._expect(b"{")
        self._space()
        if self._peek() == b"}":
            self._take()
            return
        seen: set[str] = set()
        while True:
            key = self._string(limit=4096)
            assert key is not None
            if key in seen:
                self._invalid()
            seen.add(key)
            self._expect(b":")
            yield key
            self._space()
            delimiter = self._take()
            if delimiter == b"}":
                return
            if delimiter != b",":
                self._invalid()

    def _items(self):
        self._expect(b"[")
        self._space()
        if self._peek() == b"]":
            self._take()
            return
        while True:
            yield
            self._space()
            delimiter = self._take()
            if delimiter == b"]":
                return
            if delimiter != b",":
                self._invalid()

    def _scalar(self) -> Any:
        self._space()
        if self._peek() == b'"':
            return self._string(limit=MAX_DIAGNOSTIC_TEXT_BYTES)
        raw = bytearray()
        while self._peek() and self._peek() not in (b" ", b"\t", b"\r", b"\n", b",", b"]", b"}"):
            raw.extend(self._take())
            if len(raw) > 128:
                self._invalid()
        try:
            return json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkerFailureError(
                "invalid_checker_report", "The EPUB validator returned an invalid report."
            ) from error

    def _skip_value(self, depth: int = 0) -> None:
        if depth > 256:
            self._invalid()
        self._space()
        token = self._peek()
        if token == b'"':
            self._skip_string()
        elif token == b"{":
            for _key in self._fields():
                self._skip_value(depth + 1)
        elif token == b"[":
            for _item in self._items():
                self._skip_value(depth + 1)
        else:
            self._scalar()

    def _start_location(self) -> dict[str, int]:
        position: dict[str, int] = {}
        for key in self._fields():
            if key in ("line", "column"):
                value = self._scalar()
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    position[key] = value
            else:
                self._skip_value()
        return position

    def _locations(self) -> dict[str, int]:
        first: dict[str, int] = {}
        for index, _item in enumerate(self._items()):
            current: dict[str, int] = {}
            for key in self._fields():
                if key == "start":
                    current = self._start_location()
                else:
                    self._skip_value()
            if index == 0:
                first = current
        return first

    def _diagnostic(self, member: str | None) -> None:
        code: str | None = None
        message: str | None = None
        blocking = False
        position: dict[str, int] = {}
        for key in self._fields():
            if key in ("code", "message"):
                value = self._scalar()
                if isinstance(value, str):
                    if key == "code":
                        code = value
                    else:
                        message = value
            elif key == "blocking":
                value = self._scalar()
                blocking = value is True
            elif key == "locations":
                position = self._locations()
            else:
                self._skip_value()
        if not code or not message:
            self._invalid()
        event: dict[str, Any] = {
            "type": "diagnostic",
            "severity": "error" if blocking else "warning",
            "code": code,
            "message": message,
        }
        location: dict[str, Any] = {}
        if member and not member.startswith(("/", "\\")):
            location["member"] = member
        location.update(position)
        if location:
            event["location"] = location
        _write_event(event)

    def _diagnostics(self, member: str | None) -> None:
        for _item in self._items():
            self._diagnostic(member)

    def _report(self, member: str | None) -> None:
        for key in self._fields():
            if key == "diagnostics":
                self._diagnostics(member)
            else:
                self._skip_value()

    def _finding(self) -> None:
        member: str | None = None
        for key in self._fields():
            if key == "member":
                member = self._string(limit=64 * 1024, ignore_oversize=True)
            elif key == "report":
                self._report(member)
            else:
                self._skip_value()

    def _findings(self) -> None:
        for _item in self._items():
            self._finding()

    def report(self) -> None:
        self._expect(b"{")
        seen = False
        self._space()
        if self._peek() == b"}":
            self._invalid()
        while True:
            key = self._string(limit=4096)
            assert key is not None
            self._expect(b":")
            if key == "findings":
                if seen:
                    self._invalid()
                seen = True
                self._findings()
            else:
                self._skip_value()
            self._space()
            delimiter = self._take()
            if delimiter == b"}":
                break
            if delimiter != b",":
                self._invalid()
        self._space()
        if self._peek() or not seen:
            self._invalid()


def _run_checker(checker: Path, source: Path) -> int:
    try:
        process = subprocess.Popen(
            [str(checker), str(source)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise WorkerFailureError(
            "runtime_unavailable", "The EPUB validator could not start."
        ) from error
    assert process.stdout is not None
    try:
        _JsonReader(process.stdout).report()
        return process.wait()
    except KeyboardInterrupt:
        process.wait()
        raise
    except WorkerFailureError:
        _stop_command(process)
        raise
    finally:
        if not process.stdout.closed:
            process.stdout.close()


def run(source: Path, output: Path, checker: Path, converter: Path, repair: bool) -> int:
    _write_event({"type": "progress", "phase": "validating", "message": "Checking the EPUB"})
    check_code = _run_checker(checker, source)

    if check_code == 1 and not repair:
        raise WorkerFailureError("invalid_epub", "EPUB validation found blocking issues.")
    if check_code not in (0, 1):
        raise WorkerFailureError(
            "epub_validation_failed", "The EPUB validator could not check this file."
        )

    _write_event({"type": "progress", "phase": "converting", "message": "Writing Text-Fabric"})
    command = [str(converter), str(source), str(output), "--checker", str(checker)]
    if repair:
        command.append("--repair")
    converter_code, _ = _read_command(command, output_limit=1024 * 1024)
    if converter_code != 0:
        raise WorkerFailureError(
            "conversion_failed", "The EPUB could not be exported to Text-Fabric."
        )

    manifest_path = output / "manifest.json"
    if not output.is_dir() or output.is_symlink() or not manifest_path.is_file():
        raise WorkerFailureError(
            "invalid_bundle", "The EPUB exporter did not produce a complete bundle."
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkerFailureError(
            "invalid_bundle", "The EPUB exporter did not produce a valid manifest."
        ) from error
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    source_record = manifest.get("source") if isinstance(manifest, dict) else None
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != "corpora-epub-to-tf/2"
        or not isinstance(source_record, dict)
        or source_record.get("sha256") != source_hash
        or source_record.get("file") != "sources/original.epub"
        or not (output / "tf").is_dir()
        or not (output / "sources" / "original.epub").is_file()
    ):
        raise WorkerFailureError(
            "invalid_bundle", "The EPUB exporter did not produce a complete bundle."
        )

    _write_event({"type": "progress", "phase": "finalizing", "message": "Publishing the bundle"})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checker", type=Path, required=True)
    parser.add_argument("--converter", type=Path, required=True)
    parser.add_argument("--repair", action="store_true")
    try:
        args = parser.parse_args()
        result = run(args.source, args.output, args.checker, args.converter, args.repair)
        payload = {"type": "worker_result", "outcome": "succeeded" if result == 0 else "failed"}
    except KeyboardInterrupt:
        payload = {"type": "worker_result", "outcome": "cancelled"}
    except WorkerFailureError as error:
        payload = {
            "type": "worker_result",
            "outcome": "failed",
            "code": error.code,
            "message": error.message,
        }
    except BaseException:
        payload = {
            "type": "worker_result",
            "outcome": "failed",
            "code": "conversion_failed",
            "message": "The EPUB conversion did not complete.",
        }

    try:
        _write_event(payload)
    except WorkerFailureError:
        return 1
    return 0 if payload["outcome"] in ("succeeded", "cancelled") else 1


if __name__ == "__main__":
    raise SystemExit(main())
