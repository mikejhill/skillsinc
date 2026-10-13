# Harness profiles

This guide lists exactly what each profile writes and which conversions lose information. Use it to decide which routes need `allow_lossy = true`.

## Conventions

- **exact**: the library file is copied byte for byte.
- **converted**: frontmatter is rewritten for the harness; losses are reported per item.
- Library files use the GitHub Copilot formats: `applyTo` on instructions, `tools`/`model` on agents, `${input:...}` variables in prompts.

## claude

Default path `$CLAUDE_CONFIG_DIR` or `~/.claude`. Reference: [Claude Code memory and rules](https://code.claude.com/docs/en/memory).

| Kind         | Output               | Conversion                                             |
| ------------ | -------------------- | ------------------------------------------------------ |
| skills       | `skills/<name>/`     | exact                                                  |
| instructions | `rules/<name>.md`    | `applyTo` → `paths`; `**` → no `paths` (always loaded) |
| agents       | `agents/<name>.md`   | keeps `name`, `description`; requires a description    |
| prompts      | `commands/<name>.md` | keeps `description`, `argument-hint`                   |

Losses: instructions without `applyTo` (on demand in Copilot, always loaded in Claude); `excludeAgent`; agent `tools`, `model`, `handoffs`, and other Copilot fields; prompt `agent`, `mode`, `model`, `tools`; VS Code `${...}` variables in prompt bodies.

## copilot

Default path `$COPILOT_HOME` or `~/.copilot`. Reference: [Copilot CLI custom instructions](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-custom-instructions).

| Kind         | Output                                | Conversion |
| ------------ | ------------------------------------- | ---------- |
| skills       | `skills/<name>/`                      | exact      |
| instructions | `instructions/<name>.instructions.md` | exact      |
| agents       | `agents/<name>.agent.md`              | exact      |
| prompts      | unsupported                           | —          |

Loss: an instructions file without `applyTo`, which Copilot CLI never applies.

## vscode

Default path: the VS Code user `prompts` folder (`%APPDATA%\Code\User\prompts`, `~/Library/Application Support/Code/User/prompts`, or `$XDG_CONFIG_HOME/Code/User/prompts`). Instructions, agents, and prompts are copied exactly with their `.instructions.md`, `.agent.md`, and `.prompt.md` suffixes. VS Code reads skills from `~/.copilot/skills` and `~/.agents/skills`, so route skills to a `copilot` or `agents` destination.

## codex

Default path `$CODEX_HOME` or `~/.codex`. Instructions are combined, in name order, into a managed block in `AGENTS.md`; the rest of the file is preserved. Codex reads user skills from `~/.agents/skills` ([Codex skills](https://developers.openai.com/codex/skills)), so route skills to an `agents` destination.

Losses: `applyTo` globs become an advisory sentence; files without `applyTo` become always-on.

## agents

Default path `~/.agents`. Skills only, copied exactly to `skills/<name>/`. Codex, Copilot, and other [Agent Skills](https://agentskills.io/specification) readers discover this folder.

## generic

No default path. Copies the library layout verbatim: `skills/<name>/`, and `<kind>/<original file name>` for everything else. Useful for exports and backups.
