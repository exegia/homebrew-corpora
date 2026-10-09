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

This is an unreleased feature branch. The published `corpora-py 5.0.0` wheel was
inspected and does **not** contain `corpora_linking` or `linking_api`. The command
imports the core lazily, so normal CLI commands and `references --help` work with
older dependencies; reference operations explain that a linking-enabled build
is required. No dependency on an unpublished standalone package was added.

For development, install the local built corpora-py linking wheel into a disposable
CLI environment with `--no-deps` after installing the CLI's usual dependencies.
The local wheel still labels itself 5.0.0: this is a development artifact, not a
replacement PyPI release. Alternatively run CLI tests with the core workspace
installed and this repository's src directory on PYTHONPATH.

Example snapshot (synthetic text and IDs):

```json
{"endpoint":{"work_id":"book","edition_id":"edition","package_id":"pkg","revision":"rev1","document_id":"d","locators":[]},"stream_id":"body","text":"Before. Linked words. After."}
```

Matching endpoint:

```json
{"work_id":"book","edition_id":"edition","package_id":"pkg","revision":"rev1","document_id":"d","locators":[{"kind":"text","stream_id":"body","start":8,"end":20,"exact":"Linked words","prefix":"Before. ","suffix":". After.","normalization":"preserve"}]}
```

## Release sequence — not executed here

1. Finish review of the corpora-py linking branch and choose a new release version
   above the already published 5.0.0. Publish only after separate authorization.
2. Set this CLI's corpora-py dependency floor to that actual linking-enabled release,
   or add an optional dependency on an independently released linking core if the
   repository split is adopted. Regenerate the lockfile against real artifacts.
3. Run all CLI tests with the released core. Before that, core-dependent tests skip
   explicitly in environments using the old published dependency; capability/help
   tests still run. Verify installed-wheel commands and stale-anchor failures.
4. Release the CLI through the repository's dev → next → release → main lanes.
5. Run the existing formula bump workflow for the **CLI tag**, not the Python tag.
   It calculates a real tarball checksum and updates Formula/cli.rb on main.
6. On macOS/Linuxbrew, install and run brew audit/style/test and the two reference
   commands. A dependency update alone may require reinstalling an existing keg;
   the new CLI tag ensures the tap provides a clear upgrade path.

The formula URL/checksum and VERSION are deliberately unchanged until a real
release exists. No workflow dispatch, push, formula publication, PyPI publication
or live database changes were performed. Homebrew tooling was unavailable in the
Linux execution workspace; local Python lint/tests/builds are not a brew test.

GitHub resolves the old `exegia/corpora-cli` repository name to
`exegia/homebrew-corpora`; do not maintain duplicate CLI changes in both clones.

## Existing stable formula catch-up

A separate local `fix/cli-formula-v2-1-0` worktree corrects the stale v1.2.1
formula to the already existing v2.1.0 CLI tag. The downloaded tarball VERSION
and SHA-256 were verified. That correction does not ship these new linking
commands: they still require the future coordinated core/CLI release above.
