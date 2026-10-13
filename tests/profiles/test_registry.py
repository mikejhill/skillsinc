"""Test built-in profile lookup and custom registry isolation."""

from __future__ import annotations

import pytest

from skillsinc.exceptions import ConfigurationError
from skillsinc.profiles.registry import ProfileRegistry
from skillsinc.profiles.shared import GenericProfile


class TestProfileRegistry:
    """Profile names resolve deterministically with helpful unknown-name errors."""

    def test_builtin_profiles_have_stable_order(self) -> None:
        """Expose each of the six supported harness profiles in display order."""
        result = [profile.name for profile in ProfileRegistry().profiles]

        assert result == [
            "claude",
            "copilot",
            "vscode",
            "codex",
            "agents",
            "generic",
        ], f"Expected six built-ins, got {result!r}"

    def test_custom_registry_uses_supplied_instance(self) -> None:
        """Explicit registries can limit the available profiles."""
        profile = GenericProfile()
        registry = ProfileRegistry((profile,))

        result = registry.get("generic")

        assert result is profile, f"Expected supplied profile instance, got {result!r}"

    def test_unknown_profile_lists_known_names(self) -> None:
        """Reject misspelled profile names with a contextual configuration error."""
        with pytest.raises(
            ConfigurationError, match=r"unknown profile 'missing'.*claude"
        ):
            ProfileRegistry().get("missing")
