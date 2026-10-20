# Agent instructions

SkillSinc syncs one library of agent customizations (skills, instructions, agents, prompts) into AI harness folders. This repository contains the tool only; it must never contain anyone's personal skills or library content. Test libraries are synthetic and built in `tmp_path`.

## Start here

1. [Philosophy](docs/architecture/philosophy.md): goals and the safety rules every change must keep.
2. [Architecture](docs/architecture/overview.md): module map, ownership table, safety checks.
3. [Development guide](docs/guides/development.md): setup, code standards, tests, recipes.
4. [Roadmap](docs/planning/roadmap.md): what to work on next.

## Rules

- Never write without a plan, never touch files SkillSinc does not own, never delete after a failed source read.
- `uv run poe fix`, then `uv run poe check` must pass. Never weaken ruff, `ty`, or coverage settings.
- Tests never read or write the real home directory; use the `environment` fixture or `Sandbox`.
- Update the matching guide when behavior changes; record scope changes as a new file in `docs/decisions/`.
- Conventional Commit PR titles; add a `CHANGELOG.md` entry under `[Unreleased]`.
- Workflows may use only GitHub-owned actions, `astral-sh/setup-uv`, `pypa/gh-action-pypi-publish`, and `github/codeql-action`; put other CI logic in `scripts/`.
