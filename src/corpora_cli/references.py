"""Offline reference validation/retrieval; no database or publication authority."""

from pathlib import Path
from typing import Annotated

import typer

from corpora_cli import ui

app = typer.Typer(
    name="references",
    help="Select text, create a manual link, check JSON or retrieve an exact passage offline.",
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


def _write(value, output: Path | None):
    payload = value.model_dump_json()
    if output is None:
        typer.echo(payload)
        return
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            stream.write(payload + "\n")
    except OSError as exc:
        raise ui.fail(f"cannot create {output}; existing files are never overwritten") from exc


def _verify(core, selection, text):
    if len(selection.locators) != 1 or not isinstance(selection.locators[0], core.TextLocator):
        raise ui.fail("offline verification requires exactly one text locator")
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


@app.command("select")
def select(
    snapshot: Annotated[Path, typer.Option("--snapshot", help="Trusted full text snapshot JSON.")],
    quote: Annotated[
        str | None, typer.Option("--quote", help="Exact quote; must be unique without bounds.")
    ] = None,
    start: Annotated[
        int | None, typer.Option("--start", help="Zero-based Unicode-scalar start offset.")
    ] = None,
    end: Annotated[
        int | None, typer.Option("--end", help="Exclusive Unicode-scalar end offset.")
    ] = None,
    output: Annotated[
        Path | None, typer.Option("-o", "--output", help="New endpoint JSON file; default stdout.")
    ] = None,
):
    """Select an exact passage in a pinned stream; ambiguous quotes fail."""
    core = _core()
    try:
        value = core.TextSnapshot.model_validate_json(_read(snapshot))
    except ValueError as exc:
        raise ui.fail("invalid snapshot JSON") from exc
    if (start is None) != (end is None):
        raise ui.fail("provide both --start and --end")
    if start is None:
        if not quote:
            raise ui.fail("provide a nonempty --quote or explicit bounds")
        start = value.text.find(quote)
        if start < 0 or value.text.find(quote, start + 1) >= 0:
            ui.log("Quote is missing or ambiguous. Supply exact --start and --end bounds.")
            raise typer.Exit(1)
        end = start + len(quote)
    assert end is not None
    if not 0 <= start < end <= len(value.text):
        raise ui.fail("selection bounds must identify a nonempty range within the stream")
    exact = value.text[start:end]
    if quote is not None and quote != exact:
        ui.log("Quote does not match the selected bounds. Nothing was relocated.")
        raise typer.Exit(1)
    locator = core.TextLocator(
        stream_id=value.stream_id,
        start=start,
        end=end,
        exact=exact,
        prefix=value.text[max(0, start - 24) : start],
        suffix=value.text[end : end + 24],
        normalization="preserve",
    )
    endpoint = core.Endpoint.model_validate({**value.endpoint.model_dump(), "locators": [locator]})
    _verify(core, endpoint, value)
    _write(endpoint, output)


@app.command("create")
def create(
    source: Annotated[Path, typer.Option("--source", help="Selected source endpoint JSON.")],
    target: Annotated[
        Path,
        typer.Option("--target", help="Target endpoint JSON; unknown targets remain unresolved."),
    ],
    source_snapshot: Annotated[
        Path, typer.Option("--source-snapshot", help="Trusted source stream snapshot.")
    ],
    creator: Annotated[
        str, typer.Option("--creator", help="Local creator attribution; not authenticated review.")
    ],
    target_snapshot: Annotated[
        Path | None, typer.Option("--target-snapshot", help="Required to verify a text target.")
    ] = None,
    relationship: Annotated[
        str, typer.Option("--relationship", help="Relationship type.")
    ] = "core:related",
    output: Annotated[
        Path | None, typer.Option("-o", "--output", help="New reference JSON file; default stdout.")
    ] = None,
):
    """Create a stable-ID manual link; review stays pending and publication draft."""
    core = _core()
    try:
        origin = core.Endpoint.model_validate_json(_read(source))
        destination = core.Endpoint.model_validate_json(_read(target))
        origin_text = core.TextSnapshot.model_validate_json(_read(source_snapshot))
        destination_text = (
            core.TextSnapshot.model_validate_json(_read(target_snapshot))
            if target_snapshot is not None
            else None
        )
    except ValueError as exc:
        raise ui.fail("invalid endpoint or snapshot JSON") from exc
    _verify(core, origin, origin_text)
    resolution = "unresolved"
    if any(isinstance(locator, core.TextLocator) for locator in destination.locators):
        if destination_text is None:
            raise ui.fail("a text target requires --target-snapshot")
        _verify(core, destination, destination_text)
        resolution = "resolved"
    elif destination_text is not None:
        raise ui.fail("offline target snapshot verification requires a text selection")
    try:
        reference = core.Reference(
            source=origin,
            target=destination,
            relationship=relationship,
            provenance=core.Provenance(
                origin="manual", agent_id=creator, method="cli:manual-link/v1"
            ),
            resolution=resolution,
        )
    except ValueError as exc:
        raise ui.fail("invalid creator or relationship") from exc
    if resolution == "unresolved":
        ui.warn("Target retained as unresolved; exact passage or work identity is unverified.")
    _write(reference, output)


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
    _verify(core, selection, text)
    locator = selection.locators[0]
    # The resolver requires stored text to already match its normalization policy.
    typer.echo(json.dumps({"text": text.text[locator.start : locator.end]}, ensure_ascii=False))
