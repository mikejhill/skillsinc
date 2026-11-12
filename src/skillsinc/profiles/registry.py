"""Lookup table of built-in harness profiles."""

from __future__ import annotations

from pathlib import Path

from skillsinc.exceptions import ConfigurationError
from skillsinc.profiles.base import Profile
from skillsinc.profiles.claude import ClaudeProfile
from skillsinc.profiles.codex import CodexProfile
from skillsinc.profiles.copilot import CopilotProfile, VSCodeProfile
from skillsinc.profiles.shared import AgentsProfile, GenericProfile


class ProfileRegistry:
    """Built-in profiles, keyed by name."""

    def __init__(self, profiles: tuple[Profile, ...] | None = None) -> None:
        defaults = (
            ClaudeProfile(),
            CopilotProfile(),
            VSCodeProfile(),
            CodexProfile(),
            AgentsProfile(),
            GenericProfile(),
        )
        self._profiles = {profile.name: profile for profile in profiles or defaults}

    def scoped(self, project: Path | None) -> ProfileRegistry:
        """Return these profiles for ``project``, or for the user level when None."""
        return ProfileRegistry(
            tuple(profile.scoped(project) for profile in self._profiles.values())
        )

    @property
    def profiles(self) -> tuple[Profile, ...]:
        """Return every profile in display order."""
        return tuple(self._profiles.values())

    def get(self, name: str) -> Profile:
        """Return the profile called ``name``.

        Raises:
            ConfigurationError: If no profile has that name.
        """
        profile = self._profiles.get(name)
        if profile is None:
            known = ", ".join(self._profiles)
            msg = f"unknown profile '{name}' (expected one of: {known})"
            raise ConfigurationError(msg)
        return profile
