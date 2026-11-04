"""Opt-in converter selection; orchestration stays in corpora-py."""

from enum import StrEnum
from functools import partial
from inspect import signature

from admin.parsers.schema import SourceFormat

from corpora_cli import ui


class Backend(StrEnum):
    PYTHON = "python"
    NATIVE = "native-experimental"


def select_converters(backend: Backend, source_format: SourceFormat):
    if backend == Backend.PYTHON:
        return None
    if source_format != SourceFormat.TEI:
        ui.warn(
            "Native TEI capability fallback: this format uses Python (TEI XML needs --format tei)."
        )
        return None
    from admin.converters import CONVERTERS

    converter = CONVERTERS[SourceFormat.TEI]
    if "backend" not in signature(converter).parameters:
        raise ui.fail(
            "This corpora-py installation does not support native TEI selection. "
            "Install a compatible corpora-py build or use --backend python."
        )
    return {**CONVERTERS, SourceFormat.TEI: partial(converter, backend=backend.value)}
