# Agent instructions

SkillSinc syncs one library of agent customizations into AI harness folders. Safety rules in [philosophy](docs/architecture/philosophy.md) are non-negotiable: never write without a plan, never touch unowned files, never delete on a failed source read.

- Gate: `uv run poe check` must pass. Run `uv run poe fix` first.
- Follow the `python-development` skill: classes over functions, docstrings everywhere, no `Any`, strict `ty`.
- Tests use an `Environment` rooted in `tmp_path`; never read or write the real home directory.
- Keep docs in `docs/` (architecture, guides, decisions) and update the matching guide when behavior changes.
- Record scope changes as a new file in `docs/decisions/`.
