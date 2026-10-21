"""User environment: home directory, environment variables, and storage roots."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Environment:
    """The parts of the process environment SkillSinc depends on."""

    home: Path
    variables: Mapping[str, str]
    platform: str

    @classmethod
    def current(cls) -> Environment:
        """Capture the running process's environment."""
        return cls(home=Path.home(), variables=dict(os.environ), platform=sys.platform)

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
        """Return the configuration file used when ``--config`` is not given."""
        return self.config_home / "skillsinc" / "config.jsonc"

    @property
    def default_library(self) -> Path:
        """Return the library written into the configuration by a bare ``init``."""
        return self.config_home / "skillsinc" / "library"

    @property
    def state_directory(self) -> Path:
        """Return the directory holding ownership manifests and the run lock."""
        return self.state_home / "skillsinc"


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
