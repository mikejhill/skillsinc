---
description: Maintain shared skills and instructions through the SkillSinc library.
applyTo: "**"
---

# Manage shared guidance with SkillSinc

- Use SkillSinc when creating or updating reusable skills, instructions, agents,
  or prompts for the user's harnesses. Read the `skillsinc` skill.
- Read the active SkillSinc configuration to locate the canonical library. Edit
  that source, including this instruction, instead of its managed copies.
- Keep shared guidance versioned in the library's Git repository. Review and
  stage only intended files; preserve unrelated work. If the library is not
  versioned, report the gap instead of claiming the work is saved in Git.
- Keep project-specific guidance with its repository. Do not copy private project
  content or credentials into a shared library or remote.
- Inspect `skillsinc plan` before syncing. Respect manual-only use and the user's
  current authorization. Do not implicitly adopt files, install a scheduler,
  publish, or push.
- If SkillSinc is unavailable or access is blocked, report pending source and
  distribution changes. Do not create competing harness copies.

Explicit user instructions and repository scope take precedence over these
defaults. Preserve privacy and existing file ownership when applying them.
