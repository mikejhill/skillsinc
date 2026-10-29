# Development guide

This guide is for a developer or coding agent picking up SkillSinc. It covers setup, the code standards the gate enforces, how the tests are organised, how to try changes safely against real harness folders, and the recipes for common changes.

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). Markdown linting needs Node.js.

```bash
uv sync
uv run poe check
```

| Task                     | Runs                                                                      |
| ------------------------ | ------------------------------------------------------------------------- |
| `uv run poe fix`         | `ruff format` and `ruff check --fix`                                      |
| `uv run poe check`       | Format check, lint, `ty check`, every test layer but perf, coverage ≥ 98% |
| `uv run poe e2e`         | Only the end-to-end tests (the `skillsinc` process)                       |
| `uv run poe perf`        | The performance budgets                                                   |
| `uv run poe mutate`      | Mutation testing with mutmut, then a score report (Linux, macOS, or WSL)  |
| `uv run poe docs-lint`   | `markdownlint-cli2` over every Markdown file                              |
| `uv run poe pypi-readme` | Regenerate `README.pypi.md` from `README.md`                              |
| `uv run skillsinc ...`   | The CLI from the working tree                                             |

## Continuous integration

| Workflow              | Trigger                                  | Jobs                                                                                                                                                                                                                                                                               |
| --------------------- | ---------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ci.yml`              | PRs, `main`, merge queue, Release        | PR title, ruff, `ty`, unit + integration on Linux/Windows/macOS × Python 3.12–3.14 with the coverage gate, 100% diff coverage on PRs, wheel build + `twine check`, e2e against the installed wheel on every OS, perf budgets, markdownlint, actionlint + zizmor, dependency review |
| `security.yml`        | PRs, `main`, weekly                      | `pip-audit` of every locked dependency, CodeQL                                                                                                                                                                                                                                     |
| `mutation.yml`        | `main`, weekly, manual on default branch | Asynchronous full-package mutation feedback; complete results must meet `MUTATION_FLOOR`                                                                                                                                                                                           |
| `mutation-alerts.yml` | Completed mutation runs                  | Separate durable findings and execution-failure issues, including cancelled or incomplete runs                                                                                                                                                                                     |
| `release.yml`         | `main`                                   | release-please, then CI, build, attest, publish to PyPI ([releasing](releasing.md))                                                                                                                                                                                                |

The `CI` and `Security` aggregate jobs are required status checks on `main`. Every action is pinned to a commit SHA; zizmor fails the build on an unpinned action, a persisted checkout credential, or template injection. Dependabot bumps the pins weekly.

The repository's Actions policy allows only GitHub-owned actions plus `astral-sh/setup-uv`, `pypa/gh-action-pypi-publish`, and `github/codeql-action`; any other action makes the workflow fail at startup. Put extra CI logic in `scripts/` (PEP 723 scripts run with `uv run`): `junit_summary.py` writes the test report to the job summary and `mutation_report.py` the mutation score. release-please runs as a pinned npm CLI in the Release workflow for the same reason.

## Code standards

The gate enforces most rules; the rest are conventions to keep:

- **Toolchain:** uv, ruff (broad rule set in `pyproject.toml`), `ty` with every rule at error, pytest. All tool config lives in `pyproject.toml`. Never weaken a rule to pass; suppress a single line only with the rule code and a reason.
- **Layout:** all code in `src/skillsinc/`, tests mirror it under `tests/`.
- **Design:** classes over module-level functions; frozen slotted dataclasses for values; services take collaborators in `__init__`; module level holds only imports, constants, `logger`, and classes.
- **Typing:** every parameter and return annotated; no `Any`; untyped input (JSONC, YAML, JSON, argparse) is converted to dataclasses at the boundary (`config.Table`, `FrontmatterCodec`, `ManifestStore._parse`, `CommandLine.parse`).
- **Docs in code:** every file, class, and method has a one-line docstring; comments are short block headers, not narration.
- **Errors:** raise subclasses of `AppError` from `exceptions.py`. `ConfigurationError` exits 2, other `AppError`s exit 1. Diagnostics go to stderr via `logging`; command output goes to the `out` stream.
- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/); PR titles are linted and squash-merged, and they generate the changelog; do not edit `CHANGELOG.md` by hand.

## Tests

Each test gets a layer marker from its location; select one with `pytest -m <layer>`.

| Layer         | Where                                       | Covers                                                                                  |
| ------------- | ------------------------------------------- | --------------------------------------------------------------------------------------- |
| `unit`        | `tests/test_<module>.py`, `tests/profiles/` | One module each, through its public API                                                 |
| `unit`        | `tests/test_properties.py`                  | Hypothesis properties of the codecs that edit users' files (frontmatter, blocks, JSONC) |
| `integration` | `tests/test_journeys.py`                    | `Application.run([...])` scenarios a user would perform, in one process                 |
| `e2e`         | `tests/e2e/`                                | The `skillsinc` process against a throwaway home: exit codes, streams, real path lookup |
| `perf`        | `tests/perf/`                               | Time budgets and scaling for a large library; excluded unless selected with `-m perf`   |

`tests/conftest.py` holds `Builder` (items, sources, config files), the `Sandbox` journey driver, and fixtures. CI runs the e2e layer against the built wheel by setting `SKILLSINC_E2E_COMMAND` to its installed `skillsinc`; locally it runs `python -m skillsinc`. Property tests run 100 examples locally and 500 in CI (`HYPOTHESIS_PROFILE=ci`); `HYPOTHESIS_PROFILE=deep` runs 5,000. pytest-randomly shuffles test order on every run, so tests must not depend on each other.

Mutation testing (`poe mutate`, `mutation.yml`) changes the code in small ways and checks that a test fails for each change. A surviving mutant identifies behavior to inspect: some mutations are equivalent, while others reveal a missing assertion. The complete 0.3.0 baseline caught 5,946 of 7,005 classified mutants (84.9%), below the 85% target. Never lower the target to obtain a pass.

Mutation runs are asynchronous quality feedback, independent of PR checks, release verification, and composite check commands. Pushes reuse verdict metadata while regenerating source copies and test mappings; weekly and manual runs start fresh. Running jobs are never cancelled by newer commits. Manual runs reject non-default branches. Every run retains logs, text results, and a versioned JSON summary when reporting succeeds. Empty or unclassified results fail completeness validation; they never count as a passing score.

The trusted publisher treats artifacts only as data and maintains separate [mutation findings and execution-failure issues](https://github.com/mikejhill/skillsinc/issues?q=is%3Aissue%20%22Mutation%22), assigned to `mikejhill`. Findings include commit, scope, counts, score, threshold, and a run link. Triage surviving mutants for useful regression tests or documented equivalence; investigate execution failures using the linked logs and rerun on the default branch after repair. Only a newer complete comparable healthy result may resolve findings. Incomplete, skipped, cancelled, malformed, or stale results leave findings open. Execution recovery is tracked separately, so an infrastructure repair cannot conceal an outstanding score regression.

The mutation workspace copies `examples/` and `scripts/` through
`tool.mutmut.also_copy`, because tests read scheduling examples and run the PyPI
README generator beside the package. Keep support files in that list when
adding tests; missing support files prevent the baseline run from reaching a
mutation score.

Rules:

- Tests never touch the real home directory. Use the `environment` fixture (fake home under `tmp_path`, absolute `XDG_CONFIG_HOME`/`XDG_STATE_HOME`) or `Sandbox`.
- Each test checks one behavior, and every assertion carries a message with expected and actual values.
- A bug fix starts with a failing test that reproduces it.
- New user-visible behavior needs a journey test; a new exit path or output format also needs an e2e test.
- Changed lines must be fully covered (the PR diff-coverage gate is 100%).
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
2. Report every dropped field: a `LossPolicy` for frontmatter, plus explicit messages for activation or body changes. Convert MCP servers with `server_entry`. A configuration file beside the root goes in `outside` and is placed by `locate` ([decision 0007](../decisions/0007-files-beside-the-root.md)).
3. Register it in `ProfileRegistry`.
4. Add `tests/profiles/test_<module>.py` and at least one journey test.
5. Document outputs, losses, and the vendor reference in [harness profiles](harness-profiles.md), and confirm the folder locations against current vendor documentation.

### Add a customization kind

1. Add the member to `Kind` in `models.py`, and a suffix in `sources.SUFFIXES` if it is a single-file Markdown kind (MCP servers and hooks are JSON files read by `_json_items` with a per-kind check; resources have their own reader).
2. Give every profile a `support` entry and a `render` branch (refuse it where unsupported).
3. Update the README table, [configuration](configuration.md), and [harness profiles](harness-profiles.md).

### Edit the README

PyPI publishes `README.pypi.md`, generated from `README.md`: PyPI renders no Mermaid and no repository-relative links. After editing `README.md`, run `uv run poe pypi-readme`; CI fails while the two differ. Each Mermaid block in the README is replaced by the SVG beside its copy in `docs/assets/`. To change a diagram, edit the block in both `README.md` and its `.mmd` file, then render the SVG:

```bash
npx --yes @mermaid-js/mermaid-cli@11.4.2 -c docs/assets/mermaid-config.json -b white \
  -i docs/assets/how-it-works.mmd -o docs/assets/how-it-works.svg
```

PyPI shows the SVG from `main`, so it updates when the change merges, not at the next release.

### Change ownership or apply behavior

Read [architecture](../architecture/overview.md) first. Any change to `diff.py` or `applier.py` needs tests for each row of the ownership table and the journey tests for deletion, drift, adoption, and failed sources.

## Releasing

See [releasing](releasing.md). Merging the release pull request that release-please keeps open publishes the release.
