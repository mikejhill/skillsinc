# Releasing SkillSinc

Releases are automatic. Versions follow [SemVer](https://semver.org/) and are computed from [Conventional Commits](https://www.conventionalcommits.org/) on `main` by [release-please](https://github.com/googleapis/release-please).

The manifest sets `always-update` so the generated version branch is rebuilt on
the current `main` even when a CI-only change leaves its release notes unchanged.
The version-only diff and single-parent checks still run before any dispatch.

## How a release happens

1. Pull requests are squash-merged into `main`; the PR title (checked by CI) becomes the commit message.
2. On every push to `main`, the **Release** workflow opens or updates a release pull request titled `chore: release X.Y.Z`. It bumps the version in `pyproject.toml`, `uv.lock`, and `.release-please-manifest.json`, and writes the release's `CHANGELOG.md` entry. The writer runs only for the owner's push to `main`. Before dispatching **CI** and **Security**, it verifies the GitHub Actions bot identity, same-repository release branch, current base and single generated commit, and an exact four-file version-only diff. It never approves held workflow runs or merges the PR.
3. Merging the release pull request tags `vX.Y.Z` and creates the GitHub release. The same workflow then:
   - runs the full CI workflow against the release commit,
   - builds the sdist and wheel once, checks their metadata and that their version matches the tag, and smoke-tests the installed wheel,
   - creates build-provenance attestations,
   - publishes to PyPI with trusted publishing (no API tokens; PEP 740 attestations included),
   - attaches the distributions to the GitHub release.

While the version is below 1.0, breaking changes bump the minor version.

| Commit                        | Example                                 | Next version from 0.4.2 |
| ----------------------------- | --------------------------------------- | ----------------------- |
| `fix:` / `perf:`              | `fix: keep comments in mcp.json`        | 0.4.3                   |
| `feat:`                       | `feat: add a Cursor profile`            | 0.5.0                   |
| `feat!:` / `BREAKING CHANGE:` | `feat!: rename the routes table`        | 0.5.0 (1.0+ → major)    |
| others                        | `docs:`, `ci:`, `chore:`, `test:`, ...  | no release              |

`feat`, `fix`, `perf`, `revert`, and `docs` commits appear in the changelog; the other types are hidden. To force a specific version, merge a commit whose body has a `Release-As: 1.0.0` footer.

release-please runs as its exact-version npm CLI with every transitive package integrity-locked in `.github/release-tools/package-lock.json`. Installation disables package scripts. No personal access token is used.

Dependabot proposes weekly updates for that lock. The required **Security** check
audits its dependency graph without installing packages or running lifecycle scripts,
and fails on moderate or higher npm advisories. CodeQL and the Python dependency audit
remain separate checks.

## One-time setup

These need the repository owner; the workflows cannot do them.

1. **PyPI trusted publisher.** On [pypi.org](https://pypi.org/manage/account/publishing/), add a *pending publisher*: PyPI project name `skillsinc`, owner `mikejhill`, repository `skillsinc`, workflow `release.yml`, environment `pypi`.
2. **Let Actions open pull requests.** Settings → Actions → General → Workflow permissions → enable *Allow GitHub Actions to create and approve pull requests*.
3. **Optional: TestPyPI dry run.** Add the same pending publisher on [test.pypi.org](https://test.pypi.org/manage/account/publishing/) with environment `testpypi`, then run **Actions → Release → Run workflow** with *testpypi* checked to publish the current `main` there.
4. **Optional: release approval.** Add a required reviewer to the `pypi` environment (created by the first run) so publishing waits for an approval.

The `Protected main` ruleset needs no bypass: release-please changes `main` only through its pull request, and the `Immutable version tags` ruleset allows creating new tags.

## Before merging a release pull request

- Check the generated changelog entry reads well; edit the pull request titles (not the changelog) if it does not, then re-run the Release workflow on `main`.
- The **CI** and **Security** checks on the release pull request must be green.

## Commit and artifact boundary

The release tag must resolve to the workflow commit and be an ancestor of `main`. Reusable CI checks out that immutable SHA, the distribution build checks out the same SHA, and publishers consume the artifact ID from that build in the same run. The provenance context therefore identifies the tested and packaged commit. An unpublished older tag cannot be backfilled from a newer main run; retry its original release run instead. Existing PyPI and TestPyPI environment approval settings remain in force.

The workflow fails closed if the generated version PR contains source, workflow, dependency, rename, executable or symlink changes. Review unexpected generator changes before updating the validator. Dispatch checks still have the ordinary CI permissions; the native CodeQL upload retains its existing narrow permission and analysis mode. Check association for a newly generated bot PR must be verified before relying on unattended releases. No release PR is automatically merged.

The expected generated branch is `release-please--branches--main--components--skillsinc`,
matching the configured `skillsinc` package. Changing the package or generator's branch
format requires updating and testing the exact branch validator before releasing.
