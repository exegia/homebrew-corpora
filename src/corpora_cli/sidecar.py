"""Versioned local job protocol for desktop clients."""

from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import json
import os
import queue
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

from corpora_cli.epub_runtime import MAX_SLOTS, EpubRuntime, discover_epub_runtime

PROTOCOL = 1
MAX_FRAME_BYTES = 1024 * 1024
MAX_INPUT_BYTES = 80 * 1024 * 1024
MAX_ACTIVE_JOBS = 2
REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z", re.ASCII)


class JobError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ProtocolViolationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Job:
    job_id: str
    request_id: str
    output: Path
    stage_name: str
    stage_path: Path
    stage_device: int
    stage_inode: int
    parent_fd: int
    lock_fd: int
    process: subprocess.Popen[bytes]
    guard: threading.Lock = field(default_factory=threading.Lock)
    cancel_requested: bool = False
    cancellation_escalation_started: bool = False
    finished: bool = False


def _encode(message: dict[str, Any]) -> bytes:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    frame = payload + b"\n"
    if len(frame) > MAX_FRAME_BYTES:
        raise JobError("diagnostic_too_large", "A protocol message exceeds the frame limit.")
    return frame


def _cache_lock_directory() -> Path:
    if os.environ.get("XDG_CACHE_HOME"):
        cache = Path(os.environ["XDG_CACHE_HOME"]).expanduser()
        if not cache.is_absolute():
            raise JobError(
                "reservation_unavailable", "The local job reservation store is unavailable."
            )
    elif sys.platform == "darwin":
        cache = Path.home() / "Library" / "Caches"
    else:
        cache = Path.home() / ".cache"
    directory = cache / "corpora-cli" / "sidecar-locks"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise JobError("reservation_unavailable", "The local job reservation store is unavailable.")
    if stat.S_IMODE(info.st_mode) != 0o700:
        os.chmod(directory, 0o700)
    return directory


def _reserve_output(output: Path) -> int:
    digest = hashlib.sha256(os.fsencode(output)).hexdigest()
    lock_path = _cache_lock_directory() / f"{digest}.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise JobError(
            "reservation_unavailable", "The output destination could not be reserved."
        ) from error
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        os.close(descriptor)
        raise JobError("reservation_unavailable", "The output destination could not be reserved.")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        os.close(descriptor)
        raise JobError(
            "output_reserved", "Another job is using this output destination."
        ) from error
    return descriptor


