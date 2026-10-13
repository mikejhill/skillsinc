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
        if raw == "~" or raw.startswith(("~/", "~\\")):
            raw = str(self.home) + raw[1:]
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
        return self.config_home / "skillsinc" / "config.toml"

    @property
    def state_directory(self) -> Path:
        """Return the directory holding ownership manifests and the run lock."""
        return self.state_home / "skillsinc"
