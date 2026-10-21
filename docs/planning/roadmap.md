# Roadmap

This is the prioritized list of work after the 2026-10 rebuild, with enough context for someone new to start any item. Scope decisions behind it are in [decision 0001](../decisions/0001-rebuild-scope.md).

## Status

| Area                                     | State                                                                                                                            |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| CLI (`init`, `plan`, `sync`, `profiles`) | Complete, tested                                                                                                                 |
| Profiles                                 | `claude`, `copilot`, `vscode`, `codex`, `agents`, `generic`                                                                      |
| MCP servers                              | Claude Code, Copilot CLI, VS Code, Codex; see the [gap list](gap-list.md)                                                        |
| Harness locations                        | Verified on Windows on 2026-10-21 for Claude Code, Copilot CLI, and Codex; see [harness profiles](../guides/harness-profiles.md) |
| Releases                                 | Workflow ready; first release blocked on one-time setup (see below)                                                              |

## Next

1. **First release.** Complete the one-time setup in [CONTRIBUTING](../../CONTRIBUTING.md#releasing): PyPI trusted publisher, `pypi` environment, and a GitHub Actions bypass on the `Protected main` and `Immutable version tags` rulesets. Then run the Release workflow.
2. **Remaining native checks.** Confirm that VS Code lists synced instructions, agents, and prompts, that a Claude rule with `paths` loads only for matching files, and that Codex reads the `AGENTS.md` block. Then test on macOS and Linux. Record the results under each profile's "Verified" note in [harness profiles](../guides/harness-profiles.md).
3. **Content diff in `plan`.** `plan --diff` printing a unified diff per changed file (and for the managed block) so users can review updates, not just paths.

## Later

- **Codex hooks.** Waiting until Codex's user hook location is confirmed; see gap 2 in the [gap list](gap-list.md).
- **More profiles.** Cursor (`~/.cursor`), Gemini CLI (`~/.gemini`), OpenCode (`~/.config/opencode`). Each is one class plus tests; verify locations against vendor docs.
- **Project-scope destinations.** Writing into a repository's `.github/` or `.claude/` folders. Profiles currently assume user-level folders.
- **`skillsinc release <destination>`.** Drop a destination's manifest deliberately (today: delete the manifest file by hand, as the error message explains).

## Not planned

Watch mode, OS login services, Git source fetching, and transformation plugins. See [decision 0001](../decisions/0001-rebuild-scope.md); reinstating any of them needs a new decision record.
