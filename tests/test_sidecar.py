"""Executable v1 protocol tests using stand-in corpora-core commands."""

import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from corpora_cli.cli import main

CHECKER = """
import json, pathlib, signal, sys, time
import os
if os.environ.get("FAKE_TOOL_LOG"):
    with open(os.environ["FAKE_TOOL_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps([pathlib.Path(sys.argv[0]).name, *sys.argv[2:]]) + "\\n")
source_path = pathlib.Path(sys.argv[1])
source = source_path.read_bytes()
if source.startswith(b"slow"):
    time.sleep(30)
if source.startswith(b"ignore"):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    source_path.with_suffix(".ready").write_text("ready")
    time.sleep(30)
if source.startswith(b"stubborn"):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    source_path.with_suffix(".pid").write_text(str(os.getpid()))
    source_path.with_suffix(".ready").write_text("ready")
    time.sleep(30)
invalid = source.startswith(b"invalid")
diagnostics = []
if invalid:
    diagnostics = [{
        "code": "EPUB_XML",
        "message": "Malformed XHTML",
        "blocking": True,
        "locations": [{"start": {"line": 3, "column": 2}}],
    }]
report = {
    "findings": [{"member": "OEBPS/chapter.xhtml", "report": {"diagnostics": diagnostics}}]
        if diagnostics else [],
    "documents": [] if invalid else [{"id": "chapter"}],
}
print(json.dumps(report))
sys.exit(1 if invalid else 0)
"""

CONVERTER = """
import hashlib, json, pathlib, sys
import os
if os.environ.get("FAKE_TOOL_LOG"):
    with open(os.environ["FAKE_TOOL_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps([pathlib.Path(sys.argv[0]).name, *sys.argv[2:]]) + "\\n")
source, output = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
data = source.read_bytes()
if data.startswith(b"fail-convert"):
    sys.exit(1)
output.mkdir()
(output / "tf").mkdir()
(output / "tf" / "otype.tf").write_text("node\t1\\n", encoding="utf-8")
(output / "sources").mkdir()
(output / "sources" / "original.epub").write_bytes(data)
(output / "manifest.json").write_text(json.dumps({
    "format": "corpora-epub-to-tf/2",
    "source": {
        "sha256": hashlib.sha256(data).hexdigest(),
        "file": "sources/original.epub",
    },
}), encoding="utf-8")
print(json.dumps({"output": str(output)}))
"""


def _script(path: Path, body: str) -> Path:
    path.write_text(f"#!/usr/bin/env python3\n{body}", encoding="utf-8")
    path.chmod(0o700)
    return path


@pytest.fixture
def tools(tmp_path: Path) -> tuple[Path, Path]:
    return _script(tmp_path / "corpora-epub-check", CHECKER), _script(
        tmp_path / "epub-to-tf", CONVERTER
    )


