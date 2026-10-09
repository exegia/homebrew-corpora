"""Offline reference validation/retrieval; no database or publication authority."""

from pathlib import Path
from typing import Annotated

import typer

from corpora_cli import ui

app = typer.Typer(
    name="references",
    help="Check reference JSON or retrieve a pinned text selection offline.",
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _core():
    try:
        import corpora_linking
    except ImportError as exc:
        raise ui.fail(
            "The reference-linking dependency is missing. "
            "Reinstall corpora-cli or install corpora-linking>=0.1.0,<0.2."
        ) from exc
    return corpora_linking


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ui.fail(f"cannot read input: {path}") from exc


@app.command("check")
def check(reference: Annotated[Path, typer.Argument(help="Reference JSON file.")]):
    """Validate fields and lifecycle invariants; print unchanged reference JSON.

    This checks the model, not whether a target exists or a reviewer approved it.
    """
    core = _core()
    try:
        value = core.Reference.model_validate_json(_read(reference))
    except ValueError as exc:
        raise ui.fail("invalid reference JSON or lifecycle fields") from exc
    typer.echo(value.model_dump_json())


@app.command("retrieve")
def retrieve(
    endpoint: Annotated[Path, typer.Argument(help="Exact text endpoint JSON file.")],
    snapshot: Annotated[Path, typer.Option("--snapshot", help="Trusted full text snapshot JSON.")],
):
    """Verify pinned identity, offsets, quote and context; print selected text JSON."""
    import json

    core = _core()
    try:
        selection = core.Endpoint.model_validate_json(_read(endpoint))
        text = core.TextSnapshot.model_validate_json(_read(snapshot))
    except ValueError as exc:
        raise ui.fail("invalid endpoint or snapshot JSON") from exc
    if len(selection.locators) != 1 or not isinstance(selection.locators[0], core.TextLocator):
        raise ui.fail("offline retrieval requires exactly one text locator")
    resolver = core.SnapshotResolver(
        catalog=core.SnapshotCatalog(
            entries=(
                core.CatalogEntry(work_id=text.endpoint.work_id, names=(text.endpoint.work_id,)),
            )
        ),
        snapshots=(text,),
    )
    result = resolver.resolve(selection)
    if result.status != "resolved" or result.candidates != (selection,):
        ui.log("Selection is stale, ambiguous or unavailable. Nothing was relocated.")
        raise typer.Exit(1)
    locator = selection.locators[0]
    # The resolver requires stored text to already match its normalization policy.
    typer.echo(json.dumps({"text": text.text[locator.start : locator.end]}, ensure_ascii=False))
