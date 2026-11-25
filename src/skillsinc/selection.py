"""Sync profiles: named segments of a configuration that a machine applies.

A configuration may define ``profiles`` and tag routes with them. Routes
without ``profiles`` always apply. A tagged route applies when one of its
profiles is active. Which profiles are active comes from, in order:

1. ``--profile`` on the command line,
2. the ``SKILLSINC_PROFILES`` environment variable (comma-separated),
3. each profile's own ``default`` and ``when`` conditions.

A destination that only inactive routes reach is left alone, exactly like
``"enabled": false``: it is neither planned nor destroyed, so a machine
never touches harness folders its profiles do not cover.
"""

from __future__ import annotations

import socket
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Final

from skillsinc.exceptions import ConfigurationError

if TYPE_CHECKING:
    from skillsinc.config import Config
    from skillsinc.paths import Environment

PROFILES_VARIABLE: Final = "SKILLSINC_PROFILES"
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
            value = environment.variables.get(name, "")
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


class Origin(StrEnum):
    """Where the active profile set came from."""

    NONE = "none"
    """The configuration defines no profiles."""
    COMMAND_LINE = "command line"
    VARIABLE = PROFILES_VARIABLE
    DEFAULTS = "defaults"


@dataclass(frozen=True, slots=True)
class ProfileState:
    """Whether one profile is active on this run, and why."""

    profile: SyncProfile
    active: bool
    reason: str
    routes: int


@dataclass(frozen=True, slots=True)
class Selection:
    """The active profiles for one run, and the configuration they leave."""

    origin: Origin
    states: tuple[ProfileState, ...] = ()
    warnings: tuple[str, ...] = ()
    inactive_destinations: tuple[str, ...] = ()

    @property
    def active(self) -> tuple[str, ...]:
        """Return the active profile names, in configuration order."""
        return tuple(state.profile.name for state in self.states if state.active)


class ProfileSelector:
    """Decide which profiles apply and narrow a configuration to them."""

    def __init__(self, environment: Environment) -> None:
        self._environment = environment

    def select(
        self, config: Config, requested: Sequence[str] = ()
    ) -> tuple[Config, Selection]:
        """Return ``config`` narrowed to the active profiles, and the selection.

        Raises:
            ConfigurationError: If ``requested`` names no defined profile, or
                profiles are requested from a configuration without any.
        """
        if not config.profiles:
            if requested:
                msg = f"{config.path}: --profile given, but no profiles are defined"
                raise ConfigurationError(msg)
            return config, Selection(Origin.NONE)
        warnings: list[str] = []
        names = [name for raw in requested for name in self.split(raw)]
        if names:
            explicit = self._match(config, names, strict=True, warnings=warnings)
            origin = Origin.COMMAND_LINE
        else:
            variable = self.split(
                self._environment.variables.get(PROFILES_VARIABLE, "")
            )
            explicit = (
                self._match(config, variable, strict=False, warnings=warnings)
                if variable
                else None
            )
            origin = Origin.VARIABLE if variable else Origin.DEFAULTS
        states = tuple(
            self._state(config, profile, explicit, origin)
            for profile in config.profiles.values()
        )
        active = {state.profile.name for state in states if state.active}
        narrowed, inactive = self._narrow(config, active)
        return narrowed, Selection(origin, states, tuple(warnings), inactive)

    @staticmethod
    def split(text: str) -> list[str]:
        """Split a comma-separated list, dropping blanks."""
        return [part.strip() for part in text.split(",") if part.strip()]

    # Internals

    @staticmethod
    def _match(
        config: Config, patterns: Sequence[str], *, strict: bool, warnings: list[str]
    ) -> set[str]:
        """Return the profiles ``patterns`` (globs) name.

        Raises:
            ConfigurationError: If ``strict`` and a pattern matches nothing.
        """
        chosen: set[str] = set()
        unknown: list[str] = []
        for pattern in patterns:
            matched = {name for name in config.profiles if fnmatchcase(name, pattern)}
            if not matched:
                unknown.append(pattern)
            chosen |= matched
        if unknown:
            defined = ", ".join(config.profiles)
            if strict:
                msg = f"unknown profile(s): {', '.join(unknown)} (defined: {defined})"
                raise ConfigurationError(msg)
            warnings.append(
                f"{PROFILES_VARIABLE} names profile(s) this configuration does not"
                f" define: {', '.join(unknown)} (defined: {defined})"
            )
        return chosen

    def _state(
        self,
        config: Config,
        profile: SyncProfile,
        explicit: set[str] | None,
        origin: Origin,
    ) -> ProfileState:
        """Return whether ``profile`` is active and why."""
        routes = sum(profile.name in route.profiles for route in config.routes)
        if explicit is not None:
            active = profile.name in explicit
            reason = f"{'selected' if active else 'not selected'} by {origin.value}"
            return ProfileState(profile, active=active, reason=reason, routes=routes)
        if not profile.default:
            return ProfileState(
                profile, active=False, reason="default is false", routes=routes
            )
        if profile.when is None:
            return ProfileState(
                profile, active=True, reason="applies everywhere", routes=routes
            )
        failures = profile.when.failures(self._environment)
        if failures:
            return ProfileState(
                profile, active=False, reason="; ".join(failures), routes=routes
            )
        return ProfileState(
            profile, active=True, reason="conditions match", routes=routes
        )

    @staticmethod
    def _narrow(config: Config, active: set[str]) -> tuple[Config, tuple[str, ...]]:
        """Drop inactive routes and disable destinations only they reach."""
        routes = tuple(
            route
            for route in config.routes
            if not route.profiles or active.intersection(route.profiles)
        )
        reached = {name for route in routes for name in route.destinations}
        tagged = {name for route in config.routes for name in route.destinations}
        inactive = tuple(
            name
            for name, destination in config.destinations.items()
            if destination.enabled and name in tagged and name not in reached
        )
        destinations = {
            name: replace(destination, enabled=False)
            if name in inactive
            else destination
            for name, destination in config.destinations.items()
        }
        return replace(config, routes=routes, destinations=destinations), inactive
