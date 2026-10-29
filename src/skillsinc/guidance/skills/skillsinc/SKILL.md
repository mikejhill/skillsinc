---
name: skillsinc
description: Create and maintain reusable skills, instructions, agents, and prompts in the user's SkillSinc library and distribute them across configured AI harnesses.
---

# SkillSinc library workflow

Maintain shared guidance at its canonical source and distribute it through the
existing plan and sync workflow.

## Locate and edit the source

Run `skillsinc --version` and inspect `skillsinc plan --help`. Read the active
configuration named there (normally `~/.config/skillsinc/config.json`, honoring
`XDG_CONFIG_HOME`). Resolve the selected source's path and routes. The tool
repository is separate from the user's library. Do not replace an existing
configuration with `init --force` to edit guidance.

SkillSinc reads local directories, including local Git clones. Git operations
are separate: check the library's Git status and applicable instructions before
editing. If it is not versioned, report that gap. Do not initialize a repository,
contact a remote, or migrate existing content without authorization.

- Put skills in `skills/<name>/SKILL.md`, with `name` and `description` frontmatter.
- Put shared instructions in `instructions/<name>.instructions.md`. Use
  `applyTo: "**"` when they apply to all tasks.
- Put requested agents in `agents/<name>.agent.md` and prompts in
  `prompts/<name>.prompt.md`.
- Edit canonical sources, including this skill and its companion instruction.
  Do not patch managed destination copies as the durable fix.
- Keep repository-specific guidance with its project. Do not stage credentials,
  private project content, or unrelated files for remote publication.

## Preview and distribute

Run `skillsinc plan` with the active configuration. Inspect all creates, updates,
deletes, ownership conflicts, and conversion losses, including changes unrelated
to the edit. Stop if the plan includes unapproved changes. Do not use `--adopt`
or enable lossy conversion without reviewing its effect.

When distribution is authorized, run `skillsinc sync` with the same configuration
and destination selection, then run `skillsinc plan` again to check convergence.
For manual-only use, leave the preview ready and report that sync is pending.
Do not install a scheduler or push a Git remote as part of editing guidance.

Instructions render as Claude rules, Copilot instructions, VS Code instructions,
and a managed block in Codex `AGENTS.md`. Codex discovers skills through the
`agents` profile's `~/.agents/skills`; the `codex` profile does not copy skills.
Run `skillsinc profiles` to check the installed version's support. Existing
discovery rules can suppress redundant copies across default harness folders.

Review the source Git diff and stage only intended files. Commit locally within
the user's authorization, preserving unrelated work. Report source paths, Git
status, distribution results, and blockers separately. Sync success is not proof
of a Git commit or of an already-running harness reloading its guidance.

Explicit user instructions and repository scope take precedence over defaults
in this skill; preserve private content and existing ownership throughout.
