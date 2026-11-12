"""Remote Git sources: configuration, caching, failures, and status (decision 0019).

Tests drive the real ``git`` executable against repositories in ``tmp_path``,
so they need no network.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import override

import pytest

from skillsinc.__main__ import Application
from skillsinc.config import Config, ConfigLoader, RemoteConfig, SourceConfig, mask
from skillsinc.exceptions import ConfigurationError, SourceError
from skillsinc.lock import StateLock
from skillsinc.models import Kind
from skillsinc.paths import Environment
from skillsinc.profiles.registry import ProfileRegistry
from skillsinc.remote import (
    STATE_FILE,
    Category,
    GitRunner,
    Ran,
    RemoteCache,
    RemoteState,
)
from skillsinc.sources import SourceReader

SKILL = "---\nname: {name}\ndescription: {text}\n---\n{text}\n"
IGNORED = ("GH_TOKEN", "GITHUB_TOKEN", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "HOME")


def _skill(name: str, text: str = "Hello") -> dict[str, str]:
    """Return the files of one skill under ``library/``."""
    return {f"library/skills/{name}/SKILL.md": SKILL.format(name=name, text=text)}


class Repository:
    """A work repository and the bare remote it pushes to."""

    def __init__(self, root: Path, variables: Mapping[str, str]) -> None:
        self.work = root / "work"
        self.remote = root / "remote.git"
        self._variables = dict(variables)
        self.work.mkdir(parents=True)
        self.git("init", "--quiet", "--initial-branch=main", str(self.work), cwd=root)
        self.git(
            "init",
            "--quiet",
            "--bare",
            "--initial-branch=main",
            str(self.remote),
            cwd=root,
        )
        self.git("remote", "add", "origin", str(self.remote))

    def git(self, *argv: str, cwd: Path | None = None) -> str:
        """Run Git in the work repository."""
        completed = subprocess.run(  # noqa: S603 - argv is built by the test
            ["git", *argv],  # noqa: S607 - git on PATH is a test requirement
            cwd=cwd or self.work,
            env=self._variables,
            capture_output=True,
            check=True,
            encoding="utf-8",
        )
        return completed.stdout.strip()

    def commit(self, files: Mapping[str, str | None], branch: str = "main") -> str:
        """Write (or delete, for None) ``files``, commit, push; return the SHA."""
        for relative, content in files.items():
            path = self.work / relative
            if content is None:
                path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        self.git("add", "--all")
        self.git("commit", "--quiet", "--message", "change")
        self.git("push", "--quiet", "origin", f"HEAD:refs/heads/{branch}")
        return self.git("rev-parse", "HEAD")

    def tag(self, name: str) -> None:
        """Push an annotated tag at HEAD."""
        self.git("tag", "--annotate", "--message", name, name)
        self.git("push", "--quiet", "origin", f"refs/tags/{name}")


@dataclass
class Recorder(GitRunner):
    """Run real commands while recording them; optionally fail some."""

    calls: list[list[str]]
    fail: Mapping[str, Ran]
    seen: list[dict[str, str]]

    @override
    def run(
        self, argv: Sequence[str], environment: Mapping[str, str], timeout: float
    ) -> Ran:
        """Record ``argv``, then fail it as configured or run it."""
        self.calls.append(list(argv))
        self.seen.append(dict(environment))
        for verb, result in self.fail.items():
            if verb in argv:
                return result
        return super().run(argv, environment, timeout)

    def verbs(self) -> list[str]:
        """Return the Git subcommands run so far."""
        return [
            next(arg for arg in call[1:] if not arg.startswith("-"))
            for call in self.calls
        ]


def _recorder(fail: Mapping[str, Ran] | None = None) -> Recorder:
    """Return a recording runner."""
    return Recorder([], fail or {}, [])


@pytest.fixture
def git_environment(tmp_path: Path) -> Environment:
    """An environment that can run Git without the real user's configuration."""
    home = tmp_path / "home"
    home.mkdir()
    empty = tmp_path / "gitconfig"
    empty.write_text("", encoding="utf-8")
    variables = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in IGNORED and not key.upper().startswith("GIT_")
    }
    variables |= {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "GIT_CONFIG_GLOBAL": str(empty),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }
    return Environment(home, variables, sys.platform)


@pytest.fixture
def repository(tmp_path: Path, git_environment: Environment) -> Repository:
    """A remote holding one skill on ``main``."""
    repo = Repository(tmp_path / "repo", git_environment.variables)
    repo.commit(_skill("hello"))
    return repo


def _source(
    environment: Environment, repository: Repository, **settings: object
) -> SourceConfig:
    """Load a remote source pointing at ``repository``."""
    return _config(environment, repository, **settings).sources["remote"]


