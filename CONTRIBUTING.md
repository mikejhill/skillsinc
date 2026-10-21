# Contributing

This guide covers local setup, the quality gate, and how releases are cut. Read [philosophy](docs/architecture/philosophy.md) and [architecture](docs/architecture/overview.md) first.

## Setup

```bash
uv sync
uv run poe check
```

`uv run poe` lists every task. `poe fix` formats and auto-fixes lint; `poe docs-lint` runs markdownlint (needs Node.js).

## Rules

- Python follows the strict Astral toolchain configured in `pyproject.toml`: ruff with the broad rule set, `ty` with every rule at error, pytest with at least 98% branch coverage overall and 100% on changed lines. Never loosen a setting to pass.
- New behavior needs a test; user-visible behavior also needs a journey test in `tests/test_journeys.py`, and a new exit path or output format an e2e test in `tests/e2e/`.
- Tests never touch the real home directory. Pass an `Environment` rooted in `tmp_path`.
- Pull request titles follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) (`feat:`, `fix:`, `docs:` ...); PRs are squash-merged, so the title becomes the commit message and the changelog entry. Do not edit `CHANGELOG.md` by hand.

## Releasing

Releases are automatic: release-please keeps a `chore: release X.Y.Z` pull request open from the Conventional Commits on `main`, and merging it tags, builds, and publishes to PyPI. See [releasing](docs/guides/releasing.md) for the version rules and one-time setup.
