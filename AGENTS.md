# Corpora CLI agent guide

This repository contains the `corpora` Python CLI and the Homebrew tap that
distributes it. The initial EPUB-to-Text-Fabric sidecar protocol is implemented
and tested. The required `corpora-core` executables remain separate and optional;
do not imply that this package or the Homebrew formula bundles them.

- Work on a feature branch based on `dev`; do not commit directly to a release
  branch. Multiple feature branches are allowed only for related stacked PRs.
  Merge the current feature before starting an unrelated feature.
- Preserve the Homebrew formula and released installation path while migrating
  the CLI. Change release automation and its documentation together.
- Do not commit credentials, local environments, source corpora, or private data.
- Verify changes with focused tests, `uv run ruff check`, and
  `uv run ruff format --check`; use the full `make ci` when its Homebrew and
  network prerequisites are available. Report any checks that could not run.
- Keep protocol stdout machine-readable; logs belong on stderr. Treat cancellation,
  failed jobs, and incomplete cleanup as distinct outcomes.
- Keep the public CLI page current. It lives at https://docs.exegia.co/cli and its
  source is `docs/content/cli/` in `exegia/corpora-api`. Whenever a change merged to
  `dev` adds, removes or changes a command, argument, option, default, limit, exit
  code or install step, open a matching PR there in the same work session (or file
  an issue when you cannot). Keep it short: install steps, then one code snippet and
  one terminal preview per command. Re-record the shots with `make docs`
  (`docs/record.py`) and copy any changed SVG to that repository's
  `docs/public/images/cli/`.