def _config(
    environment: Environment, repository: Repository, **settings: object
) -> Config:
    """Write and load a configuration routing the remote to one destination."""
    source = {"git": str(repository.remote), "subdirectory": "library", **settings}
    data = {
        "version": 1,
        "sources": {"remote": source},
        "destinations": {
            "target": {"profile": "generic", "path": str(environment.home / "dest")}
        },
        "routes": [{"sources": ["remote"], "destinations": ["target"]}],
    }
    path = environment.config_home / "skillsinc" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return ConfigLoader(ProfileRegistry(), environment).load(path)


def _names(items: Sequence[object]) -> list[str]:
    """Return item names."""
    return [getattr(item, "name", "") for item in items]


def _run(environment: Environment, *argv: str) -> tuple[int, str]:
    """Run the application and capture stdout."""
    out = io.StringIO()
    status = Application(environment, out=out).run(list(argv))
    return status, out.getvalue()


class TestRemoteConfiguration:
    """Configuration of github and git sources."""

    @pytest.fixture
    def loader(self, environment: Environment) -> ConfigLoader:
        """A loader bound to the isolated environment."""
        return ConfigLoader(ProfileRegistry(), environment)

    def _load(self, loader: ConfigLoader, tmp_path: Path, source: object) -> Config:
        path = tmp_path / "config.json"
        path.write_text(
            json.dumps({"version": 1, "sources": {"s": source}}), encoding="utf-8"
        )
        return loader.load(path)

    def test_github_shorthand_names_the_https_url(
        self, loader: ConfigLoader, tmp_path: Path, environment: Environment
    ) -> None:
        """``owner/repo`` becomes the GitHub HTTPS URL, cached in the state dir."""
        source = self._load(
            loader,
            tmp_path,
            {"github": "acme/skills", "ref": "v1", "refresh": "15m"},
        ).sources["s"]

        assert source.remote == RemoteConfig(
            url="https://github.com/acme/skills.git",
            label="acme/skills",
            ref="v1",
            refresh=timedelta(minutes=15),
            github=True,
        )
        assert source.path.parent == environment.remote_cache

    def test_git_url_and_subdirectory(
        self, loader: ConfigLoader, tmp_path: Path
    ) -> None:
        """Any Git URL works, and the subdirectory is normalized."""
        source = self._load(
            loader,
            tmp_path,
            {"git": "git@example.com:team/lib.git", "subdirectory": "a//b/"},
        ).sources["s"]

        assert source.remote is not None
        assert (source.remote.url, source.remote.subdirectory) == (
            "git@example.com:team/lib.git",
            "a/b",
        )
        assert not source.remote.github

    @pytest.mark.parametrize(
        ("source", "message"),
        [
            ({"path": "lib", "github": "a/b"}, "exactly one of path, github, git"),
            ({"github": "a/b", "git": "x"}, "exactly one of path, github, git"),
            ({"github": "nope"}, "expected 'owner/repo'"),
            ({"github": "a/b.git"}, "expected 'owner/repo'"),
            ({"git": "https://me:secret@host/r.git"}, "credentials or a user"),
            ({"git": "https://TOKEN123@host/r.git"}, "credentials or a user"),
            ({"git": "ssh://me:secret@host/r.git"}, "credentials or a user"),
            ({"git": "--upload-pack=x"}, "invalid repository URL"),
            ({"git": "has space"}, "invalid repository URL"),
            ({"github": "a/b", "ref": "-x"}, "invalid branch"),
            ({"github": "a/b", "ref": "a..b"}, "invalid branch"),
            ({"github": "a/b", "ref": "a b"}, "invalid branch"),
            ({"github": "a/b", "subdirectory": "../x"}, "relative path"),
            ({"github": "a/b", "subdirectory": "/x"}, "relative path"),
            ({"github": "a/b", "subdirectory": "a\\b"}, "relative path"),
            ({"github": "a/b", "subdirectory": "c:x"}, "relative path"),
            ({"github": "a/b", "refresh": "soon"}, "refresh"),
            ({"path": "lib", "ref": "main"}, "ref applies only to github or git"),
            ({"path": "lib", "subdirectory": "x"}, "subdirectory applies only"),
            ({}, "path is required"),
        ],
    )
    def test_rejects_invalid_remote_settings(
        self, loader: ConfigLoader, tmp_path: Path, source: object, message: str
    ) -> None:
        """Ambiguous, unsafe, or malformed remote settings fail validation."""
        with pytest.raises(ConfigurationError, match=message):
            self._load(loader, tmp_path, source)

    def test_rejected_credentials_are_never_echoed(
        self, loader: ConfigLoader, tmp_path: Path
    ) -> None:
        """A token in a URL does not appear in the validation error."""
        with pytest.raises(ConfigurationError) as caught:
            self._load(loader, tmp_path, {"git": "https://ghp_SECRET@host/r.git"})

        assert "ghp_SECRET" not in str(caught.value)

    @pytest.mark.parametrize(
        "url", ["ssh://git@example.com/team/lib.git", "git@example.com:team/lib.git"]
    )
    def test_ssh_user_names_are_allowed(
        self, loader: ConfigLoader, tmp_path: Path, url: str
    ) -> None:
        """An SSH login name is not a secret and stays in the label."""
        source = self._load(loader, tmp_path, {"git": url}).sources["s"]

        assert source.remote is not None
        assert source.remote.label == url

    def test_cache_key_depends_on_url_and_ref(self) -> None:
        """Changing the URL or ref starts a fresh cache; odd names are made safe."""
        base = RemoteConfig(url="u", label="u")

        keys = {
            base.cache_key("a b"),
            replace(base, ref="main").cache_key("a b"),
            replace(base, url="v").cache_key("a b"),
        }

        assert len(keys) == 3
        assert all(key.startswith("a_b-") for key in keys)

    def test_pinned_means_a_full_commit(self) -> None:
        """Only a full SHA counts as pinned."""
        assert RemoteConfig("u", "u", ref="a" * 40).pinned
        assert not RemoteConfig("u", "u", ref="a" * 12).pinned
        assert not RemoteConfig("u", "u").pinned


