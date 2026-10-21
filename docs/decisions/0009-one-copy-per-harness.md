# 0009: One copy of each item per harness

This record is for anyone changing which destinations `skillsinc init` routes to, or adding a profile whose harness reads another profile's folder.

- **Status:** Accepted
- **Date:** 2026-10-21

## Context

Several harnesses read more than their own folder:

- **Copilot CLI** reads personal skills from both `~/.copilot/skills` and `~/.agents/skills` ([Copilot CLI skills](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills)).
- **VS Code** reads personal skills from `~/.copilot/skills`, `~/.claude/skills`, and `~/.agents/skills` ([agent skills](https://code.visualstudio.com/docs/agent-customization/agent-skills)). It reads instructions from `~/.copilot/instructions` ([custom instructions](https://code.visualstudio.com/docs/agent-customization/custom-instructions)) and agents from `~/.copilot/agents` ([custom agents](https://code.visualstudio.com/docs/agent-customization/custom-agents)), as well as from its own `User/prompts` folder. When two locations hold a skill with the same name, both are listed.
- **Codex** reads skills from `~/.agents/skills`.

The starter configuration used to route everything to every detected destination. In a test run on Windows, VS Code listed each instruction and agent twice and each skill up to three times, and Copilot CLI listed each skill twice.

## Decision

- **Profiles declare what their harness also reads.** `Profile.harness` names the application. `Profile.also_reads` maps other profile names to the kinds the harness loads from that profile's default directory.
- **The plan warns about duplicates.** For each planned destination with a harness, the planner collects the items delivered to it and to every planned destination listed in `also_reads` that sits at its profile's default path. Any item delivered twice produces a warning that names the harness, the destinations, and the items. Warnings appear in text and JSON output (`warnings`). They do not block or change the exit code, because the user may want the duplicates.
- **`init` writes routes that avoid duplicates.** When `agents` is detected, `copilot` excludes `skills/*`. When `copilot` is detected, `vscode` excludes `instructions/*` and `agents/*`. Destinations with the same exclusions share one route.

## Consequences

- With Claude Code, VS Code, and `~/.agents` all present, VS Code still lists each skill twice, once from `~/.claude/skills` and once from `~/.agents/skills`. Claude Code reads only `~/.claude/skills` and Codex reads only `~/.agents/skills`, so no routing avoids this. The plan reports it.
- Warnings only cover destinations planned in the same run, so `--destination` can hide them.
- A destination at a custom path is assumed to be outside the harness's search path and is never counted.
