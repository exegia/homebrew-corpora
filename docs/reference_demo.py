"""Create a manual link using the released core; synthetic, offline evidence only."""

import argparse
import hashlib
from pathlib import Path

from corpora_linking import (
    CatalogEntry,
    Endpoint,
    Provenance,
    Reference,
    SnapshotCatalog,
    SnapshotResolver,
    TextLocator,
    TextSnapshot,
)


def snapshot(work: str, text: str) -> TextSnapshot:
    return TextSnapshot(
        endpoint=Endpoint(
            work_id=work,
            edition_id="demo-edition",
            package_id="demo-package",
            revision="sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest(),
            document_id="body.txt",
        ),
        stream_id="body",
        text=text,
    )


def select(value: TextSnapshot, quote: str) -> Endpoint:
    # A quotation alone is insufficient when it occurs more than once.
    first = value.text.find(quote)
    if not quote or first < 0 or value.text.find(quote, first + 1) >= 0:
        raise ValueError("quote is missing or ambiguous; supply explicit selection bounds")
    last = first + len(quote)
    locator = TextLocator(
        stream_id=value.stream_id,
        start=first,
        end=last,
        exact=quote,
        prefix=value.text[max(0, first - 24) : first],
        suffix=value.text[last : last + 24],
    )
    return Endpoint.model_validate({**value.endpoint.model_dump(), "locators": [locator]})


def create_demo(directory: Path) -> Reference:
    source = snapshot("reader-notes", "My note: Love your neighbor. This is my selection.")
    target = snapshot("demo-book", "Before. 😀 Love your neighbor as yourself. After.")
    source_selection = select(source, "Love your neighbor.")
    target_selection = select(target, "Love your neighbor as yourself.")
    resolver = SnapshotResolver(
        catalog=SnapshotCatalog(
            entries=tuple(
                CatalogEntry(work_id=value.endpoint.work_id, names=(value.endpoint.work_id,))
                for value in (source, target)
            )
        ),
        snapshots=(source, target),
    )
    for endpoint in (source_selection, target_selection):
        result = resolver.resolve(endpoint)
        if result.status != "resolved" or result.candidates != (endpoint,):
            raise ValueError("selection could not be verified")
    reference = Reference(
        source=source_selection,
        target=target_selection,
        relationship="core:related",
        provenance=Provenance(origin="manual", agent_id="demo-reader", method="exact-selection"),
        resolution="resolved",
    )
    values = {
        "source-snapshot": source,
        "source": source_selection,
        "target-snapshot": target,
        "target": target_selection,
        "reference": reference,
        "stale-snapshot": snapshot("demo-book", target.text + " Revised."),
    }
    directory.mkdir(parents=True, exist_ok=True)
    # Refuse to replace an existing reference and its stable ID.
    if any((directory / f"{name}.json").exists() for name in values):
        raise ValueError("demo files already exist; choose a new output directory")
    for name, value in values.items():
        (directory / f"{name}.json").write_text(value.model_dump_json(indent=2), encoding="utf-8")
    return reference


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    create_demo(args.directory)
    print("Created a manual link between two verified text selections.")
    print("Resolution: resolved. Review: pending. Publication: draft.")
