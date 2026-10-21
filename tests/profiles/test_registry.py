"""Test built-in profile lookup and custom registry isolation."""

from __future__ import annotations

import pytest
from tests.conftest import Builder

from skillsinc.exceptions import ConfigurationError
from skillsinc.models import Kind, Support
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

    @pytest.mark.parametrize(
        ("name", "hint"),
        [
            ("agents", ""),
        ],
    )
    def test_mcp_servers_are_refused_where_unsupported(
        self, name: str, hint: str
    ) -> None:
        """Profiles without an MCP location refuse servers and say where they live."""
        profile = ProfileRegistry().get(name)

        result = profile.render(Builder.item(Kind.MCP))

        assert profile.support[Kind.MCP] is Support.UNSUPPORTED, (
            f"Expected {name} to list MCP as unsupported, got {profile.support!r}"
        )
        assert result.unsupported is not None, f"Expected refusal, got {result!r}"
        assert hint in result.unsupported, f"Expected hint {hint!r}, got {result!r}"

    @pytest.mark.parametrize(
        ("name", "hint"),
        [("vscode", "copilot or claude destination"), ("codex", ""), ("agents", "")],
    )
    def test_hooks_are_refused_where_unsupported(self, name: str, hint: str) -> None:
        """Profiles without a hook location refuse hook files."""
        profile = ProfileRegistry().get(name)

        result = profile.render(Builder.item(Kind.HOOK))

        assert profile.support[Kind.HOOK] is Support.UNSUPPORTED, (
            f"Expected {name} to list hooks as unsupported, got {profile.support!r}"
        )
        assert result.unsupported is not None, f"Expected refusal, got {result!r}"
        assert hint in result.unsupported, f"Expected hint {hint!r}, got {result!r}"

    @pytest.mark.parametrize("name", ["codex", "vscode", "agents"])
    def test_resources_are_refused_where_unsupported(self, name: str) -> None:
        """Profiles without a resources folder refuse resources."""
        profile = ProfileRegistry().get(name)

        result = profile.render(Builder.item(Kind.RESOURCE))

        assert profile.support[Kind.RESOURCE] is Support.UNSUPPORTED, (
            f"Expected {name} to list resources as unsupported, got {profile.support!r}"
        )
        assert result.unsupported is not None, f"Expected refusal, got {result!r}"