class TestRefresh:
    """Fetching, reusing, and replacing cached checkouts."""

    def test_first_read_fetches_and_reads_the_subdirectory(
        self, git_environment: Environment, repository: Repository
    ) -> None:
        """The first read clones the commit and reads the library inside it."""
        source = _source(git_environment, repository)
        reader = SourceReader(remotes=RemoteCache(git_environment))

        items = reader.read(source)

        assert _names(items) == ["hello"]
        assert reader.warnings == {}
        state = RemoteCache(git_environment).state(source)
        assert state.commit == repository.git("rev-parse", "HEAD")
        assert state.failures == 0
        assert state.succeeded_at is not None

    def test_unchanged_remote_costs_one_ls_remote(
        self, git_environment: Environment, repository: Repository
    ) -> None:
        """When the commit is unchanged, nothing is fetched or rewritten."""
        source = _source(git_environment, repository)
        RemoteCache(git_environment).checkout(source)
        before = sorted(path.name for path in source.path.iterdir())
        runner = _recorder()

        RemoteCache(git_environment, runner).checkout(source)

        assert runner.verbs() == ["ls-remote"]
        assert sorted(path.name for path in source.path.iterdir()) == before

    def test_new_commit_replaces_the_checkout(
        self, git_environment: Environment, repository: Repository
    ) -> None:
        """A pushed change is fetched; the old checkout outlives a grace period.

        A concurrent run may still be reading the old checkout (Codex review
        of #59), so it is removed only by a refresh an hour later.
        """
        source = _source(git_environment, repository)
        start = datetime(2026, 1, 1, tzinfo=UTC)
        first = RemoteCache(git_environment, clock=lambda: start).checkout(source).path
        repository.commit({**_skill("second"), "library/skills/hello/SKILL.md": None})
        runner = _recorder()

        second = RemoteCache(git_environment, runner, clock=lambda: start).checkout(
            source
        )
        kept = first.exists()
        RemoteCache(
            git_environment, clock=lambda: start + timedelta(minutes=59)
        ).checkout(source)
        still_kept = first.exists()
        RemoteCache(git_environment, clock=lambda: start + timedelta(hours=1)).checkout(
            source
        )

        second = second.path
        assert runner.verbs() == ["ls-remote", "fetch", "rev-parse", "checkout"]
        assert second != first
        assert (kept, still_kept, first.exists()) == (True, True, False)
        assert RemoteCache(git_environment).state(source).retired == {}
        assert _names(SourceReader().read(replace(source, remote=None, path=second)))
        assert sorted(path.name for path in (second / "skills").iterdir()) == ["second"]

    @pytest.mark.parametrize("ref", ["feature", "refs/heads/feature"])
    def test_branch_ref(
        self, git_environment: Environment, repository: Repository, ref: str
    ) -> None:
        """A branch is followed by name or full ref."""
        commit = repository.commit(_skill("branchy"), branch="feature")
        source = _source(git_environment, repository, ref=ref)

        RemoteCache(git_environment).checkout(source)

        assert RemoteCache(git_environment).state(source).commit == commit

    def test_annotated_tag_is_peeled(
        self, git_environment: Environment, repository: Repository
    ) -> None:
        """A tag resolves to its commit, not the tag object."""
        repository.tag("v1")
        commit = repository.git("rev-parse", "HEAD")
        repository.commit(_skill("later"))
        source = _source(git_environment, repository, ref="v1")
        runner = _recorder()
        cache = RemoteCache(git_environment, runner)

        cache.checkout(source)
        cache.checkout(source)

        assert cache.state(source).commit == commit
        assert runner.verbs().count("fetch") == 1

    def test_pinned_commit_needs_no_network_once_cached(
        self, git_environment: Environment, repository: Repository
    ) -> None:
        """A full SHA is fetched once and never checked again."""
        commit = repository.git("rev-parse", "HEAD")
        repository.commit(_skill("later"))
        source = _source(git_environment, repository, ref=commit)
        RemoteCache(git_environment).checkout(source)
        runner = _recorder()

        checkout = RemoteCache(git_environment, runner).checkout(source)

        assert runner.calls == []
        assert sorted(path.name for path in (checkout.path / "skills").iterdir()) == [
            "hello"
        ]

    def test_refresh_interval_skips_the_remote(
        self, git_environment: Environment, repository: Repository
    ) -> None:
        """Within ``refresh`` of the last check, the cache is read as is."""
        source = _source(git_environment, repository, refresh="10m")
        start = datetime(2026, 1, 1, tzinfo=UTC)
        RemoteCache(git_environment, clock=lambda: start).checkout(source)
        quiet, due = _recorder(), _recorder()

        RemoteCache(
            git_environment, quiet, clock=lambda: start + timedelta(minutes=9)
        ).checkout(source)
        RemoteCache(
            git_environment, due, clock=lambda: start + timedelta(minutes=10)
        ).checkout(source)

        assert quiet.calls == []
        assert due.verbs() == ["ls-remote"]

    def test_interrupted_checkout_is_cleaned_up(
        self, git_environment: Environment, repository: Repository
    ) -> None:
        """Leftover partial checkouts are removed by the next refresh."""
        source = _source(git_environment, repository)
        RemoteCache(git_environment).checkout(source)
        leftover = source.path / "tree-000000000000.partial"
        (leftover / "skills").mkdir(parents=True)
        (leftover / "skills" / "file").write_text("x", encoding="utf-8")

        RemoteCache(git_environment).checkout(source)

        assert not leftover.exists()


