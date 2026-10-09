# Reference linking in the CLI and Homebrew tap

This checkout adds two **offline** commands:

```bash
corpora references check reference.json
corpora references retrieve endpoint.json --snapshot snapshot.json
```

`check` validates the public Reference model and returns JSON with the same ID.
It does not validate target existence, approve a link, or publish it. `retrieve`
requires exactly one text locator and a full trusted TextSnapshot. It verifies
work/edition/package/revision/document, stream, Unicode-scalar half-open offsets,
exact quote and surrounding context. A stale/missing selection exits 1 with no
stdout; malformed inputs, unsupported selectors or missing core exit 2.
Successful output is JSON: `{"text":"Selected words"}`. It never relocates a
quote, guesses a work, calls Supabase, or writes a document.

The snapshot and immutable identity come from your trusted extraction/inventory
provider. An arbitrary file claiming a revision is not independently proven
original-file evidence. Native PDF/EPUB/HTML selection adapters and authenticated
review belong to the Python service, outside these offline commands.

## Availability

These commands require the published `corpora-linking>=0.1.0,<0.2` dependency,
now declared directly by the CLI. `uv sync` installs it from PyPI; no local
Corpora core source or unpublished umbrella version is needed. The CLI feature
still needs its own release/tag before the public Homebrew formula ships it.

Normal CLI commands and `references --help` import the core lazily; a broken
installation reports the dependency to reinstall. Reference tests now require
the dependency and fail rather than silently skipping when it is missing.

Example snapshot (synthetic text and IDs):

```json
{"endpoint":{"work_id":"book","edition_id":"edition","package_id":"pkg","revision":"rev1","document_id":"d","locators":[]},"stream_id":"body","text":"Before. Linked words. After."}
```

Matching endpoint:

```json
{"work_id":"book","edition_id":"edition","package_id":"pkg","revision":"rev1","document_id":"d","locators":[{"kind":"text","stream_id":"body","start":8,"end":20,"exact":"Linked words","prefix":"Before. ","suffix":". After.","normalization":"preserve"}]}
```

## Release sequence

1. Verify CLI tests and the installed CLI wheel against the released core.
2. Release the CLI through dev → next → release → main with a new VERSION.
3. The release workflow requests the formula bump for the actual **CLI tag**. It
   calculates the tarball checksum and updates Formula/cli.rb on main.
4. Run macOS/Linuxbrew style, audit, install, test and the reference commands.

The corpora-py dependency floor remains unchanged: these offline commands use
the standalone core directly and do not require the future authenticated API
release. Format adapters, persistent review and the API remain Corpora services.
No fabricated package version or release checksum is used.

Homebrew tooling is unavailable in this execution workspace; local Python
checks do not establish a successful brew installation. GitHub resolves the old
exegia/corpora-cli name to exegia/homebrew-corpora; maintain changes here once.

## Existing stable formula catch-up

A separate local `fix/cli-formula-v2-1-0` worktree corrects the stale v1.2.1
formula to the already existing v2.1.0 CLI tag. The downloaded tarball VERSION
and SHA-256 were verified. That correction does not ship these new linking
commands: they still require the future coordinated core/CLI release above.
