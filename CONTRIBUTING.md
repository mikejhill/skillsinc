# Contributing

This guide covers local setup, the quality gate, and how releases are cut. Read [philosophy](docs/architecture/philosophy.md) and [architecture](docs/architecture/overview.md) first.

## Setup

```bash
uv sync
uv run poe check
```

`uv run poe` lists every task. `poe fix` formats and auto-fixes lint; `poe docs-lint` runs markdownlint (needs Node.js).

## Rules

- Python follows the strict Astral toolchain configured in `pyproject.toml`: ruff with the broad rule set, `ty` with every rule at error, pytest with at least 90% branch coverage. Never loosen a setting to pass.
- New behavior needs a test; user-visible behavior also needs a journey test in `tests/test_journeys.py`.
- Tests never touch the real home directory. Pass an `Environment` rooted in `tmp_path`.
- Pull request titles follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) (`feat:`, `fix:`, `docs:` ...); PRs are squash-merged.
- Add an entry under `## [Unreleased]` in `CHANGELOG.md`.

## Releasing

Releases are cut by the **Release** workflow (`workflow_dispatch` on `main`). It computes the next version from commit messages, runs `poe check`, promotes the changelog, bumps `pyproject.toml`, tags `vX.Y.Z`, creates the GitHub release, and publishes to PyPI through trusted publishing.

One-time setup:

1. Register a PyPI trusted publisher for project `skillsinc`, workflow `release.yml`, environment `pypi`.
2. Create the `pypi` GitHub environment with required reviewers.
3. Allow the workflow to push the release commit and tag: add a bypass for GitHub Actions (or a GitHub App) to the `Protected main` and `Immutable version tags` rulesets.
