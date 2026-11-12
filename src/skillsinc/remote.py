"""Keep a local, last-known-good checkout of each remote Git source (decision 0019).

A refresh costs one ``git ls-remote`` when nothing changed. A new commit is
fetched shallowly into a persistent bare repository and checked out into a
fresh directory; the cache's ``state.json`` moves to it only once the
checkout is complete, so an interrupted or failed refresh always leaves the
previous checkout readable.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import stat
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from skillsinc.config import COMMIT, RemoteConfig, SourceConfig
from skillsinc.exceptions import LockError, SourceError
from skillsinc.lock import StateLock
from skillsinc.paths import Environment

logger = logging.getLogger(__name__)

STATE_FILE: Final = "state.json"
TREE_PREFIX: Final = "tree-"
BARE: Final = "git"
CURRENT_REF: Final = "refs/skillsinc/current"
STATE_VERSION: Final = 1

# Seconds; a scheduled run must never hang on the network
CHECK_TIMEOUT: Final = 60
FETCH_TIMEOUT: Final = 600
LOCAL_TIMEOUT: Final = 120
GH_TIMEOUT: Final = 15
# A refresh lock older than this belongs to a crashed run
LOCK_STALE_SECONDS: Final = 2 * FETCH_TIMEOUT
MISSING_PROGRAM: Final = 127
TIMED_OUT: Final = 124

TOKEN_VARIABLES: Final = ("GH_TOKEN", "GITHUB_TOKEN")
GITHUB_PREFIX: Final = "https://github.com/"
QUIET_GIT: Final = {
    # Never wait for a password or a credential manager window
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "never",
    # Give up on a stalled transfer instead of waiting for the timeout
    "GIT_HTTP_LOW_SPEED_LIMIT": "1000",
    "GIT_HTTP_LOW_SPEED_TIME": "30",
}


class Category(StrEnum):
    """Why a refresh failed, so status output can name a remedy."""

    AUTH = "auth"
    NETWORK = "network"
    REF = "ref"
    GIT_MISSING = "git-missing"
    TIMEOUT = "timeout"
    OTHER = "other"


# Lowercase stderr fragments, checked in order; authentication comes first
# because Git also says "unable to access" when a server refuses credentials
SIGNATURES: Final = (
    (
        Category.AUTH,
        (
            "authentication failed",
            "could not read username",
            "could not read password",
            "terminal prompts disabled",
            "repository not found",
            "invalid username or password",
            "permission denied",
            "host key verification failed",
            "returned error: 401",
            "returned error: 403",
            "the requested url returned error: 404",
        ),
    ),
    (Category.REF, ("couldn't find remote ref", "not our ref")),
    (
        Category.NETWORK,
        (
            "could not resolve",
            "failed to connect",
            "connection timed out",
            "connection refused",
            "connection reset",
            "network is unreachable",
            "operation timed out",
            "ssl",
            "tls",
            "proxy",
            "unable to access",
            "early eof",
            "the remote end hung up",
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class Ran:
    """A finished Git (or ``gh``) command."""

    code: int
    stdout: str = ""
    stderr: str = ""


class GitRunner:
    """Run commands with a timeout; tests may substitute a fake."""

    def run(
        self,
        argv: Sequence[str],
        environment: Mapping[str, str],
        timeout: float,
    ) -> Ran:
        """Run ``argv``; a missing program exits 127 and a timeout exits 124."""
        try:
            completed = subprocess.run(  # noqa: S603 - argv is built here, never a shell
                list(argv),
                env=dict(environment),
                capture_output=True,
                stdin=subprocess.DEVNULL,
                check=False,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except FileNotFoundError as err:
            return Ran(MISSING_PROGRAM, "", str(err))
        except subprocess.TimeoutExpired:
            return Ran(TIMED_OUT, "", f"timed out after {timeout:g} seconds")
        return Ran(completed.returncode, completed.stdout, completed.stderr)


@dataclass(frozen=True, slots=True)
class RemoteState:
    """What a remote source's cache holds, persisted as ``state.json``."""

    url: str
    ref: str | None = None
    commit: str | None = None
    tree: str | None = None
    checked_at: str | None = None
    """When the remote was last contacted, successfully or not."""
    succeeded_at: str | None = None
    """When a refresh last confirmed the cached commit is current."""
    updated_at: str | None = None
    """When the cached commit last changed."""
    failures: int = 0
    """Consecutive failed refreshes; zero once one succeeds."""
    error: str | None = None
    category: str | None = None
    hint: str | None = None

    @property
    def failing(self) -> bool:
        """Return True if the most recent refresh failed."""
        return self.failures > 0


