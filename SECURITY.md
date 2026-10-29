# Security policy

This document explains how to report a vulnerability and what SkillSinc does and does not protect against.

## Reporting

Report vulnerabilities privately through [GitHub security advisories](https://github.com/mikejhill/skillsinc/security/advisories/new). Do not open a public issue.

## Scope

- SkillSinc copies files you author into folders that AI harnesses load as instructions. Treat your library like code: anything in it can steer an agent.
- Sync reads local files without executing library content or contacting the network. SkillSinc does not authenticate to services or maintain a credential store. Values authored in MCP environment variables or headers are copied into destination configuration files; keep real secrets out of a publicly shared library.
- Planning rejects symlinks and junctions beneath a destination root. Writes target that root and the bounded sibling configuration files declared by its profile, such as Claude Code's `../.claude.json` and VS Code's `../mcp.json`. These declared files must not be links; other parent-directory paths are refused.
- Only the latest release receives fixes.
