# Security policy

This document explains how to report a vulnerability and what SkillSinc does and does not protect against.

## Reporting

Report vulnerabilities privately through [GitHub security advisories](https://github.com/mikejhill/skillsinc/security/advisories/new). Do not open a public issue.

## Scope

- SkillSinc copies files you author into folders that AI harnesses load as instructions. Treat your library like code: anything in it can steer an agent.
- It never executes library content, never contacts the network, and stores no credentials.
- It refuses to write through symlinks or junctions inside a destination and never writes outside a configured destination directory.
- Only the latest release receives fixes.
