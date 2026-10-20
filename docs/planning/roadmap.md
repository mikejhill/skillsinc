# Roadmap

This is the prioritized list of work after the 2026-10 rebuild, with enough context for someone new to start any item. Scope decisions behind it are in [decision 0001](../decisions/0001-rebuild-scope.md).

## Status

| Area                                     | State                                                                               |
| ---------------------------------------- | ----------------------------------------------------------------------------------- |
| CLI (`init`, `plan`, `sync`, `profiles`) | Complete, tested                                                                    |
| Profiles                                 | `claude`, `copilot`, `vscode`, `codex`, `agents`, `generic`                         |
| MCP servers                              | Copilot CLI only; see the [gap list](gap-list.md)                                   |
| Harness locations                        | Checked against vendor docs on 2026-10-13; not yet verified by loading each harness |
| Releases                                 | Workflow ready; first release blocked on one-time setup (see below)                 |

## Next

1. **First release.** Complete the one-time setup in [CONTRIBUTING](../../CONTRIBUTING.md#releasing): PyPI trusted publisher, `pypi` environment, and a GitHub Actions bypass on the `Protected main` and `Immutable version tags` rulesets. Then run the Release workflow.
2. **Native verification.** For each profile, sync a small library into a scratch user, start the harness, and confirm it discovers each kind: skills listed, a rule with `paths` loads only for matching files, agents and commands appear, and the Codex block is read. Record harness versions and results in [harness profiles](../guides/harness-profiles.md) under a "Verified" note per profile.
3. **Content diff in `plan`.** `plan --diff` printing a unified diff per changed file (and for the managed block) so users can review updates, not just paths.

## Later

- **MCP servers for Claude Code, VS Code, and Codex, then hooks and plugins.** Copilot CLI and Codex MCP entries shipped under decisions [0002](../decisions/0002-mcp-server-entries.md) and [0004](../decisions/0004-toml-entries-with-tomlkit.md). The remaining harnesses, in order, are in the [gap list](gap-list.md).
- **More profiles.** Cursor (`~/.cursor`), Gemini CLI (`~/.gemini`), OpenCode (`~/.config/opencode`). Each is one class plus tests; verify locations against vendor docs.
- **Project-scope destinations.** Writing into a repository's `.github/` or `.claude/` folders. Profiles currently assume user-level folders.
- **`skillsinc release <destination>`.** Drop a destination's manifest deliberately (today: delete the manifest file by hand, as the error message explains).

## Not planned

Watch mode, OS login services, Git source fetching, and transformation plugins. See [decision 0001](../decisions/0001-rebuild-scope.md); reinstating any of them needs a new decision record.
