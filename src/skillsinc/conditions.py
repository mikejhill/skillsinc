"""Sync profile definitions and the conditions that activate them by default.

Kept apart from ``selection`` so ``config`` can build these values without
importing the module that narrows a ``Config``.
"""

from __future__ import annotations

import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from skillsinc.paths import Environment

SYSTEMS: Final = ("windows", "macos", "linux")


def system_name(platform: str) -> str:
    """Return ``windows``, ``macos``, or ``linux`` for a ``sys.platform`` value."""
    if platform == "win32":
        return "windows"
    if platform == "darwin":
        return "macos"
    if platform.startswith("linux"):
        return "linux"
    return platform


def host_name(environment: Environment) -> str:
    """Return the machine's host name (``SKILLSINC_HOSTNAME`` overrides it)."""
    return environment.variables.get("SKILLSINC_HOSTNAME", "") or socket.gethostname()


def variable(environment: Environment, name: str) -> str:
    """Return a variable's value, or empty; names ignore case on Windows."""
    value = environment.variables.get(name)
    if value is None and environment.platform == "win32":
        folded = name.upper()
        value = next(
            (
                found
                for key, found in environment.variables.items()
                if key.upper() == folded
            ),
            None,
        )
    return value or ""


@dataclass(frozen=True, slots=True)
class Condition:
    """When a profile applies by default; every listed key must match.

    Within ``hosts`` and ``systems`` any one entry matching is enough.
    """

    hosts: tuple[str, ...] = ()
    """Case-insensitive host name globs, matched against the full and short name."""
    systems: tuple[str, ...] = ()
    """``windows``, ``macos``, or ``linux``."""
    variables: Mapping[str, str | bool] = field(default_factory=dict[str, "str | bool"])
    """Variable name to a value glob, ``true`` (set and non-empty), or ``false``."""

    def failures(self, environment: Environment) -> list[str]:
        """Return why the condition does not hold here, or nothing when it does."""
        failed: list[str] = []
        if self.hosts:
            host = host_name(environment).lower()
            names = {host, host.split(".", 1)[0]}
            if not any(
                fnmatchcase(name, pattern.lower())
                for name in names
                for pattern in self.hosts
            ):
                failed.append(f"host {host} is not {' or '.join(self.hosts)}")
        if self.systems:
            system = system_name(environment.platform)
            if system not in self.systems:
                failed.append(f"system {system} is not {' or '.join(self.systems)}")
        for name, expected in self.variables.items():
            value = variable(environment, name)
            if expected is True:
                held = bool(value)
                wanted = "set"
            elif expected is False:
                held = not value
                wanted = "unset"
            else:
                held = bool(value) and fnmatchcase(value, expected)
                wanted = f"{expected!r}"
            if not held:
                failed.append(f"{name} is not {wanted}")
        return failed


@dataclass(frozen=True, slots=True)
class SyncProfile:
    """One named segment of the configuration."""

    name: str
    description: str = ""
    default: bool = True
    """False leaves the profile out unless it is selected explicitly."""
    when: Condition | None = None
