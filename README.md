<p align="center">
  <img src="docs/git-banner.png" alt="corpora logo">
</p>

# corpora/cli

Prepare texts for **C-USX (Corpora’s extended USX)** and link exact passages
across works. C-USX is the direction for document interchange; reference linking
is available today. The released converter still produces `.corpus` archives.

![GitHub Release](https://img.shields.io/github/v/release/exegia/homebrew-corpora?sort=semver&display_name=tag&style=for-the-badge&color=%23d2a24c)
![GitHub Actions Workflow Status](https://img.shields.io/github/actions/workflow/status/exegia/homebrew-corpora/release.yml?style=for-the-badge)
![Static Badge](https://img.shields.io/badge/license-MIT-black?style=for-the-badge)
![Static Badge](https://img.shields.io/badge/homebrew-exegia%2Fcorpora%2Fcli-black?style=for-the-badge)

![Create a manual link with Python, retrieve its exact passage with the CLI, and reject a stale revision](docs/corpora-references.gif)

`corpora` is the terminal front end for
[corpora-py](https://github.com/exegia/corpora-py). Its reference commands use
[corpora-linking](https://github.com/exegia/corpora-linking), the independent,
open-source Python package. Both automatic citations and reader-created links
use the same version-aware reference model.

**What works now:** create manual links through the Python package; check their
JSON and retrieve exact text selections through the released CLI. Corpora’s
Python integration can bind anchors and export approved links into supplied
C-USX documents.

**What comes next:** a document-to-C-USX CLI conversion path and a convenient
release of the new `references select` and `references create` commands
implemented on this branch. PDF/EPUB/HTML-to-C-USX conversion is not yet a
released CLI command. Existing `.corpus` and Text-Fabric workflows remain
available; no removal or deprecation deadline has been declared.

## Installation

Install with [Homebrew](https://brew.sh) — one command, no separate tap step:
Homebrew taps [`exegia/corpora`](https://github.com/exegia/homebrew-corpora)
on the way in.

```bash
brew install exegia/corpora/cli
```

Verify the install:

```bash
corpora --help
```

<img src="docs/corpora-help.svg" alt="corpora --help output" width="760">

### Without Homebrew

On machines without Homebrew (Linux, or macOS where you'd rather not use a
formula), install with the one-liner:

1. It builds `corpora-cli` into its own virtualenv under `~/.corpora`.
2. Resolves the `corpora-py` dependency closure from PyPI.
3. Links the same command into `~/.local/bin`.

```bash
curl -fsSL https://raw.githubusercontent.com/exegia/homebrew-corpora/main/install.sh | bash
```

### Pin a release, or remove it later

```bash
curl -fsSL https://raw.githubusercontent.com/exegia/homebrew-corpora/main/install.sh | bash -s -- --version vX.Y.Z
```

```bash
curl -fsSL https://raw.githubusercontent.com/exegia/homebrew-corpora/main/install.sh | bash -s -- --uninstall
```

`python3.13` is required; `uv` is used when present (faster resolver), else
the `venv` module + `pip`. Relocate with `CORPORA_HOME` / `CORPORA_BIN`.

## Start with reference linking

The released Homebrew CLI includes these offline commands:

```bash
corpora references check reference.json
corpora references retrieve target.json --snapshot target-snapshot.json
```

A reference connects a source selection to a target selection or work. It keeps
a stable ID, relationship, creator and exact document versions. Retrieval checks
the selected quote and context at the original Unicode character offsets. If
an anchor is stale or ambiguous, it fails instead of choosing another passage.

This branch also adds `references select` and `references create` for manual
linking from snapshots. They need the next CLI release; `check` and `retrieve`
are already released.

For a complete copy-and-run example that creates a link with Python and retrieves
it through the CLI, see [the reference linking demo](docs/reference-linking.md).
The GIF above shows real command results using synthetic, offline text snapshots.

## C-USX direction

C-USX carries document text plus explicit reference boundaries and links. The
Python integration preserves matching reference IDs when exporting approved
links into C-USX. The database remains authoritative for working references;
resolution, review and publication are separate states.

Creating a local link does **not** approve it or publish it into C-USX. Native
PDF/EPUB/HTML locations and original-to-converted mappings belong to the
conversion/service integration, outside the reusable linking core. See
[the Python integration documentation](https://github.com/exegia/corpora-py/tree/main/specs/reference-linking)
for supported adapters and the opt-in API configuration.

## Existing corpus workflow

The released `convert` command below still builds `.corpus` archives for
Text-Fabric / Context Fabric consumers. Choose this workflow when you need
those consumers; a `.cusx` output filename alone does not change the format.

```text
Commands: convert  validate  schema  reconcile  library  references
```

### Convert

Converts a document to a `.corpus` archive.

```bash
corpora convert book.epub --name "My Book" -o book.corpus
corpora convert dataset.zip --format tf_zip
corpora convert notes.txt          # format inferred from the extension
```

| Argument / option     | Description                                                                                                                                                         |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `source`              | Path to the source document.                                                                                                                                        |
| `-f`, `--format`      | Source format — see the table below. Inferred from the file extension when omitted; **required for `.zip` sources**.                                                |
| `-n`, `--name`        | Corpus name (fallback when the source has no title).                                                                                                                |
| `-d`, `--description` | Corpus description.                                                                                                                                                 |
| `-c`, `--category`    | Corpus structure category: `document`, `book`, or `religious`. Auto-detected when omitted; an upgrade the document tree can't support is downgraded with a warning. |
| `-o`, `--output`      | Output `.corpus` path (default: `./<slugified-title>.corpus`).                                                                                                      |
| `--force`             | Overwrite the output file if it already exists.                                                                                                                     |

#### Supported formats

- **EPUB**: `.epub` e-books.
- **PDF**: `.pdf` documents.
- **HTML**: `.html` / `.htm` pages.
- **XML**: XML (`*.xml`) and TEI-encoded (`*.tei`) texts.
- **Plain**: `.txt` plain text.
- **TF**: Zipped Text-Fabric (`*.tf.zip`) datasets.
- **TEI**: Zipped TEI (`*.tei.zip`) collections.

<img src="docs/corpora-convert-help.svg" alt="corpora convert --help output" width="760">

### Validate

Validates the integrity of a `.corpus` file against the text-fabric requirements.

- "Is the archive internally sound?"
- "Does it faithfully represent the document it was converted from?"
  [#41](https://github.com/exegia/homebrew-corpora/issues/41)

```bash
corpora validate book.corpus
```

Runs the corpus integrity checks over a `.corpus` file and prints a verdict
plus archive stats:

<img src="docs/corpora-validate.svg" alt="corpora validate output">

### Schema

- Parses the schema of a document and outputs it as a JSON schema.
- Normalises the source document into a reference schema — levels, units,
  labels, and the opening word shingles used for alignment;

```zsh
corpora schema book.corpus
```

### Reconcile

```zsh
corpora reconcile book.corpus --schema book.schema.json --yes
```

- Locates those shingles in the archive's Text-Fabric slot stream and reports missing
  units, extra units, boundary drift, length/label mismatches and reordering
  (`RC001`–`RC008`), with optional append-only `.tf` patches for missing
  structure.
- The two sides name their structural levels differently (a Markdown or
  OCR'd-PDF reference yields `h1`/`h2`).
- Declares its own `@sectionTypes`, so reconciliation bridges them first and
  compares **every mapped level pairwise** — a missing or drifted _part_ is
  reported, not just chapter-level trouble.

```bash
# explicit mapping, reference side on the left
corpora reconcile book.corpus --schema book.schema.json \
    --map h1=book --map h2=chapter

# or persist the confirmed mapping next to the corpus for CI
corpora reconcile book.corpus --schema book.schema.json \
    --map-file book.levelmap.json
```

With no `--map`, the mapping is inferred from depth alignment, unit counts
and anchor concordance, printed with its evidence, and must be confirmed —
pass `--yes` in scripts and CI.

### Library

Manages stored archives on the configured storage backend:

```bash
corpora library list                      # list stored archives
corpora library show book.corpus          # print manifest + section tree
corpora library show book.corpus --ref 1  # print the passages under a section
```

<img src="docs/corpora-library-list.svg" alt="corpora library list output">

```bash
corpora library show book.corpus --ref 1  # print the passages under a section
```

`show` prints the archive's manifest, its section tree, and — with `--ref` —
the passages under a section.

<img src="docs/corpora-library-show.svg" alt="corpora library show output">

## Credits

- **[Cody Kingham](https://github.com/Context-Fabric/context-fabric)** — for
  [Context Fabric](https://github.com/Context-Fabric/context-fabric), the engine
  that compiles and queries the datasets every `.corpus` archive is built from.
- **[Will McGugan](https://github.com/textualize/rich)** — for
  [Rich](https://github.com/textualize/rich), which every line this CLI prints
  goes through.

## License

[MIT](/LICENSE)
