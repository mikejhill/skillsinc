# Development guide

This guide is for a developer or coding agent picking up SkillSinc. It covers setup, the code standards the gate enforces, how the tests are organised, how to try changes safely against real harness folders, and the recipes for common changes.

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). Markdown linting needs Node.js.

```bash
uv sync
uv run poe check
```

| Task                   | Runs                                                       |
| ---------------------- | ---------------------------------------------------------- |
| `uv run poe fix`       | `ruff format` and `ruff check --fix`                       |
| `uv run poe check`     | Format check, lint, `ty check`, pytest with coverage ≥ 90% |
| `uv run poe docs-lint` | `markdownlint-cli2` over every Markdown file               |
| `uv run skillsinc ...` | The CLI from the working tree                              |

CI (`.github/workflows/ci.yml`) runs the same tasks on Linux, Windows, and macOS with Python 3.12–3.14. The `CI` and `Security` aggregate jobs are required status checks on `main`.

The repository's Actions policy allows only GitHub-owned actions plus `astral-sh/setup-uv`, `pypa/gh-action-pypi-publish`, and `github/codeql-action`; any other action makes the workflow fail at startup. Put extra CI logic in `scripts/` (PEP 723 scripts run with `uv run`): `junit_summary.py` writes the test report to the job summary, and `next_version.py` computes the release version from Conventional Commits.

## Code standards

The gate enforces most rules; the rest are conventions to keep:

- **Toolchain:** uv, ruff (broad rule set in `pyproject.toml`), `ty` with every rule at error, pytest. All tool config lives in `pyproject.toml`. Never weaken a rule to pass; suppress a single line only with the rule code and a reason.
- **Layout:** all code in `src/skillsinc/`, tests mirror it under `tests/`.
- **Design:** classes over module-level functions; frozen slotted dataclasses for values; services take collaborators in `__init__`; module level holds only imports, constants, `logger`, and classes.
- **Typing:** every parameter and return annotated; no `Any`; untyped input (TOML, YAML, JSON, argparse) is converted to dataclasses at the boundary (`config.Table`, `FrontmatterCodec`, `ManifestStore._parse`, `CommandLine.parse`).
- **Docs in code:** every file, class, and method has a one-line docstring; comments are short block headers, not narration.
- **Errors:** raise subclasses of `AppError` from `exceptions.py`. `ConfigurationError` exits 2, other `AppError`s exit 1. Diagnostics go to stderr via `logging`; command output goes to the `out` stream.
- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/); PR titles are linted and squash-merged. Add a `CHANGELOG.md` entry under `[Unreleased]`.

## Tests

| File or folder           | Covers                                                                       |
| ------------------------ | ---------------------------------------------------------------------------- |
| `tests/conftest.py`      | `Builder` (items, sources, config files), `Sandbox` journey driver, fixtures |
| `tests/test_<module>.py` | One module each, through its public API                                      |
| `tests/profiles/`        | One file per profile module                                                  |
| `tests/test_journeys.py` | End-to-end `Application.run([...])` scenarios a user would perform           |

Rules:

- Tests never touch the real home directory. Use the `environment` fixture (fake home under `tmp_path`, absolute `XDG_CONFIG_HOME`/`XDG_STATE_HOME`) or `Sandbox`.
- Each test checks one behavior, and every assertion carries a message with expected and actual values.
- A bug fix starts with a failing test that reproduces it.
- Symlink tests skip when the OS refuses to create links (Windows without Developer Mode).

## Trying changes against real harness folders

`plan` is read-only, so it is safe to run against real folders. Isolate state so manifests from experiments never mix with real ones:

```bash
export XDG_CONFIG_HOME=/tmp/skillsinc-try/config
export XDG_STATE_HOME=/tmp/skillsinc-try/state
uv run skillsinc init --source ~/my-agent-library
uv run skillsinc plan --verbose
```

Before running `sync` against real folders, point destinations at scratch directories with explicit `path` keys, or run a single destination with `-d NAME`.

## Recipes

### Add a harness profile

1. Create a `Profile` subclass in `src/skillsinc/profiles/` with `name`, `summary`, `support`, `default_path`, and `render`. Use `copy_skill`, `copy_file`, `apply_to`, and `variable_losses` from the base class.
2. Report every dropped field: a `LossPolicy` for frontmatter, plus explicit messages for activation or body changes.
3. Register it in `ProfileRegistry`.
4. Add `tests/profiles/test_<module>.py` and at least one journey test.
5. Document outputs, losses, and the vendor reference in [harness profiles](harness-profiles.md), and confirm the folder locations against current vendor documentation.

### Add a customization kind

1. Add the member to `Kind` in `models.py`, and a suffix in `sources.SUFFIXES` if it is a single-file Markdown kind (MCP servers are JSON and have their own reader).
2. Give every profile a `support` entry and a `render` branch (refuse it where unsupported).
3. Update the README table, [configuration](configuration.md), and [harness profiles](harness-profiles.md).

### Change ownership or apply behavior

Read [architecture](../architecture/overview.md) first. Any change to `diff.py` or `applier.py` needs tests for each row of the ownership table and the journey tests for deletion, drift, adoption, and failed sources.

## Releasing

See [CONTRIBUTING](../../CONTRIBUTING.md#releasing). The **Release** workflow is manual (`workflow_dispatch` on `main`) and needs the one-time setup listed there.
