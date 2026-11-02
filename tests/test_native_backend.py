"""CLI contract tests, plus acceptance against an installed native wheel."""

import inspect
import json
import zipfile
from unittest.mock import patch

import pytest
from admin.converters import CONVERTERS
from admin.parsers.schema import SourceFormat

from corpora_cli import cli
from corpora_cli.backend import Backend, select_converters

SOURCE = (
    '<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><fileDesc>'
    "<titleStmt><title>Native</title></titleStmt></fileDesc></teiHeader>"
    "<text><body><p>Hello <hi>world</hi>.</p></body></text></TEI>"
)


def test_default_does_not_replace_registry():
    assert select_converters(Backend.PYTHON, SourceFormat.TEI) is None


def test_unsupported_format_warns(capsys):
    assert select_converters(Backend.NATIVE, SourceFormat.PLAIN) is None
    assert "capability fallback" in capsys.readouterr().err


def test_old_python_build_is_actionable(monkeypatch, capsys):
    monkeypatch.setitem(CONVERTERS, SourceFormat.TEI, lambda source, output: None)
    with pytest.raises(SystemExit) as caught:
        select_converters(Backend.NATIVE, SourceFormat.TEI)
    assert caught.value.code == 2
    assert "compatible corpora-py build" in capsys.readouterr().err


@pytest.fixture
def native():
    module = pytest.importorskip("corpora_native")
    from importlib.metadata import version

    if version("context-fabric") != "0.6.0rc1":
        pytest.skip("Native acceptance requires the verified CF 0.6 runtime")
    if "backend" not in inspect.signature(CONVERTERS[SourceFormat.TEI]).parameters:
        pytest.skip("Native acceptance requires merged corpora-py source")
    return module


def invoke(tmp_path, source=SOURCE, category="document"):
    path = tmp_path / "input.tei"
    path.write_text(source, encoding="utf-8")
    output = tmp_path / "out.corpus"
    result = cli.main(
        [
            "convert",
            str(path),
            "--backend",
            "native-experimental",
            "--category",
            category,
            "-o",
            str(output),
        ]
    )
    return result, output


def test_native_executes_and_packages_exact_engine_output(native, tmp_path, capsys):
    expected = native.convert_bytes(
        SOURCE.encode(),
        locator=str(tmp_path / "input.tei"),
        include_tf=True,
        tf_projection="annotated",
        profile="corpora-source/v1",
    )
    with patch.object(native, "convert_bytes", wraps=native.convert_bytes) as called:
        with patch(
            "admin.converters._tei_to_tf.convert_document",
            side_effect=AssertionError("silent Python fallback"),
        ):
            result, output = invoke(tmp_path)
    assert result == 0
    called.assert_called_once()
    captured = capsys.readouterr()
    assert captured.out.strip() == str(output)
    with zipfile.ZipFile(output) as archive:
        member = next(n for n in archive.namelist() if n.endswith("corpora-annotated.json"))
        document = json.loads(archive.read(member))
        assert document["corpus"]["documents"] == expected["document"]["corpus"]["documents"]
    assert cli.main(["validate", str(output)]) == 0


def test_semantic_error_does_not_fallback(native, tmp_path, capsys):
    with patch(
        "admin.converters._tei_to_tf.convert_document",
        side_effect=AssertionError("semantic fallback"),
    ):
        result, output = invoke(
            tmp_path,
            SOURCE.replace("<hi>world</hi>", "<choice><orig>world</orig><reg>Earth</reg></choice>"),
        )
    assert result == 1
    assert not output.exists()
    captured = capsys.readouterr()
    assert not captured.out
    assert "reading policy" in captured.err


def test_missing_wheel_explicit_fallback(native, tmp_path, capsys):
    missing = ModuleNotFoundError("missing corpora_native", name="corpora_native")
    with patch("admin.converters._native_tei.importlib.import_module", side_effect=missing):
        result, output = invoke(tmp_path)
    assert result == 0 and output.exists()
    assert "wheel is not installed" in capsys.readouterr().err


def test_unsupported_category_explicit_fallback(native, tmp_path, capsys):
    with patch.object(native, "convert_bytes", side_effect=AssertionError("unsupported native")):
        result, output = invoke(tmp_path, category="book")
    assert result == 0 and output.exists()
    assert "capability fallback" in capsys.readouterr().err