def _open_directory(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise JobError("invalid_output", "The output parent directory is unavailable.") from error
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise JobError("invalid_output", "The output parent must be a directory.")
    try:
        visible = path.lstat()
    except OSError as error:
        os.close(descriptor)
        raise JobError(
            "invalid_output", "The output parent directory changed unexpectedly."
        ) from error
    opened = os.fstat(descriptor)
    if not stat.S_ISDIR(visible.st_mode) or (visible.st_dev, visible.st_ino) != (
        opened.st_dev,
        opened.st_ino,
    ):
        os.close(descriptor)
        raise JobError("invalid_output", "The output parent directory changed unexpectedly.")
    return descriptor


def _resolve_input(value: object) -> Path:
    if not isinstance(value, str) or not value or "\0" in value:
        raise JobError("invalid_input", "Input must be an absolute local file path.")
    candidate = Path(value)
    if not candidate.is_absolute():
        raise JobError("invalid_input", "Input must be an absolute local file path.")
    try:
        resolved = candidate.resolve(strict=True)
        info = resolved.stat()
    except OSError as error:
        raise JobError("invalid_input", "The EPUB input file is unavailable.") from error
    if not stat.S_ISREG(info.st_mode):
        raise JobError("invalid_input", "The EPUB input must be a regular file.")
    if info.st_size > MAX_INPUT_BYTES:
        raise JobError("input_too_large", "The EPUB archive exceeds the 80 MiB input limit.")
    return resolved


def _resolve_output(value: object, source: Path) -> tuple[Path, int]:
    if not isinstance(value, str) or not value or "\0" in value:
        raise JobError("invalid_output", "Output must be an absolute local directory path.")
    candidate = Path(value)
    if not candidate.is_absolute() or candidate.name in ("", ".", ".."):
        raise JobError("invalid_output", "Output must be an absolute local directory path.")
    try:
        parent = candidate.parent.resolve(strict=True)
    except OSError as error:
        raise JobError("invalid_output", "The output parent directory is unavailable.") from error
    parent_fd = _open_directory(parent)
    output = parent / candidate.name
    try:
        if os.path.lexists(output):
            raise JobError("output_exists", "The output destination already exists.")
        if output == source:
            raise JobError("invalid_output", "Input and output must be different paths.")
        try:
            source.relative_to(output)
        except ValueError:
            pass
        else:
            raise JobError("invalid_output", "The output cannot contain the input file.")
        return output, parent_fd
    except BaseException:
        os.close(parent_fd)
        raise


def _snapshot_source(source: Path, stage_fd: int) -> None:
    try:
        before = source.stat()
        source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise JobError(
            "invalid_input", "The EPUB input file could not be opened safely."
        ) from error
    target_fd = -1
    try:
        opened = os.fstat(source_fd)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise JobError("invalid_input", "The EPUB input changed while the job was starting.")
        target_fd = os.open(
            "source.epub",
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=stage_fd,
        )
        total = 0
        while chunk := os.read(source_fd, 1024 * 1024):
            total += len(chunk)
            if total > MAX_INPUT_BYTES:
                raise JobError(
                    "input_too_large", "The EPUB archive exceeds the 80 MiB input limit."
                )
            view = memoryview(chunk)
            while view:
                written = os.write(target_fd, view)
                view = view[written:]
        os.fsync(target_fd)
    except OSError as error:
        raise JobError("invalid_input", "The EPUB input could not be copied safely.") from error
    finally:
        os.close(source_fd)
        if target_fd >= 0:
            os.close(target_fd)


def _rename_noreplace(parent_fd: int, source_name: str, output_name: str) -> None:
    """Atomically publish a sibling directory only if the destination is absent."""
    libc = ctypes.CDLL(None, use_errno=True)
    source = os.fsencode(source_name)
    output = os.fsencode(output_name)
    if sys.platform == "darwin":
        try:
            rename = libc.renameatx_np
        except AttributeError as error:
            raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable") from error
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(parent_fd, source, parent_fd, output, 0x00000004)  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        try:
            rename = libc.renameat2
        except AttributeError as error:
            raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable") from error
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(parent_fd, source, parent_fd, output, 0x00000001)  # RENAME_NOREPLACE
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable")
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), output_name)


