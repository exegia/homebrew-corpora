# Corpora CLI agent guide

This repository contains the `corpora` Python CLI and the Homebrew tap that
distributes it. The sidecar protocol in `docs/sidecar-protocol-v1.md` is a draft;
do not describe it as implemented until the command and its tests exist.

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
