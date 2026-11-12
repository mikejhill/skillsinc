# 0019: Remote Git sources

Status: Accepted

## Context

Libraries are often published as Git repositories, such as a personal `agent-skills` repository on GitHub. Until now each user had to clone the repository, keep the clone current, and point a source `path` at it. Users want a source to name the repository directly, public or private, while a sync every five minutes stays cheap and never turns a network or authentication problem into deleted skills.

## Decision

A source names exactly one of `path`, `github` (`owner/repo`), or `git` (any URL Git accepts). Remote sources also take `ref` (branch, tag, or full commit SHA; default the remote's `HEAD`), `subdirectory` (the library inside the repository), and `refresh` (the shortest time between remote checks, default every run).

**Transport.** SkillSinc runs the `git` executable instead of calling a hosting API. Git already speaks every host and every authentication method users have set up, adds no Python dependency, and is not subject to REST API rate limits.

**Cost.** Each refresh first runs one `git ls-remote` for the configured ref: a single HTTPS round trip of a few kilobytes. When the commit matches the cached one, nothing is downloaded and nothing on disk changes. Only a new commit triggers a shallow (`--depth 1`) fetch into a persistent bare cache, so Git transfers only the missing objects. A `ref` that is a full commit SHA already in the cache needs no network at all, and `refresh` can throttle checks further. The cache lives in `$XDG_STATE_HOME/skillsinc/remotes/`.

**Atomic updates.** A new commit is checked out into a fresh directory next to the old one; the cache's `state.json` is replaced to point at it only after the checkout succeeds, and older checkouts are removed afterwards. A run interrupted at any point leaves the previous checkout readable. A per-remote lock keeps a `plan` and a scheduled `sync` from refreshing the same cache at once; the run that finds it locked reads the cached checkout.

**Authentication.** Git never prompts: SkillSinc sets `GIT_TERMINAL_PROMPT=0` and `GCM_INTERACTIVE=never`, so a scheduled run fails fast instead of hanging. For `github` sources, a token from `GH_TOKEN`, `GITHUB_TOKEN`, or `gh auth token` is passed as an HTTP header through Git's environment configuration (`GIT_CONFIG_COUNT`), so it never appears in a command line, a log, or the cached repository's configuration. Without a token, and for `git` sources, Git's own credential helpers and SSH keys apply. URLs that embed a password are refused at configuration time.

**Failures.** When a refresh fails and a checkout is cached, the source is read from that last good checkout and the run continues, so destinations keep their content. The failure is a `warning:` line in `plan` and `sync`, appears under `source_warnings` in JSON reports, and makes the run exit 1 so the scheduler's last result and the sync log show it. Without any cached checkout the source fails like a missing directory: its destinations are left unchanged (the existing "never delete after a failed source read" rule). Every subprocess has a timeout.

**Monitoring.** Each cache records the last check, the last success, consecutive failures, and the last error with a category (`auth`, `network`, `ref`, `git-missing`, `timeout`, `other`) and a remedy. The new `skillsinc status` command prints every source's state without touching the network and exits 1 when any remote source's last refresh failed, so it suits a monitoring check. Authentication failures, including GitHub's "repository not found" for an unauthenticated private repository, name the token variables and `gh auth status`.

## Consequences

Remote sources need Git on `PATH`. Configurations with `github` or `git` are rejected by 0.4.x and earlier. The cache is disposable: deleting `remotes/` costs one fresh fetch. A source whose repository never becomes reachable keeps serving its last good checkout indefinitely; `skillsinc status` shows how stale it is. Tests drive real Git against local repositories, so they need no network.
