import json

import pytest

from corpora_cli import cli


def fixture(tmp_path):
    from corpora_linking import Endpoint, Provenance, Reference, TextLocator, TextSnapshot

    base = Endpoint(
        work_id="book", edition_id="edition", package_id="pkg", revision="rev1", document_id="d"
    )
    text = "😀 Before. Linked words. After."
    start = text.index("Linked words")
    selected = base.model_copy(
        update={
            "locators": (
                TextLocator(
                    stream_id="body",
                    start=start,
                    end=start + 12,
                    exact="Linked words",
                    prefix="Before. ",
                    suffix=". After.",
                ),
            )
        }
    )
    snapshot = TextSnapshot(endpoint=base, stream_id="body", text=text)
    reference = Reference(
        source=selected,
        target=selected,
        provenance=Provenance(origin="manual", agent_id="local-reader", method="selection"),
    )
    paths = {}
    for name, model in (("endpoint", selected), ("snapshot", snapshot), ("reference", reference)):
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(model.model_dump_json())
    return paths, reference


def test_check_preserves_identity_without_claiming_resolution(tmp_path, capsys):
    paths, reference = fixture(tmp_path)
    assert cli.main(["references", "check", str(paths["reference"])]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["id"] == str(reference.id)
    assert result["resolution"] == "unresolved" and result["review"] == "pending"


def test_retrieve_unicode_exact_quote(tmp_path, capsys):
    paths, _ = fixture(tmp_path)
    assert (
        cli.main(
            ["references", "retrieve", str(paths["endpoint"]), "--snapshot", str(paths["snapshot"])]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {"text": "Linked words"}


@pytest.mark.parametrize(
    "change",
    [
        {"revision": "rev2"},
        {"work_id": "other"},
        {"quote": "Wrong words!"},
        {"prefix": "wrong context"},
    ],
)
def test_stale_selection_never_relocates(tmp_path, capsys, change):
    paths, _ = fixture(tmp_path)
    data = json.loads(paths["endpoint"].read_text())
    for key, value in change.items():
        if key in ("quote", "prefix"):
            data["locators"][0]["exact" if key == "quote" else key] = value
        else:
            data[key] = value
    paths["endpoint"].write_text(json.dumps(data))
    assert (
        cli.main(
            ["references", "retrieve", str(paths["endpoint"]), "--snapshot", str(paths["snapshot"])]
        )
        == 1
    )
    assert capsys.readouterr().out == ""


def test_bad_json_is_usage_error(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"id": "wrong"}')
    with pytest.raises(SystemExit) as exc:
        cli.main(["references", "check", str(path)])
    assert exc.value.code == 2


def test_missing_core_has_actionable_error(monkeypatch, tmp_path, capsys):
    import sys

    monkeypatch.setitem(sys.modules, "corpora_linking", None)
    with pytest.raises(SystemExit) as exc:
        cli.main(["references", "check", str(tmp_path / "missing.json")])
    assert exc.value.code == 2
    assert "Reinstall corpora-cli" in capsys.readouterr().err


def test_help_does_not_require_core(monkeypatch, capsys):
    import sys

    monkeypatch.setitem(sys.modules, "corpora_linking", None)
    assert cli.main(["references", "--help"]) == 0
    assert "retrieve" in capsys.readouterr().out
