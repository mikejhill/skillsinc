"""Test starter detection, placeholder paths, and explicit overwrite policy."""

from __future__ import annotations

from pathlib import Path

import pytest

from skillsinc.config import ConfigLoader
from skillsinc.exceptions import ConfigurationError
from skillsinc.paths import Environment
from skillsinc.profiles.registry import ProfileRegistry
from skillsinc.starter import StarterConfig


class TestStarterConfig:
    """Initial configuration enables only detected harness directories."""

    def test_no_harnesses_comments_out_route_and_uses_default_library(
        self, environment: Environment
    ) -> None:
        """Point at the default library without enabling absent harnesses."""
        result = StarterConfig(ProfileRegistry(), environment).render(None)
        library = (environment.config_home / "skillsinc" / "library").as_posix()

        assert f'path = "{library}"' in result, (
            f"Expected default library and disabled harnesses, got {result!r}"
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

    def test_default_library_is_tilde_path_under_home_config(
        self, tmp_path: Path
    ) -> None:
        """Default to ``~/.config/skillsinc/library`` when XDG is not set."""
        home = tmp_path / "home"
        home.mkdir()
        environment = Environment(home, {}, "linux")

        result = StarterConfig(ProfileRegistry(), environment).render(None)

        assert 'path = "~/.config/skillsinc/library"' in result, (
            f"Expected the library under ~/.config, got {result!r}"
        )

    def test_write_without_source_creates_default_library(
        self, tmp_path: Path, environment: Environment
    ) -> None:
        """Create the empty default library so the first plan can read it."""
        StarterConfig(ProfileRegistry(), environment).write(
            tmp_path / "config.toml", None, force=False
        )

        library = environment.config_home / "skillsinc" / "library"
        assert library.is_dir(), f"Expected {library} to be created"

    def test_write_with_source_creates_nothing_else(
        self, tmp_path: Path, environment: Environment
    ) -> None:
        """Leave the default library alone when the user names a source."""
        StarterConfig(ProfileRegistry(), environment).write(
            tmp_path / "config.toml", tmp_path / "mine", force=False
        )

        library = environment.config_home / "skillsinc" / "library"
        assert not library.exists(), f"Expected no default library at {library}"
        assert not (tmp_path / "mine").exists(), "Expected --source left untouched"

    def test_detected_harnesses_get_routes_without_duplicates(
        self, tmp_path: Path, environment: Environment
    ) -> None:
        """Skills skip ~/.copilot and VS Code skips what ~/.copilot provides."""
        registry = ProfileRegistry()
        for name in ("claude", "copilot", "vscode", "codex", "agents"):
            default = registry.get(name).default_path(environment)
            assert default is not None
            default.mkdir(parents=True)
        path = tmp_path / "config.toml"

        StarterConfig(registry, environment).write(path, None, force=False)
        config = ConfigLoader(registry, environment).load(path)

        routes = {route.destinations: route.exclude for route in config.routes}
        assert routes == {
            ("claude", "codex", "agents"): (),
            ("copilot",): ("skills/*",),
            ("vscode",): ("instructions/*", "agents/*"),
        }, f"Expected split routes, got {routes!r}"

    def test_copilot_keeps_skills_without_agents_home(
        self, tmp_path: Path, environment: Environment
    ) -> None:
        """Copilot still receives skills when ~/.agents does not exist."""
        registry = ProfileRegistry()
        default = registry.get("copilot").default_path(environment)
        assert default is not None
        default.mkdir(parents=True)
        path = tmp_path / "config.toml"

        StarterConfig(registry, environment).write(path, None, force=False)
        config = ConfigLoader(registry, environment).load(path)

        assert [(r.destinations, r.exclude) for r in config.routes] == [
            (("copilot",), ())
        ], f"Expected one unfiltered route, got {config.routes!r}"

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