def _remove_stage(job: Job) -> bool:
    try:
        info = os.stat(job.stage_name, dir_fd=job.parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != (
        job.stage_device,
        job.stage_inode,
    ):
        return False
    try:
        shutil.rmtree(job.stage_name, dir_fd=job.parent_fd)
    except OSError:
        return False
    try:
        os.stat(job.stage_name, dir_fd=job.parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _publish_stage(job: Job) -> None:
    try:
        visible_parent = job.output.parent.stat()
    except OSError as error:
        raise JobError(
            "output_parent_changed", "The output parent directory changed unexpectedly."
        ) from error
    opened_parent = os.fstat(job.parent_fd)
    if (visible_parent.st_dev, visible_parent.st_ino) != (
        opened_parent.st_dev,
        opened_parent.st_ino,
    ):
        raise JobError("output_parent_changed", "The output parent directory changed unexpectedly.")
    stage_fd = os.open(
        job.stage_name,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=job.parent_fd,
    )
    published = False
    try:
        stage_info = os.fstat(stage_fd)
        if (stage_info.st_dev, stage_info.st_ino) != (job.stage_device, job.stage_inode):
            raise JobError("staging_changed", "The job staging directory changed unexpectedly.")
        os.unlink("source.epub", dir_fd=stage_fd)
        bundle_fd = os.open(
            "bundle",
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=stage_fd,
        )
        try:
            for name in os.listdir(bundle_fd):
                os.rename(name, name, src_dir_fd=bundle_fd, dst_dir_fd=stage_fd)
        finally:
            os.close(bundle_fd)
        os.rmdir("bundle", dir_fd=stage_fd)
        _rename_noreplace(job.parent_fd, job.stage_name, job.output.name)
        published = True
    except FileExistsError as error:
        raise JobError("output_exists", "The output destination already exists.") from error
    except OSError as error:
        if error.errno == errno.EEXIST:
            raise JobError("output_exists", "The output destination already exists.") from error
        raise JobError(
            "publish_failed", "The completed Text-Fabric bundle could not be published."
        ) from error
    finally:
        try:
            os.close(stage_fd)
        except OSError:
            if not published:
                raise


class EpubJobManager:
    """Own destination reservations, child processes, and one terminal event per job."""

    def __init__(self, runtime: EpubRuntime | None = None) -> None:
        self.runtime = runtime if runtime is not None else discover_epub_runtime()
        self.events: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    @property
    def capabilities(self) -> list[str]:
        return ["epub_to_tf"] if self.runtime is not None else []

    def active_count(self) -> int:
        with self._lock:
            return sum(not job.finished for job in self.jobs.values())

    def start(
        self,
        request_id: str,
        source_value: object,
        output_value: object,
        repair: bool,
        max_slots: int = MAX_SLOTS,
    ) -> dict[str, Any]:
        if self.runtime is None:
            raise JobError("runtime_unavailable", "The EPUB conversion tools are not installed.")
        if self.active_count() >= MAX_ACTIVE_JOBS:
            raise JobError(
                "too_many_jobs", "The maximum number of active EPUB jobs has been reached."
            )

        source = _resolve_input(source_value)
        output, parent_fd = _resolve_output(output_value, source)
        lock_fd = -1
        stage_path: Path | None = None
        stage_fd = -1
        process: subprocess.Popen[bytes] | None = None
        registered = False
        job_id = "job-" + uuid.uuid4().hex
        stage_name = ".corpora-sidecar-" + uuid.uuid4().hex
        try:
            lock_fd = _reserve_output(output)
            if os.path.lexists(output):
                raise JobError("output_exists", "The output destination already exists.")
            os.mkdir(stage_name, 0o700, dir_fd=parent_fd)
            stage_path = output.parent / stage_name
            stage_fd = os.open(
                stage_name,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            stage_info = os.fstat(stage_fd)
            _snapshot_source(source, stage_fd)
            bundle_path = stage_path / "bundle"
            command = [
                sys.executable,
                "-m",
                "corpora_cli.epub_worker",
                "--source",
                str(stage_path / "source.epub"),
                "--output",
                str(bundle_path),
                "--checker",
                str(self.runtime.checker),
                "--converter",
                str(self.runtime.converter),
                "--max-slots",
                str(max_slots),
            ]
            if repair:
                command.append("--repair")
            environment = os.environ.copy()
            environment.pop("PYTHONPATH", None)
            environment.pop("PYTHONHOME", None)
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=environment,
            )
            job = Job(
                job_id=job_id,
                request_id=request_id,
                output=output,
                stage_name=stage_name,
                stage_path=stage_path,
                stage_device=stage_info.st_dev,
                stage_inode=stage_info.st_ino,
                parent_fd=parent_fd,
                lock_fd=lock_fd,
                process=process,
            )
            with self._lock:
                self.jobs[job_id] = job
                registered = True
            threading.Thread(target=self._monitor, args=(job,), daemon=True).start()
            return {"job_id": job_id, "staging_path": str(stage_path)}
        except BaseException:
            if registered:
                with self._lock:
                    self.jobs.pop(job_id, None)
            if process is not None:
                self._stop_process(process)
            if stage_fd >= 0:
                os.close(stage_fd)
                stage_fd = -1
            if stage_path is not None and os.path.lexists(stage_path):
                shutil.rmtree(stage_path, ignore_errors=True)
            if lock_fd >= 0:
                os.close(lock_fd)
            os.close(parent_fd)
            raise
        finally:
            if stage_fd >= 0:
                os.close(stage_fd)

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None:
            return False
        start_escalation = False
        with job.guard:
            if job.finished:
                return False
            job.cancel_requested = True
            try:
                os.killpg(job.process.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
            if not job.cancellation_escalation_started:
                job.cancellation_escalation_started = True
                start_escalation = True
        if start_escalation:
            threading.Thread(target=self._escalate_cancellation, args=(job,), daemon=True).start()
        return True

    def cancel_all(self) -> None:
        for job_id in list(self.jobs):
            self.cancel(job_id)

    @staticmethod
    def _escalate_cancellation(job: Job) -> None:
        """Bound cancellation when native converter code ignores SIGINT."""
        try:
            job.process.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            pass
        with job.guard:
            if job.finished:
                return
            try:
                os.killpg(job.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            job.process.wait(timeout=2)
            return
        except subprocess.TimeoutExpired:
            pass
        with job.guard:
            if job.finished:
                return
            try:
                os.killpg(job.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    @staticmethod
    def _stop_process(process: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(process.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            pass
        for signum in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, signum)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
                return
            except subprocess.TimeoutExpired:
                continue
        process.wait()

    def _monitor(self, job: Job) -> None:
        result: dict[str, Any] | None = None
        monitor_error: JobError | None = None
        stdout = job.process.stdout
        assert stdout is not None
        try:
            while True:
                line = stdout.readline(MAX_FRAME_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_FRAME_BYTES or not line.endswith(b"\n"):
                    monitor_error = JobError(
                        "worker_protocol_error", "The EPUB worker returned an invalid event."
                    )
                    self._stop_process(job.process)
                    break
                try:
                    event = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    monitor_error = JobError(
                        "worker_protocol_error", "The EPUB worker returned an invalid event."
                    )
                    self._stop_process(job.process)
                    break
                if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                    monitor_error = JobError(
                        "worker_protocol_error", "The EPUB worker returned an invalid event."
                    )
                    self._stop_process(job.process)
                    break
                event_type = event["type"]
                if event_type == "worker_result":
                    if result is not None or event.get("outcome") not in (
                        "succeeded",
                        "failed",
                        "cancelled",
                    ):
                        monitor_error = JobError(
                            "worker_protocol_error", "The EPUB worker returned an invalid result."
                        )
                        self._stop_process(job.process)
                        break
                    result = event
                    continue
                if result is not None or event_type not in ("progress", "diagnostic"):
                    monitor_error = JobError(
                        "worker_protocol_error", "The EPUB worker returned an invalid event."
                    )
                    self._stop_process(job.process)
                    break
                forwarded = {"protocol": PROTOCOL, "job_id": job.job_id, **event}
                try:
                    _encode(forwarded)
                except JobError as error:
                    monitor_error = error
                    self._stop_process(job.process)
                    break
                self.events.put(forwarded)
        except BaseException:
            monitor_error = JobError(
                "worker_protocol_error", "The EPUB worker output could not be read."
            )
            self._stop_process(job.process)
        finally:
            stdout.close()
        return_code = job.process.wait()

        with job.guard:
            if job.finished:
                return
            try:
                if job.cancel_requested or (
                    result is not None and result.get("outcome") == "cancelled"
                ):
                    cleanup_ok = _remove_stage(job)
                    if cleanup_ok:
                        terminal = {
                            "protocol": PROTOCOL,
                            "type": "finished",
                            "job_id": job.job_id,
                            "outcome": "cancelled",
                            "outputs": [],
                        }
                    else:
                        terminal = self._cleanup_failure(job)
                elif (
                    monitor_error is None
                    and result is not None
                    and result.get("outcome") == "succeeded"
                    and return_code == 0
                ):
                    try:
                        _publish_stage(job)
                    except JobError as error:
                        if _remove_stage(job):
                            terminal = self._failed(job, error.code, error.message)
                        else:
                            terminal = self._cleanup_failure(job)
                    else:
                        terminal = {
                            "protocol": PROTOCOL,
                            "type": "finished",
                            "job_id": job.job_id,
                            "outcome": "succeeded",
                            "outputs": [{"kind": "tf_bundle", "path": str(job.output)}],
                        }
                else:
                    failure = monitor_error
                    if failure is None:
                        code = result.get("code") if result else None
                        message = result.get("message") if result else None
                        failure = JobError(
                            code if isinstance(code, str) else "conversion_failed",
                            message
                            if isinstance(message, str)
                            else "The EPUB conversion did not complete.",
                        )
                    if _remove_stage(job):
                        terminal = self._failed(job, failure.code, failure.message)
                    else:
                        terminal = self._cleanup_failure(job)
            except BaseException:
                terminal = (
                    self._cleanup_failure(job)
                    if not _remove_stage(job)
                    else self._failed(
                        job,
                        "job_finalization_failed",
                        "The EPUB job could not be finalized safely.",
                    )
                )
            finally:
                job.finished = True
                for descriptor in (job.lock_fd, job.parent_fd):
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            self.events.put(terminal)

    @staticmethod
    def _failed(job: Job, code: str, message: str) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL,
            "type": "finished",
            "job_id": job.job_id,
            "outcome": "failed",
            "outputs": [],
            "error": {"code": code, "message": message},
        }

    @staticmethod
    def _cleanup_failure(job: Job) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL,
            "type": "finished",
            "job_id": job.job_id,
            "outcome": "failed",
            "outputs": [],
            "error": {
                "code": "cleanup_incomplete",
                "message": "The job staging directory could not be removed safely.",
                "staging_path": str(job.stage_path),
            },
        }


def _error(request_id: str, code: str, message: str) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "type": "error",
        "request_id": request_id,
        "code": code,
        "message": message,
    }


def _protocol_error(code: str, message: str) -> dict[str, Any]:
    return {"protocol": PROTOCOL, "type": "protocol_error", "code": code, "message": message}


def _silence_closed_output(stream: BinaryIO) -> None:
    """Keep Python's shutdown flush from retrying a failed protocol pipe."""
    try:
        stream_fd = stream.fileno()
        null_fd = os.open(os.devnull, os.O_WRONLY)
    except (AttributeError, OSError, ValueError):
        return
    try:
        os.dup2(null_fd, stream_fd)
    except OSError:
        pass
    finally:
        os.close(null_fd)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("invalid JSON constant")


def _validate_request_id(value: object, seen: set[str]) -> str:
    if not isinstance(value, str) or REQUEST_ID.fullmatch(value) is None:
        raise ProtocolViolationError("invalid_request_id", "A valid unique request_id is required.")
    if value in seen:
        raise ProtocolViolationError("duplicate_request_id", "request_id values cannot be reused.")
    seen.add(value)
    return value


def _check_fields(message: dict[str, Any], allowed: set[str], request_id: str) -> None:
    del request_id
    if set(message) - ({"protocol", "type", "request_id"} | allowed):
        raise JobError("unknown_field", "The request contains an unknown field.")


def _handle_request(
    message: object,
    seen: set[str],
    manager: EpubJobManager,
    write: Any,
) -> bool:
    if not isinstance(message, dict):
        raise ProtocolViolationError("invalid_request", "A request must be a JSON object.")
    if type(message.get("protocol")) is not int or message.get("protocol") != PROTOCOL:
        raise ProtocolViolationError("incompatible_protocol", "Protocol version 1 is required.")
    request_id = _validate_request_id(message.get("request_id"), seen)
    message_type = message.get("type")
    if message_type == "start":
        try:
            _check_fields(
                message,
                {"operation", "input", "output", "options"},
                request_id,
            )
            if message.get("operation") != "epub_to_tf":
                raise JobError(
                    "unsupported_operation", "This sidecar does not support that operation."
                )
            source = message.get("input")
            output = message.get("output")
            options = message.get("options", {})
            if not isinstance(source, dict) or set(source) != {"path"}:
                raise JobError(
                    "invalid_input", "Input must contain only an absolute local file path."
                )
            if not isinstance(output, dict) or set(output) != {"path"}:
                raise JobError(
                    "invalid_output", "Output must contain only an absolute local directory path."
                )
            if not isinstance(options, dict) or set(options) - {"repair", "max_slots"}:
                raise JobError(
                    "invalid_options", "The request contains unsupported conversion options."
                )
            repair = options.get("repair", False)
            if type(repair) is not bool:
                raise JobError("invalid_options", "The repair option must be a boolean.")
            max_slots = options.get("max_slots", MAX_SLOTS)
            if type(max_slots) is not int or not 1 <= max_slots <= MAX_SLOTS:
                raise JobError(
                    "invalid_options",
                    f"The max_slots option must be an integer from 1 to {MAX_SLOTS}.",
                )
            accepted = manager.start(
                request_id, source.get("path"), output.get("path"), repair, max_slots
            )
        except JobError as error:
            write(_error(request_id, error.code, error.message))
            return False
        except OSError:
            write(
                _error(request_id, "job_start_failed", "The EPUB job could not be started safely.")
            )
            return False
        write(
            {
                "protocol": PROTOCOL,
                "type": "accepted",
                "request_id": request_id,
                **accepted,
            }
        )
        return False
    if message_type == "cancel":
        try:
            _check_fields(message, {"job_id"}, request_id)
            job_id = message.get("job_id")
            if not isinstance(job_id, str) or not job_id:
                raise JobError("invalid_job_id", "A job_id is required.")
            if not manager.cancel(job_id):
                write(_error(request_id, "job_not_running", "The job is not running."))
                return False
        except JobError as error:
            write(_error(request_id, error.code, error.message))
            return False
        write(
            {
                "protocol": PROTOCOL,
                "type": "cancel_ack",
                "request_id": request_id,
                "job_id": job_id,
                "status": "requested",
            }
        )
        return False
    try:
        _check_fields(message, set(), request_id)
    except JobError as error:
        write(_error(request_id, error.code, error.message))
        return False
    write(_error(request_id, "unknown_message_type", "The request type is not supported."))
    return False


def serve(
    stdin: BinaryIO | None = None,
    stdout: BinaryIO | None = None,
    manager: EpubJobManager | None = None,
) -> int:
    """Serve newline-delimited requests; stdout contains protocol messages only."""
    input_stream = stdin or sys.stdin.buffer
    output_stream = stdout or sys.stdout.buffer
    jobs = manager or EpubJobManager()
    input_fd = input_stream.fileno()
    selector = selectors.DefaultSelector()
    selector.register(input_fd, selectors.EVENT_READ)
    used_request_ids: set[str] = set()
    pending = bytearray()
    input_open = True
    close_after_drain = False

    def write(message: dict[str, Any]) -> None:
        frame = _encode(message)
        output_stream.write(frame)
        output_stream.flush()

    return_code = 0
    try:
        write({"protocol": PROTOCOL, "type": "ready", "capabilities": jobs.capabilities})
        while True:
            while True:
                try:
                    event = jobs.events.get_nowait()
                except queue.Empty:
                    break
                write(event)

            if input_open:
                for _key, _mask in selector.select(timeout=0.05):
                    chunk = os.read(input_fd, 64 * 1024)
                    if not chunk:
                        input_open = False
                        if pending:
                            write(
                                _protocol_error(
                                    "invalid_frame", "A request frame must end with a newline."
                                )
                            )
                            close_after_drain = True
                        jobs.cancel_all()
                        break
                    pending.extend(chunk)
                    while True:
                        newline = pending.find(b"\n")
                        if newline < 0:
                            if len(pending) > MAX_FRAME_BYTES:
                                write(
                                    _protocol_error(
                                        "invalid_frame", "The request frame exceeds 1 MiB."
                                    )
                                )
                                pending.clear()
                                input_open = False
                                close_after_drain = True
                                jobs.cancel_all()
                            break
                        frame_length = newline + 1
                        if frame_length > MAX_FRAME_BYTES:
                            write(
                                _protocol_error("invalid_frame", "The request frame exceeds 1 MiB.")
                            )
                            pending.clear()
                            input_open = False
                            close_after_drain = True
                            jobs.cancel_all()
                            break
                        raw = bytes(pending[:newline])
                        del pending[:frame_length]
                        try:
                            message = json.loads(
                                raw,
                                object_pairs_hook=_unique_object,
                                parse_constant=_reject_json_constant,
                            )
                        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                            write(
                                _protocol_error(
                                    "invalid_json", "The request is not valid UTF-8 JSON."
                                )
                            )
                            input_open = False
                            close_after_drain = True
                            jobs.cancel_all()
                            break
                        try:
                            _handle_request(message, used_request_ids, jobs, write)
                        except ProtocolViolationError as error:
                            write(_protocol_error(error.code, error.message))
                            input_open = False
                            close_after_drain = True
                            jobs.cancel_all()
                            break
                        except JobError as error:
                            write(_protocol_error(error.code, error.message))
                            input_open = False
                            close_after_drain = True
                            jobs.cancel_all()
                            break
                    if not input_open:
                        break

            if not input_open and jobs.active_count() == 0:
                while True:
                    try:
                        event = jobs.events.get_nowait()
                    except queue.Empty:
                        break
                    write(event)
                break
            if close_after_drain:
                jobs.cancel_all()
    except KeyboardInterrupt:
        return_code = 130
    except OSError:
        # A closed client pipe (or stdin transport failure) must not orphan
        # workers whose process groups outlive this sidecar.
        return_code = 1
        _silence_closed_output(output_stream)
    finally:
        if jobs.active_count():
            jobs.cancel_all()
            while jobs.active_count():
                try:
                    event = jobs.events.get(timeout=0.05)
                except queue.Empty:
                    continue
                if return_code == 130:
                    try:
                        write(event)
                    except OSError:
                        return_code = 1
                        _silence_closed_output(output_stream)
        selector.close()
    return return_code
