# 0005: Plugins expand into their component kinds

This record decides how SkillSinc syncs agent plugins: by expanding each library plugin into the kinds it already syncs, or by publishing a local plugin marketplace. It is for anyone extending plugin support or adding a harness.

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

A plugin is a directory of skills, subagents, slash commands, hooks, MCP servers, and other components, optionally described by a manifest. Claude Code reads the manifest from `.claude-plugin/plugin.json` ([plugin reference](https://code.claude.com/docs/en/plugins-reference)); Copilot CLI installs the same layout and also looks for `plugin.json` under `.github/plugin/` ([Copilot CLI plugin reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference)). Codex, VS Code's prompts folder, and `~/.agents` have no user plugin folder SkillSinc could write.

Both harnesses that understand plugins install them from a marketplace into their own cache (`~/.claude/plugins/`, `~/.copilot/...`) and record what is enabled in their settings. Components are namespaced by plugin (`deploy-tools:reviewer`) and may refer to `${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PLUGIN_DATA}`, or `${user_config.*}`, which only resolve inside an installed plugin.

Two options:

| Option                                                                                                              | Works for                        | Cost                                                                                                                                                                                                                                                                   |
| ------------------------------------------------------------------------------------------------------------------- | -------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Expand** each library plugin into skills, agents, prompts, and MCP servers, then render those through profiles | Every profile, today             | Loses the bundle: namespacing, enable/disable as a unit, and components with no kind yet (hooks, LSP servers, output styles, `bin/`)                                                                                                                                   |
| **B. Marketplace**: write a local `marketplace.json` plus plugin copies the user registers once                     | Claude Code and Copilot CLI only | The harness copies the plugin into its own cache on install and update, so SkillSinc cannot see or own what is installed; a user step (`/plugin marketplace add`, `/plugin install`) per machine; enabling means editing `settings.json`; two more JSON formats to own |

## Decision

**Expand (option A) is the default and only mode for now.** It fits the principles: the library stays the source of truth, every written file is owned and reconciled, and losses are reported per item instead of hidden in a harness cache. It reuses the profiles, loss policies, and MCP entries that exist, and it reaches Codex (through `~/.agents` skills) and VS Code, which a marketplace never would. A marketplace adds ownership of state SkillSinc does not control, for two harnesses that can already load a plugin directory directly.

The rules:

- **Library layout.** One plugin per directory under `plugins/` (`sources.<name>.layout.plugins` renames it), in Claude Code's standard layout. The manifest `.claude-plugin/plugin.json` is optional; if it has a `name`, it must equal the directory name.
- **Expansion.** `skills/<name>/` → skills, `agents/*.md` → agents, `commands/*.md` → prompts, `.mcp.json` (an `mcpServers` map, or the bare map) → MCP servers. Each item keeps its own name; a name that also exists in the library or another plugin fails the source, as two library files with one name already do.
- **Format.** Plugin agents and commands are Claude Code files. The `claude` profile copies them exactly. Other profiles keep `name`, `description`, and (for prompts) `argument-hint`, and report every other field, plus `$ARGUMENTS`-style placeholders and `` !`cmd` `` shell context, as a loss.
- **Fail closed.** A manifest that relocates components (`skills`, `agents`, `commands`, `mcpServers`) fails the source, because reading the default folders would sync the wrong files. An item whose files (skill assets included) refer to a `CLAUDE_PLUGIN_*` variable (such as `${CLAUDE_PLUGIN_ROOT}`) or to `${user_config.*}` is skipped with that reason in every destination: nothing would resolve it.
- **Visible losses.** Anything else in the plugin (hooks, LSP servers, output styles, `bin/`, scripts, settings, `userConfig`, dependencies, a root `SKILL.md`, nested agent or command folders) is named as a loss on every item of that plugin, so the route must set `allow_lossy = true` to sync part of a plugin. Namespacing is lost too and is documented rather than reported per item.
- **Selection.** Plugin items match route patterns by `kind/name` like any item, and also by `plugins/<plugin>/kind/name`, so a route can include or exclude a whole plugin.

## Consequences

- Plugins work with every profile today, with no new dependency and no new ownership format.
- Hook-heavy plugins mostly sync as losses until the hooks kind (gap 2) exists; it should expand `hooks/hooks.json` the same way.
- A plugin's scripts are not copied, so components that call them by relative path are not portable. Skill assets inside `skills/<name>/` are copied as usual.
- Option B remains possible as an opt-in profile (`claude-marketplace`) if users want bundles intact in Claude Code or Copilot CLI. It needs its own decision record covering ownership of `marketplace.json` and the `enabledPlugins` setting.
