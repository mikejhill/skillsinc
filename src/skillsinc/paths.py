"""User environment: home directory, environment variables, and storage roots."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

PROJECT_CONFIG: Final = Path(".config") / "skillsinc"
"""Where a project keeps its configuration, relative to the project root."""
PROJECT_STATE: Final = PROJECT_CONFIG / "state"
"""Where a project keeps its manifests and run lock, relative to its root."""
CONFIG_NAMES: Final = ("config.json", "config.jsonc")


def absolute(path: Path) -> Path:
    """Return ``path`` absolute and normalized, without resolving links."""
    return Path(os.path.normpath(path.absolute()))


@dataclass(frozen=True, slots=True)
class Environment:
    """The parts of the process environment SkillSinc depends on."""

    home: Path
    variables: Mapping[str, str]
    platform: str
    cwd: Path | None = None
    """The working directory projects are discovered from; None is the process's."""

    @classmethod
    def current(cls) -> Environment:
        """Capture the running process's environment."""
        return cls(home=Path.home(), variables=dict(os.environ), platform=sys.platform)

    @property
    def working_directory(self) -> Path:
        """Return the absolute directory project discovery starts from."""
        return absolute(self.cwd or Path.cwd())

    def directory(self, variable: str, fallback: Path) -> Path:
        """Return the absolute path named by ``variable``, else ``fallback``."""
        value = self.variables.get(variable, "")
        if value and Path(value).is_absolute():
            return Path(value)
        return fallback

    def expand(self, raw: str, base: Path) -> Path:
        """Expand ``~`` against the home directory and resolve relative to ``base``."""
        if raw == "~":
            return self.home
        if raw.startswith(("~/", "~\\")):
            # Accept either separator so one config works on every platform
            return self.home / raw[2:].replace("\\", "/")
        path = Path(raw)
        return path if path.is_absolute() else base / path

    @property
    def config_home(self) -> Path:
        """Return the XDG configuration root (honoured on every platform)."""
        return self.directory("XDG_CONFIG_HOME", self.home / ".config")

    @property
    def state_home(self) -> Path:
        """Return the XDG state root (honoured on every platform)."""
        return self.directory("XDG_STATE_HOME", self.home / ".local" / "state")

    @property
    def default_config_file(self) -> Path:
        """Return the configuration file used when ``--config`` is not given.

        That is ``config.json`` (read as JSONC), or ``config.jsonc`` when only
        that one exists, as early builds wrote.
        """
        return self._config_in(self.config_home / "skillsinc")

    def is_user_config(self, path: Path) -> bool:
        """Return True if ``path`` is the user configuration file."""
        directory = self.config_home / "skillsinc"
        return any(
            absolute(path) == absolute(directory / name) for name in CONFIG_NAMES
        )

    def project_config(self) -> Path | None:
        """Return the nearest project configuration above the working directory.

        Each directory from the working directory upward is checked for
        ``.config/skillsinc/config.json`` (or ``config.jsonc``). The home
        directory is not a project: its ``.config/skillsinc`` is the user
        configuration, so the search stops below it.
        """
        home = absolute(self.home)
        for directory in (self.working_directory, *self.working_directory.parents):
            if directory == home:
                return None
            candidate = self._config_in(directory / PROJECT_CONFIG)
            if candidate.is_file() and not self.is_user_config(candidate):
                return candidate
        return None

    def config_file(self, *, user: bool = False) -> Path:
        """Return the configuration used when ``--config`` is not given.

        That is the nearest project configuration, unless ``user`` is set or
        there is none, in which case it is the user configuration.
        """
        project = None if user else self.project_config()
        return project or self.default_config_file

    @staticmethod
    def _config_in(directory: Path) -> Path:
        """Return ``config.json`` in ``directory``, or an early ``config.jsonc``."""
        preferred, early = directory / "config.json", directory / "config.jsonc"
        return early if early.is_file() and not preferred.exists() else preferred

    @property
    def default_library(self) -> Path:
        """Return the library written into the configuration by a bare ``init``."""
        return self.config_home / "skillsinc" / "library"

    @property
    def state_directory(self) -> Path:
        """Return the directory holding ownership manifests and the run lock."""
        return self.state_home / "skillsinc"

    @property
    def remote_cache(self) -> Path:
        """Return the directory holding one cache per remote source."""
        return self.state_directory / "remotes"


def destination_file(root: Path, path: str) -> Path:
    """Return the file at destination-relative ``path``.

    A leading ``..`` (a file a profile declares beside its root) is taken by
    name, because the operating system would resolve ``root/..`` through a
    linked ``root``; nothing else in ``root`` is normalized.
    """
    parts = Path(path).parts
    if parts[:1] == ("..",):
        return root.parent.joinpath(*parts[1:])
    return root / path
