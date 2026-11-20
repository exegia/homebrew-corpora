"""Discovery for the two ``corpora-core`` EPUB executables."""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

# corpora-core's HARD_MAX_SLOTS: the largest character-slot bound its tools accept.
# The sidecar runs on the user's machine, so it uses the full bound by default.
MAX_SLOTS = 8_000_000


@dataclass(frozen=True)
class EpubRuntime:
    checker: Path
    converter: Path


def _atomic_publish_supported() -> bool:
    libc = ctypes.CDLL(None)
    if sys.platform == "darwin":
        return hasattr(libc, "renameatx_np")
    if sys.platform.startswith("linux"):
        return hasattr(libc, "renameat2")
    return False


def _executable(env_name: str, command_name: str) -> Path | None:
    configured = os.environ.get(env_name)
    candidate = Path(configured).expanduser() if configured else None
    if candidate is None:
        discovered = shutil.which(command_name)
        candidate = Path(discovered) if discovered else None
    if candidate is None:
        return None
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        return None
    return resolved


def discover_epub_runtime() -> EpubRuntime | None:
    """Return tools only when both the core validator and exporter can run."""
    if os.name != "posix" or not _atomic_publish_supported():
        return None
    checker = _executable("CORPORA_EPUB_CHECK_BIN", "corpora-epub-check")
    converter = _executable("CORPORA_EPUB_TO_TF_BIN", "epub-to-tf")
    if checker is None or converter is None:
        return None
    return EpubRuntime(checker=checker, converter=converter)
