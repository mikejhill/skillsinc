# Optional self-managed onboarding guidance

## Context

Users can sync a library without teaching their assistants to maintain its
canonical sources. Harness-specific edits then drift. Other users want only
manual syncing and do not want additional instructions installed.

## Decision

`skillsinc init --with-guidance` explicitly installs two portable starter files
in the chosen local source, or the default library:

- `skills/skillsinc/SKILL.md`
- `instructions/skillsinc.instructions.md`, with `applyTo: "**"`

The skill explains source discovery, authoring, Git boundaries, and plan/sync.
The always-on instruction directs assistants to use that workflow. Both files
are ordinary library content after installation, distributed by the existing
profiles and ownership rules. The library, including its own SkillSinc guidance,
is the editable source of truth. Keep project-specific instructions in their
project repositories.

When Codex or VS Code is detected, opt-in also enables the `agents` destination
even if `~/.agents` does not yet exist: those profiles cannot themselves write
skills. Its directory is created only by an explicit sync. Other profile
selection and conversion policies remain unchanged.

Plain `init` keeps its existing empty-library behavior. Opting in neither syncs
destinations nor registers a scheduler, initializes Git, contacts a remote,
changes routes to accept losses, or adopts existing destination files. Manual
sync remains supported. Users manage library Git history separately; no remote
source model or personal content is added to the product repository.

Preflight both asset paths before changing configuration or creating content.
Existing identical bytes are left untouched; different content, directories,
linked paths, and non-directory parents fail with a configuration error.
Check links before comparing normalized config and guidance locations, so `..`
aliases cannot overwrite a source asset with generated configuration.
`--force` applies only to configuration, never to guidance conflicts. Exclusive
file creation also refuses targets appearing after preflight. An interrupted or
failed write can leave a partial installation: preserve it for review rather
than deleting content that another process could have changed.

Keep the existing config overwrite rule: a repeat `init` requires `--force`.
With that flag, identical guidance is a no-op; customized guidance still blocks.
Package upgrades do not automatically refresh installed guidance. Users review
new defaults and edit their canonical source deliberately.

## Existing installations

Existing users can copy the two packaged Markdown assets into their chosen
library, review `skillsinc plan`, and explicitly sync. Do not use `init --force`
over a customized configuration merely to add guidance. A separate installer
command or persistent bootstrap setting is outside this initial change.

## Verification

Use synthetic homes and sources to verify opt-in/default behavior, source and
profile schemas, conflict preflight, identical repeats, path/link safety,
exclusive creation, process exit codes, and ordinary sync convergence after
editing the canonical instruction. Verify the built wheel includes the assets.