class RefreshError(Exception):
    """A refresh failed; carries a category and a remedy for status output."""

    def __init__(self, message: str, category: Category, hint: str) -> None:
        super().__init__(message)
        self.category = category
        self.hint = hint


@dataclass(frozen=True, slots=True)
class Checkout:
    """The directory to read for a remote source, and any warning about it."""

    path: Path
    warning: str | None = None


class RemoteCache:
    """Refresh remote sources into the state directory and report on them."""

    def __init__(
        self,
        environment: Environment,
        runner: GitRunner | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._environment = environment
        self._runner = runner or GitRunner()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._token: str | None = None
        self._token_looked_up = False

    # Public API

    def checkout(self, source: SourceConfig) -> Checkout:
        """Return the library directory to read for ``source``.

        Refreshes the cache when due. When a refresh fails, the last good
        checkout is returned with a warning.

        Raises:
            SourceError: If no checkout is available at all.
        """
        remote = self._remote(source)
        cache = source.path
        state = self.state(source)
        cached = self._tree(cache, state)
        if cached is not None and not self._due(remote, state):
            logger.debug("source '%s': using cached %s", source.name, state.commit)
            return Checkout(self._library(cached, remote))
        try:
            with StateLock(cache / "refresh.lock", stale_after=LOCK_STALE_SECONDS):
                return self._refresh(source, remote, state, cached)
        except LockError as err:
            if cached is None:
                msg = f"source '{source.name}': {err}"
                raise SourceError(msg) from err
            logger.info("source '%s': %s; using the cached copy", source.name, err)
            return Checkout(self._library(cached, remote))

    def state(self, source: SourceConfig) -> RemoteState:
        """Return the stored state of ``source``'s cache (empty when absent)."""
        remote = self._remote(source)
        empty = RemoteState(url=remote.url, ref=remote.ref)
        try:
            data = json.loads((source.path / STATE_FILE).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return empty
        except (OSError, ValueError) as err:
            logger.warning(
                "source '%s': ignoring unreadable cache state (%s)", source.name, err
            )
            return empty
        if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
            return empty
        fields = set(RemoteState.__dataclass_fields__)
        try:
            state = RemoteState(**{k: v for k, v in data.items() if k in fields})
        except TypeError:
            return empty
        # A cache written for another URL or ref is not this source's
        if state.url != remote.url or state.ref != remote.ref:
            return empty
        return state

    def library(self, source: SourceConfig) -> Path | None:
        """Return the cached library directory without refreshing, if any."""
        remote = self._remote(source)
        tree = self._tree(source.path, self.state(source))
        return None if tree is None else self._library(tree, remote)

    # Refreshing

    def _refresh(
        self,
        source: SourceConfig,
        remote: RemoteConfig,
        state: RemoteState,
        cached: Path | None,
    ) -> Checkout:
        """Check the remote and update the cache; fall back to ``cached``."""
        cache = source.path
        now = self._now()
        try:
            commit, wanted = self._resolve(remote)
            if cached is None or commit != state.commit:
                commit, tree = self._update(cache, remote, wanted)
                state = replace(state, commit=commit, tree=tree, updated_at=now)
                cached = cache / tree
        except RefreshError as err:
            state = replace(
                state,
                checked_at=now,
                failures=state.failures + 1,
                error=str(err),
                category=err.category.value,
                hint=err.hint,
            )
            self._save(cache, state)
            message = f"source '{source.name}' ({remote.label}): {err}; {err.hint}"
            if cached is None:
                raise SourceError(message) from err
            warning = (
                f"{message}. Using cached commit {str(state.commit)[:12]}"
                f" from {state.updated_at or 'an earlier run'}"
            )
            logger.info("%s", warning)
            return Checkout(self._library(cached, remote), warning)
        state = replace(
            state,
            checked_at=now,
            succeeded_at=now,
            failures=0,
            error=None,
            category=None,
            hint=None,
        )
        self._save(cache, state)
        self._prune(cache, keep=str(state.tree))
        return Checkout(self._library(cached, remote))

    def _resolve(self, remote: RemoteConfig) -> tuple[str, str]:
        """Return the commit ``remote.ref`` names now and the ref to fetch it by.

        Costs one ``ls-remote``, or nothing for a pinned commit.
        """
        if remote.pinned:
            return str(remote.ref), str(remote.ref)
        pattern = remote.ref or "HEAD"
        # Peeled tags (``^{}``) match only a pattern that names them
        listed = self._git(
            remote,
            ["ls-remote", "--", remote.url, pattern, f"{pattern}^{{}}"],
            CHECK_TIMEOUT,
        )
        refs: dict[str, str] = {}
        for line in listed.splitlines():
            commit, _, name = line.partition("\t")
            if COMMIT.match(commit) and name:
                refs[name] = commit
        for name in self._candidates(remote.ref):
            if name in refs:
                return refs[name], name.removesuffix("^{}")
        msg = f"ref '{pattern}' was not found in {remote.label}"
        raise RefreshError(msg, Category.REF, "check the source's 'ref' setting")

    @staticmethod
    def _candidates(ref: str | None) -> tuple[str, ...]:
        """Return the full ref names ``ref`` may mean, best first; tags peeled."""
        if ref is None:
            return ("HEAD",)
        if ref.startswith("refs/"):
            return (f"{ref}^{{}}", ref)
        return (f"refs/heads/{ref}", f"refs/tags/{ref}^{{}}", f"refs/tags/{ref}")

    def _update(
        self, cache: Path, remote: RemoteConfig, wanted: str
    ) -> tuple[str, str]:
        """Fetch ``wanted`` shallowly and check it out; return the commit and tree."""
        bare = cache / BARE
        if not (bare / "HEAD").is_file():
            cache.mkdir(parents=True, exist_ok=True)
            self._git(remote, ["init", "--bare", "--quiet", str(bare)], LOCAL_TIMEOUT)
        self._git(
            remote,
            [
                f"--git-dir={bare}",
                "fetch",
                "--quiet",
                "--no-tags",
                "--depth=1",
                "--",
                remote.url,
                f"+{wanted}:{CURRENT_REF}",
            ],
            FETCH_TIMEOUT,
        )
        fetched = self._git(
            remote,
            [f"--git-dir={bare}", "rev-parse", "--verify", f"{CURRENT_REF}^{{commit}}"],
            LOCAL_TIMEOUT,
        ).strip()
        tree = f"{TREE_PREFIX}{fetched[:12]}"
        staging = cache / f"{tree}.partial"
        self._remove(staging)
        staging.mkdir(parents=True)
        self._git(
            remote,
            [
                f"--git-dir={bare}",
                f"--work-tree={staging}",
                "checkout",
                "--force",
                "--quiet",
                fetched,
                "--",
                ".",
            ],
            LOCAL_TIMEOUT,
        )
        target = cache / tree
        self._remove(target)
        staging.replace(target)
        return fetched, tree

    # Git

    def _git(self, remote: RemoteConfig, arguments: list[str], timeout: float) -> str:
        """Run Git non-interactively and return stdout.

        Raises:
            RefreshError: If Git is missing, times out, or fails.
        """
        ran = self._runner.run(["git", *arguments], self._variables(remote), timeout)
        if ran.code == 0:
            return ran.stdout
        if ran.code == MISSING_PROGRAM:
            msg = "git is not installed or not on PATH"
            raise RefreshError(msg, Category.GIT_MISSING, "install Git")
        if ran.code == TIMED_OUT:
            msg = f"git {arguments[0]} {ran.stderr}"
            raise RefreshError(
                msg, Category.TIMEOUT, "check the network; the remote did not answer"
            )
        detail = self._detail(ran.stderr)
        category = self._categorize(ran.stderr)
        msg = f"git {self._verb(arguments)} failed: {detail}"
        raise RefreshError(msg, category, self._hint(category, remote))

    def _variables(self, remote: RemoteConfig) -> dict[str, str]:
        """Return Git's environment: never prompt; a GitHub token if one is set.

        The token travels as environment configuration (``GIT_CONFIG_*``),
        so it is never on a command line or saved in the cached repository.
        """
        variables = {**self._environment.variables, **QUIET_GIT}
        token = self._github_token() if remote.github else None
        if token is None or not remote.url.startswith(GITHUB_PREFIX):
            return variables
        try:
            count = int(variables.get("GIT_CONFIG_COUNT", "0"))
        except ValueError:
            count = 0
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        variables[f"GIT_CONFIG_KEY_{count}"] = f"http.{GITHUB_PREFIX}.extraHeader"
        variables[f"GIT_CONFIG_VALUE_{count}"] = f"Authorization: Basic {basic}"
        variables["GIT_CONFIG_COUNT"] = str(count + 1)
        return variables

    def _github_token(self) -> str | None:
        """Return a token from GH_TOKEN, GITHUB_TOKEN, or ``gh auth token``."""
        if self._token_looked_up:
            return self._token
        self._token_looked_up = True
        for name in TOKEN_VARIABLES:
            value = self._environment.variables.get(name, "").strip()
            if value:
                self._token = value
                return value
        ran = self._runner.run(
            ["gh", "auth", "token", "--hostname", "github.com"],
            self._environment.variables,
            GH_TIMEOUT,
        )
        token = ran.stdout.strip()
        if ran.code == 0 and token and not any(char.isspace() for char in token):
            self._token = token
        return self._token

    @staticmethod
    def _verb(arguments: Sequence[str]) -> str:
        """Return the Git subcommand among ``arguments``."""
        return next(arg for arg in arguments if not arg.startswith("-"))

    @staticmethod
    def _detail(stderr: str) -> str:
        """Return Git's message, with any credentials in URLs masked."""
        lines = [line.strip() for line in stderr.splitlines() if line.strip()]
        fatal = [line for line in lines if line.lower().startswith(("fatal", "error"))]
        text = " ".join((fatal or lines)[-2:]) or "no error output"
        return mask(text)

    @staticmethod
    def _categorize(stderr: str) -> Category:
        """Classify Git's error output."""
        lowered = stderr.lower()
        for category, fragments in SIGNATURES:
            if any(fragment in lowered for fragment in fragments):
                return category
        return Category.OTHER

    @staticmethod
    def _hint(category: Category, remote: RemoteConfig) -> str:
        """Return what the user can do about a failure."""
        if category is Category.AUTH and remote.github:
            return (
                "check access: set GH_TOKEN or GITHUB_TOKEN, or sign in with"
                " `gh auth login` (see `gh auth status`); GitHub reports a private"
                " repository it will not show you as not found"
            )
        if category is Category.AUTH:
            return (
                "check the Git credential helper or SSH key for this host;"
                " SkillSinc never prompts for credentials"
            )
        if category is Category.REF:
            return "check the source's 'ref' setting"
        if category is Category.NETWORK:
            return "check the network connection or proxy"
        return "run the Git command by hand to see the full error"

    # Cache files

    def _due(self, remote: RemoteConfig, state: RemoteState) -> bool:
        """Return True if the remote should be contacted on this run."""
        if remote.pinned:
            return state.commit != remote.ref
        if not remote.refresh or state.checked_at is None or state.failing:
            return True
        try:
            checked = datetime.fromisoformat(state.checked_at)
        except ValueError:
            return True
        return self._clock() - checked >= remote.refresh

    @staticmethod
    def _tree(cache: Path, state: RemoteState) -> Path | None:
        """Return the cached checkout ``state`` names, if it is on disk."""
        if state.tree is None or not state.tree.startswith(TREE_PREFIX):
            return None
        tree = cache / state.tree
        return tree if tree.is_dir() else None

    @staticmethod
    def _library(tree: Path, remote: RemoteConfig) -> Path:
        """Return the library directory inside a checkout."""
        return tree / remote.subdirectory if remote.subdirectory else tree

    @staticmethod
    def _save(cache: Path, state: RemoteState) -> None:
        """Replace ``state.json`` atomically."""
        cache.mkdir(parents=True, exist_ok=True)
        staging = cache / f"{STATE_FILE}.tmp"
        data = {"version": STATE_VERSION, **asdict(state)}
        staging.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        staging.replace(cache / STATE_FILE)

    def _prune(self, cache: Path, keep: str) -> None:
        """Remove checkouts other than ``keep``, including interrupted ones."""
        for entry in cache.iterdir():
            if entry.name.startswith(TREE_PREFIX) and entry.name != keep:
                try:
                    self._remove(entry)
                except OSError as err:
                    logger.debug("could not remove %s: %s", entry, err)

    @classmethod
    def _remove(cls, path: Path) -> None:
        """Delete a directory tree, clearing read-only flags Windows enforces."""
        if path.exists():
            shutil.rmtree(path, onexc=cls._writable_retry)

    @staticmethod
    def _writable_retry(
        function: Callable[[str], object], name: str, _: BaseException
    ) -> None:
        """Make ``name`` writable and repeat the failed removal once."""
        os.chmod(name, stat.S_IWRITE)  # noqa: PTH101 - rmtree passes a str
        function(name)

    def _now(self) -> str:
        """Return the current time as ISO 8601."""
        return self._clock().astimezone().isoformat(timespec="seconds")

    @staticmethod
    def _remote(source: SourceConfig) -> RemoteConfig:
        """Return ``source``'s remote settings."""
        if source.remote is None:
            msg = f"source '{source.name}' is not a remote source"
            raise ValueError(msg)
        return source.remote


def mask(text: str) -> str:
    """Hide the credentials part of any ``scheme://user:secret@`` in ``text``."""
    words = []
    for word in text.split(" "):
        scheme, separator, rest = word.partition("://")
        if separator and "@" in rest.split("/", 1)[0]:
            rest = "***@" + rest.split("@", 1)[1]
        words.append(scheme + separator + rest if separator else word)
    return " ".join(words)