class TestCacheMaintenance:
    """Edge cases of the runner and the cache files."""

    def test_runner_reports_missing_programs_and_timeouts(self) -> None:
        """A missing program exits 127; a slow one is stopped and exits 124."""
        runner = GitRunner()
        variables = dict(os.environ)

        missing = runner.run(["skillsinc-no-such-program"], variables, 5)
        slow = runner.run(
            [sys.executable, "-c", "import time; time.sleep(5)"], variables, 0.2
        )

        assert missing.code == 127
        assert (slow.code, slow.stderr) == (124, "timed out after 0.2 seconds")

    def test_unparseable_check_time_means_due(
        self, git_environment: Environment, repository: Repository
    ) -> None:
        """A damaged timestamp triggers a check rather than an error."""
        source = _source(git_environment, repository, refresh="1h")
        cache = RemoteCache(git_environment)
        cache.checkout(source)
        state = cache.state(source)
        RemoteCache._save(source.path, replace(state, checked_at="yesterday"))
        runner = _recorder()

        RemoteCache(git_environment, runner).checkout(source)

        assert runner.verbs() == ["ls-remote"]

    def test_state_naming_a_foreign_directory_is_refetched(
        self, git_environment: Environment, repository: Repository
    ) -> None:
        """Only ``tree-*`` directories inside the cache are ever read."""
        source = _source(git_environment, repository)
        cache = RemoteCache(git_environment)
        cache.checkout(source)
        state = cache.state(source)
        RemoteCache._save(source.path, replace(state, tree="git"))

        assert cache.library(source) is None
        assert cache.checkout(source).path.parent.name.startswith("tree-")

    def test_ls_remote_noise_is_ignored(
        self, git_environment: Environment, repository: Repository
    ) -> None:
        """Lines that are not a SHA, a tab, and a ref are skipped."""
        commit = repository.git("rev-parse", "HEAD")
        source = _source(git_environment, repository)
        listing = Ran(0, f"warning: noise\n{commit}\tHEAD\n")
        cache = RemoteCache(git_environment, _recorder({"ls-remote": listing}))

        cache.checkout(source)

        assert cache.state(source).commit == commit

    def test_prune_tolerates_undeletable_checkouts(
        self,
        git_environment: Environment,
        repository: Repository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An old checkout that cannot be removed now is retried next time."""
        source = _source(git_environment, repository)
        start = datetime(2026, 1, 1, tzinfo=UTC)
        RemoteCache(git_environment, clock=lambda: start).checkout(source)
        stale = source.path / "tree-111111111111"
        stale.mkdir()
        RemoteCache(git_environment, clock=lambda: start).checkout(source)

        def refuse(path: Path) -> None:
            raise PermissionError(path)

        monkeypatch.setattr(RemoteCache, "_remove", staticmethod(refuse))
        later = start + timedelta(hours=2)
        RemoteCache(git_environment, clock=lambda: later).checkout(source)

        assert stale.is_dir()
        retired = RemoteCache(git_environment).state(source).retired
        assert retired == {stale.name: start.isoformat(timespec="seconds")}

    def test_damaged_or_zoneless_times_are_treated_as_unknown(
        self, git_environment: Environment, repository: Repository
    ) -> None:
        """Unreadable retirement times restart the grace; checks become due."""
        source = _source(git_environment, repository, refresh="1h")
        start = datetime(2026, 1, 1, tzinfo=UTC)
        cache = RemoteCache(git_environment, clock=lambda: start)
        cache.checkout(source)
        stale = source.path / "tree-222222222222"
        stale.mkdir()
        state = replace(
            cache.state(source),
            checked_at="2026-01-01T00:00:00",
            retired={stale.name: "garbage"},
        )
        RemoteCache._save(source.path, state)
        runner = _recorder()

        RemoteCache(git_environment, runner, clock=lambda: start).checkout(source)

        assert runner.verbs() == ["ls-remote"]
        assert stale.is_dir()
        assert cache.state(source).retired == {
            stale.name: start.isoformat(timespec="seconds")
        }

    def test_existing_checkout_of_the_fetched_commit_is_reused(
        self, git_environment: Environment, repository: Repository
    ) -> None:
        """A complete checkout already on disk is adopted, never rewritten."""
        source = _source(git_environment, repository)
        cache = RemoteCache(git_environment)
        tree = cache.checkout(source).path.parent
        marker = tree / "marker"
        marker.write_text("kept", encoding="utf-8")
        (source.path / STATE_FILE).unlink()
        runner = _recorder()

        RemoteCache(git_environment, runner).checkout(source)

        assert "checkout" not in runner.verbs()
        assert marker.is_file()

    def test_read_only_files_are_made_writable_before_removal(
        self, tmp_path: Path
    ) -> None:
        """The removal retry clears the read-only flag Windows enforces."""
        path = tmp_path / "locked"
        path.write_text("x", encoding="utf-8")
        path.chmod(0o444)

        RemoteCache._writable_retry(os.unlink, str(path), PermissionError())

        assert not path.exists()


class TestSafety:
    """Findings from the Codex review of #59: concurrent readers and links."""

    def test_checkout_in_use_survives_a_concurrent_refresh(
        self, git_environment: Environment, repository: Repository
    ) -> None:
        """A sync reading a checkout is unaffected by a plan replacing it."""
        source = _source(git_environment, repository)
        sync = RemoteCache(git_environment).checkout(source).path
        repository.commit({**_skill("second"), "library/skills/hello/SKILL.md": None})

        planned = RemoteCache(git_environment).checkout(source).path
        items = SourceReader().read(replace(source, remote=None, path=sync))

        assert planned != sync
        assert _names(items) == ["hello"]

    @pytest.mark.skipif(sys.platform == "win32", reason="links need privileges")
    def test_symbolic_links_are_refused(
        self, tmp_path: Path, git_environment: Environment, repository: Repository
    ) -> None:
        """A link to a local secret fails the refresh; the secret is never read."""
        secret = tmp_path / "secret.txt"
        secret.write_text("TOP-SECRET", encoding="utf-8")
        source = _source(git_environment, repository)
        RemoteCache(git_environment).checkout(source)
        link = repository.work / "library" / "skills" / "hello" / "leak.txt"
        link.symlink_to(secret)
        repository.commit({})
        reader = SourceReader(remotes=RemoteCache(git_environment))

        items = reader.read(source)

        state = RemoteCache(git_environment).state(source)
        assert state.category == "content"
        assert "skills/hello/leak.txt" in str(state.error)
        assert "Using cached commit" in reader.warnings["remote"]
        contents = [asset.content for item in items for asset in item.assets]
        assert b"TOP-SECRET" not in contents
        assert not list(source.path.glob("*.partial"))

    @pytest.mark.skipif(sys.platform == "win32", reason="links need privileges")
    def test_linked_subdirectory_is_refused(
        self, tmp_path: Path, git_environment: Environment
    ) -> None:
        """A configured subdirectory that is a link cannot escape the checkout."""
        outside = tmp_path / "outside"
        (outside / "skills" / "x").mkdir(parents=True)
        repo = Repository(tmp_path / "linked", git_environment.variables)
        (repo.work / "library").symlink_to(outside, target_is_directory=True)
        repo.commit({"README.md": "x"})
        source = _source(git_environment, repo)

        with pytest.raises(SourceError, match="symbolic link"):
            SourceReader(remotes=RemoteCache(git_environment)).read(source)


class TestFailures:
    """Refresh failures keep serving the last good checkout."""

    def test_failure_with_cache_serves_it_and_warns(
        self, git_environment: Environment, repository: Repository
    ) -> None:
        """An unreachable remote falls back to the cached checkout."""
        source = _source(git_environment, repository)
        RemoteCache(git_environment).checkout(source)
        repository.remote.rename(repository.remote.with_name("moved.git"))
        reader = SourceReader(remotes=RemoteCache(git_environment))

        items = reader.read(source)

        assert _names(items) == ["hello"]
        assert "Using cached commit" in reader.warnings["remote"]
        state = RemoteCache(git_environment).state(source)
        assert (state.failing, state.failures) == (True, 1)
        assert state.error is not None
        assert "git ls-remote failed" in state.error

    def test_consecutive_failures_count_and_recovery_resets(
        self, git_environment: Environment, repository: Repository
    ) -> None:
        """Failures accumulate until a refresh succeeds again."""
        source = _source(git_environment, repository)
        RemoteCache(git_environment).checkout(source)
        moved = repository.remote.with_name("moved.git")
        repository.remote.rename(moved)
        RemoteCache(git_environment).checkout(source)
        RemoteCache(git_environment).checkout(source)
        assert RemoteCache(git_environment).state(source).failures == 2

        moved.rename(repository.remote)
        checkout = RemoteCache(git_environment).checkout(source)

        state = RemoteCache(git_environment).state(source)
        assert (checkout.warning, state.failures, state.error) == (None, 0, None)

    def test_failure_without_cache_fails_the_source(
        self, git_environment: Environment, repository: Repository
    ) -> None:
        """With nothing cached, the source fails and status records why."""
        source = _source(git_environment, repository, ref="missing")
        reader = SourceReader(remotes=RemoteCache(git_environment))

        with pytest.raises(SourceError, match="ref 'missing' was not found"):
            reader.read(source)

        assert RemoteCache(git_environment).state(source).category == "ref"

    def test_failing_refresh_is_retried_despite_the_interval(
        self, git_environment: Environment, repository: Repository
    ) -> None:
        """A failed refresh does not wait out ``refresh`` before retrying."""
        source = _source(git_environment, repository, refresh="1h")
        RemoteCache(git_environment).checkout(source)
        state = RemoteCache(git_environment).state(source)
        RemoteCache._save(source.path, replace(state, failures=1))
        runner = _recorder()

        RemoteCache(git_environment, runner).checkout(source)

        assert runner.verbs() == ["ls-remote"]

    @pytest.mark.parametrize(
        ("result", "category", "hint"),
        [
            (Ran(127, "", "No such file"), Category.GIT_MISSING, "install Git"),
            (Ran(124, "", "timed out after 60 seconds"), Category.TIMEOUT, "network"),
            (
                Ran(
                    128, "", "fatal: could not read Username: terminal prompts disabled"
                ),
                Category.AUTH,
                "credential helper",
            ),
            (
                Ran(128, "", "fatal: Could not resolve host: example.com"),
                Category.NETWORK,
                "network",
            ),
            (
                Ran(128, "", "fatal: couldn't find remote ref nope"),
                Category.REF,
                "'ref' setting",
            ),
            (Ran(1, "", "fatal: something odd"), Category.OTHER, "by hand"),
            (Ran(1, "", ""), Category.OTHER, "by hand"),
        ],
    )
    def test_failures_are_categorized(
        self,
        git_environment: Environment,
        repository: Repository,
        result: Ran,
        category: Category,
        hint: str,
    ) -> None:
        """Each failure records a category and a remedy."""
        source = _source(git_environment, repository)
        cache = RemoteCache(git_environment, _recorder({"ls-remote": result}))

        with pytest.raises(SourceError, match=hint):
            cache.checkout(source)

        state = cache.state(source)
        assert (state.category, state.hint is not None) == (category.value, True)

    def test_github_auth_failure_names_token_remedies(self) -> None:
        """GitHub authentication hints name the tokens and gh."""
        remote = RemoteConfig("https://github.com/a/b.git", "a/b", github=True)

        hint = RemoteCache._hint(Category.AUTH, remote)

        assert "GH_TOKEN" in hint
        assert "gh auth status" in hint
        assert RemoteCache._categorize("remote: Repository not found.") is Category.AUTH
        assert RemoteCache._categorize("fatal: couldn't find remote ref x") is (
            Category.REF
        )

    def test_locked_cache_serves_the_cached_copy(
        self, git_environment: Environment, repository: Repository
    ) -> None:
        """A refresh in progress elsewhere means reading the current checkout."""
        source = _source(git_environment, repository)
        RemoteCache(git_environment).checkout(source)
        runner = _recorder()

        with StateLock(source.path / "refresh.lock"):
            checkout = RemoteCache(git_environment, runner).checkout(source)

        assert runner.calls == []
        assert checkout.warning is None

    def test_locked_cache_without_checkout_fails(
        self, git_environment: Environment, repository: Repository
    ) -> None:
        """With nothing cached, a held lock fails the source."""
        source = _source(git_environment, repository)

        with (
            StateLock(source.path / "refresh.lock"),
            pytest.raises(SourceError, match="in progress"),
        ):
            RemoteCache(git_environment).checkout(source)

    @pytest.mark.parametrize(
        "content",
        [
            "not json",
            json.dumps([1]),
            json.dumps({"version": 99}),
            json.dumps({"version": 1, "url": "elsewhere"}),
            json.dumps({"version": 1, "unexpected": 1}),
            json.dumps({"version": 1, "url": 5, "failures": "x", "bogus": 1}),
            json.dumps({"version": 1, "url": "u", "commit": None, "tree": None}),
            json.dumps({"version": 1, "url": "u", "retired": {"tree-x": 5}}),
        ],
    )
    def test_unusable_state_is_ignored(
        self, git_environment: Environment, repository: Repository, content: str
    ) -> None:
        """A corrupt or foreign state file starts the cache afresh."""
        source = _source(git_environment, repository)
        source.path.mkdir(parents=True)
        (source.path / STATE_FILE).write_text(content, encoding="utf-8")
        cache = RemoteCache(git_environment)

        state = cache.state(source)

        assert state.commit is None
        assert _names(SourceReader(remotes=cache).read(source)) == ["hello"]

    def test_reader_without_cache_rejects_remote_sources(
        self, git_environment: Environment, repository: Repository
    ) -> None:
        """A reader built without a cache cannot read a remote source."""
        source = _source(git_environment, repository)

        with pytest.raises(SourceError, match="not available"):
            SourceReader().read(source)

    def test_local_sources_are_not_remote(self, tmp_path: Path) -> None:
        """Asking the cache about a local source is a programming error."""
        source = SourceConfig("local", tmp_path, {kind: kind.value for kind in Kind})

        with pytest.raises(ValueError, match="not a remote source"):
            RemoteCache(Environment(tmp_path, {}, sys.platform)).state(source)


class TestAuthentication:
    """Tokens reach Git through its environment, never its command line."""

    GITHUB = RemoteConfig("https://github.com/a/b.git", "a/b", github=True)

    def _cache(
        self, variables: Mapping[str, str], gh: Ran | None = None
    ) -> tuple[RemoteCache, Recorder]:
        runner = _recorder({"auth": gh or Ran(1, "", "not logged in")})
        environment = Environment(Path(), dict(variables), sys.platform)
        return RemoteCache(environment, runner), runner

    def test_environment_token_is_an_extra_header(self) -> None:
        """GH_TOKEN becomes a Basic header appended to existing GIT_CONFIG_*."""
        cache, runner = self._cache(
            {"GH_TOKEN": "secret", "GIT_CONFIG_COUNT": "1", "PATH": "x"}
        )

        variables = cache._variables(self.GITHUB)

        assert variables["GIT_CONFIG_COUNT"] == "2"
        assert variables["GIT_CONFIG_KEY_1"] == "http.https://github.com/.extraHeader"
        assert variables["GIT_CONFIG_VALUE_1"].startswith("Authorization: Basic ")
        assert variables["GIT_TERMINAL_PROMPT"] == "0"
        assert runner.calls == []

    def test_github_token_variable_is_second_choice(self) -> None:
        """GITHUB_TOKEN works when GH_TOKEN is absent; a bad count restarts at 0."""
        cache, _ = self._cache({"GITHUB_TOKEN": "t", "GIT_CONFIG_COUNT": "x"})

        variables = cache._variables(self.GITHUB)

        assert variables["GIT_CONFIG_COUNT"] == "1"
        assert "GIT_CONFIG_KEY_0" in variables

    def test_gh_cli_token_is_looked_up_once(self) -> None:
        """Without variables, ``gh auth token`` supplies the token, asked once."""
        cache, runner = self._cache({}, Ran(0, "gho_abc\n"))

        cache._variables(self.GITHUB)
        variables = cache._variables(self.GITHUB)

        assert runner.calls == [["gh", "auth", "token", "--hostname", "github.com"]]
        assert variables["GIT_CONFIG_COUNT"] == "1"

    def test_no_token_leaves_git_to_its_credential_helpers(self) -> None:
        """When gh is absent or signed out, no header is added."""
        cache, _ = self._cache({})

        assert "GIT_CONFIG_COUNT" not in cache._variables(self.GITHUB)

    def test_other_hosts_never_receive_the_token(self) -> None:
        """Git sources, and github sources with another URL, get no header."""
        cache, runner = self._cache({"GH_TOKEN": "secret"})

        plain = cache._variables(RemoteConfig("https://example.com/r.git", "r"))
        moved = cache._variables(replace(self.GITHUB, url="https://example.com/r"))

        assert "GIT_CONFIG_COUNT" not in plain
        assert "GIT_CONFIG_COUNT" not in moved
        assert runner.calls == []

    def test_token_never_reaches_argv(
        self, git_environment: Environment, repository: Repository
    ) -> None:
        """Every Git command line is free of the token."""
        source = _source(git_environment, repository)
        remote = replace(source.remote, github=True) if source.remote else None
        source = replace(source, remote=remote)
        variables = {**git_environment.variables, "GH_TOKEN": "s3cret"}
        runner = _recorder()

        RemoteCache(replace(git_environment, variables=variables), runner).checkout(
            source
        )

        assert all("s3cret" not in " ".join(call) for call in runner.calls)

    def test_mask_hides_url_credentials(self) -> None:
        """Credentials in URLs inside error text are masked."""
        assert (
            mask("fatal: https://me:tok@host/r.git not found")
            == "fatal: https://***@host/r.git not found"
        )
        assert mask("plain https://host/r.git") == "plain https://host/r.git"
        assert mask("at https://TOKEN@host/r.git") == "at https://***@host/r.git"
        assert mask("ssh://git@host/r.git") == "ssh://git@host/r.git"
        assert mask("ssh://git:pw@host/r.git") == "ssh://***@host/r.git"

    def test_persisted_state_and_status_never_hold_url_credentials(
        self, git_environment: Environment, repository: Repository
    ) -> None:
        """Even a URL that bypassed validation is masked in state and status."""
        source = _source(git_environment, repository)
        assert source.remote is not None
        leaky = replace(
            source,
            remote=replace(source.remote, url="https://TOKEN9@127.0.0.1:9/r.git"),
        )
        cache = RemoteCache(git_environment)

        with pytest.raises(SourceError) as caught:
            cache.checkout(leaky)

        stored = (leaky.path / STATE_FILE).read_text(encoding="utf-8")
        assert "TOKEN9" not in stored
        assert "TOKEN9" not in str(caught.value)
        assert cache.state(leaky).failures == 1


class TestJourneys:
    """Plan, sync, and status through the application."""

    def test_sync_status_and_failure_journey(
        self, git_environment: Environment, repository: Repository
    ) -> None:
        """Sync delivers remote skills; a failing remote keeps them and exits 1."""
        _config(git_environment, repository)
        dest = git_environment.home / "dest" / "skills"

        status, _ = _run(git_environment, "sync")
        assert status == 0
        assert (dest / "hello" / "SKILL.md").is_file()

        status, report = _run(git_environment, "status")
        assert status == 0
        assert "state: ok" in report

        repository.remote.rename(repository.remote.with_name("moved.git"))
        status, report = _run(git_environment, "sync", "--json")
        data = json.loads(report)
        assert status == 1
        assert data["ok"] is False
        assert "remote" in data["source_warnings"]
        assert (dest / "hello" / "SKILL.md").is_file()

        status, report = _run(git_environment, "sync")
        assert status == 1
        assert report.startswith("warning: source 'remote'")

        status, report = _run(git_environment, "status")
        assert status == 1
        assert "failing (2 failed refreshes; serving the cached copy)" in report
        assert "Needs attention: remote." in report

    def test_failure_before_first_fetch_leaves_destinations_alone(
        self, git_environment: Environment, repository: Repository
    ) -> None:
        """Without a cached copy, the destination is not touched."""
        _config(git_environment, repository)
        repository.remote.rename(repository.remote.with_name("moved.git"))

        status, report = _run(git_environment, "sync", "--json")

        data = json.loads(report)
        assert status == 1
        assert "remote" in data["source_errors"]
        assert not (git_environment.home / "dest").exists()

        status, report = _run(git_environment, "status", "--json")
        data = json.loads(report)
        assert status == 1
        entry = data["sources"][0]
        assert (entry["state"], entry["healthy"], entry["library"]) == (
            "failing",
            False,
            None,
        )

        status, report = _run(git_environment, "status")
        assert "failing (1 failed refresh; nothing cached yet)" in report

    def test_status_lists_local_and_unfetched_sources(
        self, git_environment: Environment, tmp_path: Path
    ) -> None:
        """Status needs no network and reports local and remote sources."""
        path = git_environment.config_home / "skillsinc" / "config.json"
        path.parent.mkdir(parents=True)
        data = {
            "version": 1,
            "sources": {
                "here": {"path": str(tmp_path)},
                "gone": {"path": str(tmp_path / "missing")},
                "far": {"github": "acme/skills", "ref": "main"},
            },
        }
        path.write_text(json.dumps(data), encoding="utf-8")

        status, report = _run(git_environment, "status")

        assert status == 1
        assert "here: local" in report
        assert "state: directory not found" in report
        assert "far: github acme/skills (ref main)" in report
        assert "not fetched yet" in report
        assert "Needs attention: gone." in report

    def test_status_with_no_sources(self, git_environment: Environment) -> None:
        """An empty configuration reports nothing to check."""
        path = git_environment.config_home / "skillsinc" / "config.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"version": 1}', encoding="utf-8")

        status, report = _run(git_environment, "status")

        assert status == 0
        assert "No sources are configured." in report
        assert "All sources are healthy." in report

    def test_remote_state_records_round_trip(self) -> None:
        """State is plain data."""
        assert not RemoteState(url="u").failing
        assert RemoteState(url="u", failures=2).failing
