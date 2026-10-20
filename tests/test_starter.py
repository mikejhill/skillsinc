"""Test starter detection, placeholder paths, and explicit overwrite policy."""

from __future__ import annotations

from pathlib import Path

import pytest

from skillsinc.exceptions import ConfigurationError
from skillsinc.paths import Environment
from skillsinc.profiles.registry import ProfileRegistry
from skillsinc.starter import StarterConfig


class TestStarterConfig:
    """Initial configuration enables only detected harness directories."""

    def test_no_harnesses_comments_out_route_and_uses_placeholder(
        self, environment: Environment
    ) -> None:
        """Generate editable source guidance without enabling absent harnesses."""
        result = StarterConfig(ProfileRegistry(), environment).render(None)

        assert 'path = "~/agent-library"' in result, (
            f"Expected placeholder and disabled harnesses, got {result!r}"
        )
        assert "# [[routes]]" in result, (
            f"Expected placeholder and disabled harnesses, got {result!r}"
        )
        assert "# [destinations.claude]" in result, (
            f"Expected placeholder and disabled harnesses, got {result!r}"
        )

    def test_home_source_uses_portable_tilde_path(
        self, environment: Environment
    ) -> None:
        """Display sources inside the injected home as portable tilde paths."""
        result = StarterConfig(ProfileRegistry(), environment).render(
            environment.home / "library"
        )

        assert 'path = "~/library"' in result, (
            f"Expected home-relative source, got {result!r}"
        )

    def test_existing_config_requires_force(
        self, tmp_path: Path, environment: Environment
    ) -> None:
        """Refuse accidental overwrite of existing user configuration."""
        path = tmp_path / "config.toml"
        path.write_text("User config", encoding="utf-8")

        with pytest.raises(ConfigurationError, match="pass --force"):
            StarterConfig(ProfileRegistry(), environment).write(path, None, force=False)

        assert path.read_text(encoding="utf-8") == "User config", (
            "Expected existing config preserved"
        )

    def test_force_overwrites_existing_config(
        self, tmp_path: Path, environment: Environment
    ) -> None:
        """Explicit force persists the generated configuration."""
        path = tmp_path / "config.toml"
        path.write_text("old", encoding="utf-8")

        result = StarterConfig(ProfileRegistry(), environment).write(
            path, None, force=True
        )

        assert path.read_text(encoding="utf-8") == result, (
            f"Expected generated config persisted, got {result!r}"
        )
        assert "version = 1" in result, (
            f"Expected generated config persisted, got {result!r}"
        )