class SidecarProcess:
    def __init__(self, tmp_path: Path, tools: tuple[Path, Path] | None) -> None:
        environment = os.environ.copy()
        environment["XDG_CACHE_HOME"] = str(tmp_path / "cache")
        if tools is None:
            environment["CORPORA_EPUB_CHECK_BIN"] = str(tmp_path / "missing-checker")
            environment["CORPORA_EPUB_TO_TF_BIN"] = str(tmp_path / "missing-exporter")
        else:
            environment["CORPORA_EPUB_CHECK_BIN"] = str(tools[0])
            environment["CORPORA_EPUB_TO_TF_BIN"] = str(tools[1])
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "from corpora_cli.cli import main; raise SystemExit(main())",
                "sidecar",
                "--protocol",
                "1",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            bufsize=0,
        )
        self.pending = bytearray()
        self.ready = self.receive()

    def send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message).encode("utf-8") + b"\n")

    def receive(self, timeout: float = 8) -> dict[str, Any]:
        assert self.process.stdout is not None
        while True:
            newline = self.pending.find(b"\n")
            if newline >= 0:
                line = bytes(self.pending[:newline])
                del self.pending[: newline + 1]
                return json.loads(line)
            readable, _, _ = select.select([self.process.stdout], [], [], timeout)
            if not readable:
                raise AssertionError("timed out waiting for a sidecar event")
            chunk = os.read(self.process.stdout.fileno(), 64 * 1024)
            if not chunk:
                raise AssertionError("sidecar closed stdout before the expected event")
            self.pending.extend(chunk)

    def finish(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        self.process.wait(timeout=10)
        assert self.process.returncode == 0, self.stderr()

    def stderr(self) -> str:
        assert self.process.stderr is not None
        return self.process.stderr.read().decode("utf-8", errors="replace")


def _start(
    request_id: str,
    source: Path,
    output: Path,
    *,
    repair: bool = False,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "protocol": 1,
        "type": "start",
        "request_id": request_id,
        "operation": "epub_to_tf",
        "input": {"path": str(source)},
        "output": {"path": str(output)},
        "options": {"repair": repair} if options is None else options,
    }


def _tool_calls(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def _run_to_finish(sidecar: SidecarProcess) -> dict[str, Any]:
    while (event := sidecar.receive())["type"] != "finished":
        pass
    return event


@pytest.mark.parametrize(
    ("options", "expected"),
    [({}, "8000000"), ({"max_slots": 4000000}, "4000000"), ({"max_slots": 20000000}, "20000000")],
)
def test_sidecar_passes_the_slot_limit_to_both_core_tools(
    tmp_path, tools, monkeypatch, options, expected
):
    log = tmp_path / "tools.log"
    monkeypatch.setenv("FAKE_TOOL_LOG", str(log))
    source = tmp_path / "book.epub"
    source.write_bytes(b"valid EPUB bytes")
    sidecar = SidecarProcess(tmp_path, tools)

    sidecar.send(_start("start-1", source, tmp_path / "book-tf", options=options))
    assert sidecar.receive()["type"] == "accepted"
    assert _run_to_finish(sidecar)["outcome"] == "succeeded"
    sidecar.finish()

    calls = _tool_calls(log)
    assert [call[0] for call in calls] == ["corpora-epub-check", "epub-to-tf"]
    for call in calls:
        assert call[call.index("--max-slots") + 1] == expected


@pytest.mark.parametrize("max_slots", [0, -1, True, "4000000", 2.5])
def test_sidecar_rejects_an_invalid_slot_limit(tmp_path, tools, max_slots):
    source = tmp_path / "book.epub"
    source.write_bytes(b"valid EPUB bytes")
    sidecar = SidecarProcess(tmp_path, tools)

    sidecar.send(_start("start-1", source, tmp_path / "book-tf", options={"max_slots": max_slots}))

    error = sidecar.receive()
    assert error["type"] == "error"
    assert error["code"] == "invalid_options"
    assert not (tmp_path / "book-tf").exists()
    sidecar.finish()


def test_sidecar_runs_a_complete_epub_job_and_publishes_atomically(tmp_path, tools):
    source = tmp_path / "book.epub"
    source.write_bytes(b"valid EPUB bytes")
    output = tmp_path / "book-tf"
    sidecar = SidecarProcess(tmp_path, tools)
    assert sidecar.ready == {"protocol": 1, "type": "ready", "capabilities": ["epub_to_tf"]}

    sidecar.send(_start("start-1", source, output))
    accepted = sidecar.receive()
    assert accepted["type"] == "accepted"
    assert accepted["request_id"] == "start-1"
    assert Path(accepted["staging_path"]).is_absolute()

    events = [sidecar.receive() for _ in range(4)]
    assert [event["type"] for event in events] == ["progress"] * 3 + ["finished"]
    assert [event["phase"] for event in events[:3]] == [
        "validating",
        "converting",
        "finalizing",
    ]
    finished = events[-1]
    assert finished["outcome"] == "succeeded"
    assert finished["outputs"] == [{"kind": "tf_bundle", "path": str(output)}]
    assert not Path(accepted["staging_path"]).exists()
    assert json.loads((output / "manifest.json").read_text())["format"] == "corpora-epub-to-tf/2"
    assert (output / "sources" / "original.epub").read_bytes() == source.read_bytes()

    sidecar.send(
        {"protocol": 1, "type": "cancel", "request_id": "cancel-1", "job_id": accepted["job_id"]}
    )
    assert sidecar.receive()["code"] == "job_not_running"
    sidecar.finish()


def test_sidecar_streams_core_diagnostics_and_cleans_failed_staging(tmp_path, tools):
    source = tmp_path / "bad.epub"
    source.write_bytes(b"invalid EPUB")
    output = tmp_path / "bad-tf"
    sidecar = SidecarProcess(tmp_path, tools)
    sidecar.send(_start("start-bad", source, output))
    accepted = sidecar.receive()
    assert accepted["type"] == "accepted"
    progress = sidecar.receive()
    diagnostic = sidecar.receive()
    finished = sidecar.receive()
    assert progress["phase"] == "validating"
    assert diagnostic == {
        "protocol": 1,
        "job_id": accepted["job_id"],
        "type": "diagnostic",
        "severity": "error",
        "code": "EPUB_XML",
        "message": "Malformed XHTML",
        "location": {"member": "OEBPS/chapter.xhtml", "line": 3, "column": 2},
    }
    assert finished["outcome"] == "failed"
    assert finished["error"]["code"] == "invalid_epub"
    assert finished["outputs"] == []
    assert not output.exists()
    assert not Path(accepted["staging_path"]).exists()
    sidecar.finish()


def test_sidecar_cancels_and_reaps_the_converter_tree_before_terminal_event(tmp_path, tools):
    source = tmp_path / "slow.epub"
    source.write_bytes(b"slow EPUB")
    output = tmp_path / "slow-tf"
    sidecar = SidecarProcess(tmp_path, tools)
    sidecar.send(_start("start-slow", source, output))
    accepted = sidecar.receive()
    assert sidecar.receive()["phase"] == "validating"

    sidecar.send(
        {"protocol": 1, "type": "cancel", "request_id": "cancel-slow", "job_id": accepted["job_id"]}
    )
    ack = sidecar.receive()
    terminal = sidecar.receive()
    assert ack["type"] == "cancel_ack" and ack["status"] == "requested"
    assert terminal["type"] == "finished" and terminal["outcome"] == "cancelled"
    assert terminal["outputs"] == []
    assert not output.exists()
    assert not Path(accepted["staging_path"]).exists()
    sidecar.finish()


def test_sidecar_escalates_cancellation_when_checker_ignores_sigint(tmp_path, tools):
    source = tmp_path / "ignore.epub"
    source.write_bytes(b"ignore SIGINT")
    output = tmp_path / "ignored-cancel"
    sidecar = SidecarProcess(tmp_path, tools)
    sidecar.send(_start("start-ignore", source, output))
    accepted = sidecar.receive()
    assert sidecar.receive()["phase"] == "validating"
    marker = Path(accepted["staging_path"]) / "source.ready"
    deadline = time.monotonic() + 3
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.exists()

    sidecar.send(
        {
            "protocol": 1,
            "type": "cancel",
            "request_id": "cancel-ignore",
            "job_id": accepted["job_id"],
        }
    )
    assert sidecar.receive()["type"] == "cancel_ack"
    finished = sidecar.receive(timeout=10)
    assert finished["type"] == "finished"
    assert finished["outcome"] == "cancelled"
    assert not Path(accepted["staging_path"]).exists()
    sidecar.finish()


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_sidecar_stops_descendants_that_ignore_sigterm_before_reporting_cancelled(tmp_path, tools):
    source = tmp_path / "stubborn.epub"
    source.write_bytes(b"stubborn checker")
    output = tmp_path / "stubborn-cancel"
    sidecar = SidecarProcess(tmp_path, tools)
    sidecar.send(_start("start-stubborn", source, output))
    accepted = sidecar.receive()
    assert sidecar.receive()["phase"] == "validating"
    stage = Path(accepted["staging_path"])
    deadline = time.monotonic() + 3
    while not (stage / "source.ready").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert (stage / "source.ready").exists()
    checker_pid = int((stage / "source.pid").read_text())

    sidecar.send(
        {
            "protocol": 1,
            "type": "cancel",
            "request_id": "cancel-stubborn",
            "job_id": accepted["job_id"],
        }
    )
    assert sidecar.receive()["type"] == "cancel_ack"
    finished = sidecar.receive(timeout=15)
    assert finished["type"] == "finished"
    assert finished["outcome"] == "cancelled"
    # The terminal event promises the whole worker tree has stopped.
    assert not _process_exists(checker_pid)
    assert not stage.exists()
    sidecar.finish()


def test_sidecar_cancels_and_reaps_when_client_closes_stdout(tmp_path, tools):
    source = tmp_path / "closed-pipe.epub"
    source.write_bytes(b"slow converter")
    sidecar = SidecarProcess(tmp_path, tools)
    assert sidecar.process.stdout is not None
    sidecar.process.stdout.close()

    sidecar.send(_start("closed-output", source, tmp_path / "closed-output"))
    assert sidecar.process.stdin is not None
    sidecar.process.stdin.close()
    sidecar.process.wait(timeout=10)
    assert sidecar.process.returncode == 1
    assert not list(tmp_path.glob(".corpora-sidecar-*"))


def test_sidecar_does_not_advertise_or_start_without_both_core_tools(tmp_path):
    source = tmp_path / "book.epub"
    source.write_bytes(b"valid")
    sidecar = SidecarProcess(tmp_path, None)
    assert sidecar.ready["capabilities"] == []
    sidecar.send(_start("start-no-runtime", source, tmp_path / "book-tf"))
    error = sidecar.receive()
    assert error["type"] == "error"
    assert error["code"] == "runtime_unavailable"
    sidecar.finish()


def test_sidecar_reports_unknown_fields_and_closes_on_reused_request_id(tmp_path, tools):
    sidecar = SidecarProcess(tmp_path, tools)
    sidecar.send({"protocol": 1, "type": "mystery", "request_id": "same", "extra": True})
    error = sidecar.receive()
    assert error["type"] == "error" and error["code"] == "unknown_field"

    sidecar.send({"protocol": 1, "type": "unknown", "request_id": "same"})
    protocol_error = sidecar.receive()
    assert protocol_error["type"] == "protocol_error"
    assert protocol_error["code"] == "duplicate_request_id"
    sidecar.finish()


def test_sidecar_rejects_existing_destinations_without_overwriting(tmp_path, tools):
    source = tmp_path / "book.epub"
    source.write_bytes(b"valid")
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("keep me")
    sidecar = SidecarProcess(tmp_path, tools)
    sidecar.send(_start("start-existing", source, output))
    error = sidecar.receive()
    assert error["type"] == "error" and error["code"] == "output_exists"
    assert marker.read_text() == "keep me"
    sidecar.finish()


def test_convert_epub_human_command_uses_the_same_job_pipeline(
    tmp_path, tools, monkeypatch, capsys
):
    source = tmp_path / "human.epub"
    source.write_bytes(b"valid EPUB")
    output = tmp_path / "human-tf"
    monkeypatch.setenv("CORPORA_EPUB_CHECK_BIN", str(tools[0]))
    monkeypatch.setenv("CORPORA_EPUB_TO_TF_BIN", str(tools[1]))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    assert main(["convert-epub", str(source), "--output", str(output)]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == str(output)
    assert "validating: Checking the EPUB" in captured.err
    assert "finalizing: Publishing the bundle" in captured.err
    assert output.is_dir()


@pytest.mark.parametrize(
    ("args", "expected"), [([], "8000000"), (["--max-slots", "20000000"], "20000000")]
)
def test_convert_epub_passes_its_slot_limit_to_both_core_tools(
    tmp_path, tools, monkeypatch, args, expected
):
    log = tmp_path / "tools.log"
    monkeypatch.setenv("FAKE_TOOL_LOG", str(log))
    source = tmp_path / "long.epub"
    source.write_bytes(b"valid EPUB")
    monkeypatch.setenv("CORPORA_EPUB_CHECK_BIN", str(tools[0]))
    monkeypatch.setenv("CORPORA_EPUB_TO_TF_BIN", str(tools[1]))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    assert main(["convert-epub", str(source), "--output", str(tmp_path / "out"), *args]) == 0

    calls = _tool_calls(log)
    assert [call[0] for call in calls] == ["corpora-epub-check", "epub-to-tf"]
    for call in calls:
        assert call[call.index("--max-slots") + 1] == expected


def test_convert_epub_rejects_a_non_positive_slot_limit(tmp_path, tools, monkeypatch):
    source = tmp_path / "long.epub"
    source.write_bytes(b"valid EPUB")
    monkeypatch.setenv("CORPORA_EPUB_CHECK_BIN", str(tools[0]))
    monkeypatch.setenv("CORPORA_EPUB_TO_TF_BIN", str(tools[1]))

    args = ["convert-epub", str(source), "--output", str(tmp_path / "out"), "--max-slots", "0"]
    with pytest.raises(SystemExit) as excinfo:
        main(args)
    assert excinfo.value.code == 2
    assert not (tmp_path / "out").exists()
