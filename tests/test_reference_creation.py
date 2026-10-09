import json

import pytest

from corpora_cli import cli


def command(args):
    try:
        return cli.main(args)
    except SystemExit as exc:
        return exc.code


def snapshot(tmp_path, name, text):
    from corpora_linking import Endpoint, TextSnapshot

    value = TextSnapshot(
        endpoint=Endpoint(
            work_id=name, edition_id="e", package_id=name, revision="r1", document_id="d"
        ),
        stream_id="body",
        text=text,
    )
    path = tmp_path / f"{name}-snapshot.json"
    path.write_text(value.model_dump_json())
    return path


def selected(tmp_path, snapshot_path, name, quote):
    path = tmp_path / f"{name}.json"
    assert (
        command(
            [
                "references",
                "select",
                "--snapshot",
                str(snapshot_path),
                "--quote",
                quote,
                "-o",
                str(path),
            ]
        )
        == 0
    )
    return path


def test_create_cross_work_link_check_and_retrieve(tmp_path, capsys):
    source_snapshot = snapshot(tmp_path, "notes", "😀 Before. My selection. After.")
    target_snapshot = snapshot(tmp_path, "book", "A separate work has a linked passage.")
    source = selected(tmp_path, source_snapshot, "source", "My selection.")
    target = selected(tmp_path, target_snapshot, "target", "linked passage.")
    output = tmp_path / "reference.json"
    assert (
        command(
            [
                "references",
                "create",
                "--source",
                str(source),
                "--target",
                str(target),
                "--source-snapshot",
                str(source_snapshot),
                "--target-snapshot",
                str(target_snapshot),
                "--creator",
                "reader",
                "-o",
                str(output),
            ]
        )
        == 0
    )
    reference = json.loads(output.read_text())
    assert reference["source"]["work_id"] != reference["target"]["work_id"]
    assert reference["resolution"] == "resolved"
    assert reference["review"] == "pending" and reference["publication"] == "draft"
    assert reference["provenance"]["origin"] == "manual"
    assert json.loads(source.read_text())["locators"][0]["start"] == 10
    capsys.readouterr()
    assert command(["references", "check", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == reference["id"]
    assert command(["references", "retrieve", str(target), "--snapshot", str(target_snapshot)]) == 0
    assert json.loads(capsys.readouterr().out) == {"text": "linked passage."}


def test_repeated_quote_needs_explicit_bounds(tmp_path, capsys):
    path = snapshot(tmp_path, "book", "😀 repeat repeat")
    output = tmp_path / "selected.json"
    assert (
        command(
            [
                "references",
                "select",
                "--snapshot",
                str(path),
                "--quote",
                "repeat",
                "-o",
                str(output),
            ]
        )
        == 1
    )
    assert not output.exists() and not capsys.readouterr().out
    assert (
        command(
            [
                "references",
                "select",
                "--snapshot",
                str(path),
                "--quote",
                "repeat",
                "--start",
                "9",
                "--end",
                "15",
                "-o",
                str(output),
            ]
        )
        == 0
    )
    assert json.loads(output.read_text())["locators"][0]["start"] == 9


def test_stale_target_does_not_create_reference(tmp_path, capsys):
    path = snapshot(tmp_path, "book", "Selected words.")
    endpoint = selected(tmp_path, path, "target", "Selected words.")
    changed = json.loads(path.read_text())
    changed["endpoint"]["revision"] = "r2"
    stale = tmp_path / "stale.json"
    stale.write_text(json.dumps(changed))
    output = tmp_path / "reference.json"
    capsys.readouterr()
    assert (
        command(
            [
                "references",
                "create",
                "--source",
                str(endpoint),
                "--target",
                str(endpoint),
                "--source-snapshot",
                str(path),
                "--target-snapshot",
                str(stale),
                "--creator",
                "reader",
                "-o",
                str(output),
            ]
        )
        == 1
    )
    assert not output.exists() and not capsys.readouterr().out


def test_work_target_preserved_unresolved(tmp_path, capsys):
    path = snapshot(tmp_path, "notes", "Selected sentence.")
    source = selected(tmp_path, path, "source", "Selected sentence.")
    target = tmp_path / "work.json"
    target.write_text('{"work_id":"another-work"}')
    capsys.readouterr()
    assert (
        command(
            [
                "references",
                "create",
                "--source",
                str(source),
                "--target",
                str(target),
                "--source-snapshot",
                str(path),
                "--creator",
                "reader",
            ]
        )
        == 0
    )
    reference = json.loads(capsys.readouterr().out)
    assert reference["target"]["work_id"] == "another-work"
    assert reference["target"]["locators"] == []
    assert reference["resolution"] == "unresolved"


def test_existing_reference_is_never_overwritten(tmp_path):
    path = snapshot(tmp_path, "book", "Selected words.")
    endpoint = selected(tmp_path, path, "selected", "Selected words.")
    output = tmp_path / "reference.json"
    output.write_text("existing stable reference")
    assert (
        command(
            [
                "references",
                "create",
                "--source",
                str(endpoint),
                "--target",
                str(endpoint),
                "--source-snapshot",
                str(path),
                "--target-snapshot",
                str(path),
                "--creator",
                "reader",
                "-o",
                str(output),
            ]
        )
        == 2
    )
    assert output.read_text() == "existing stable reference"


@pytest.mark.parametrize("bounds", [["--start", "0"], ["--start", "1", "--end", "999"]])
def test_invalid_bounds_rejected(tmp_path, bounds):
    path = snapshot(tmp_path, "book", "Text.")
    assert command(["references", "select", "--snapshot", str(path), *bounds]) == 2
