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

    @pytest.mark.parametrize("harness", ["codex", "vscode"])
    @pytest.mark.parametrize("with_guidance", [False, True])
    def test_init_enables_shared_skills_for_readers(
        self,
        tmp_path: Path,
        environment: Environment,
        harness: str,
        *,
        with_guidance: bool,
    ) -> None:
        """Codex and VS Code receive their skill even without an agents folder."""
        profiles = ProfileRegistry()
        destination = profiles.get(harness).default_path(environment)
        assert destination is not None, "Expected a built-in harness home"
        destination.mkdir(parents=True)
        path = tmp_path / "config.json"

        StarterConfig(profiles, environment).write(
            path, None, force=False, with_guidance=with_guidance
        )

        loaded = ConfigLoader(profiles, environment).load(path)
        assert set(loaded.destinations) == {harness, "agents"}, (
            f"Expected shared skill discovery for {harness}, got {loaded!r}"
        )
        assert not (environment.home / ".agents").exists(), (
            "Expected only the route enabled; destination creation waits for sync"
        )

    def test_no_harnesses_comments_out_route_and_uses_default_library(
        self, environment: Environment
    ) -> None:
        """Point at the default library without enabling absent harnesses."""
        result = StarterConfig(ProfileRegistry(), environment).render(None)
        library = (environment.config_home / "skillsinc" / "library").as_posix()

        assert f'"path": "{library}"' in result, (
            f"Expected default library and disabled harnesses, got {result!r}"
        )
        assert '    // {\n    //   "sources": ["library"],' in result, (
            f"Expected placeholder and disabled harnesses, got {result!r}"
        )
        assert '// "claude": { "profile": "claude" },' in result, (
            f"Expected placeholder and disabled harnesses, got {result!r}"
        )

    def test_home_source_uses_portable_tilde_path(
        self, environment: Environment
    ) -> None:
        """Display sources inside the injected home as portable tilde paths."""
        result = StarterConfig(ProfileRegistry(), environment).render(
            environment.home / "library"
        )

        assert '"path": "~/library"' in result, (
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

        assert '"path": "~/.config/skillsinc/library"' in result, (
            f"Expected the library under ~/.config, got {result!r}"
        )

    def test_write_without_source_creates_default_library(
        self, tmp_path: Path, environment: Environment
    ) -> None:
        """Create the empty default library so the first plan can read it."""
        StarterConfig(ProfileRegistry(), environment).write(
            tmp_path / "config.json", None, force=False
        )

        library = environment.config_home / "skillsinc" / "library"
        assert library.is_dir(), f"Expected {library} to be created"
        assert list(library.iterdir()) == [], "Expected guidance to remain opt-in"

    def test_guidance_is_added_to_the_explicit_source_only(
        self, tmp_path: Path, environment: Environment
    ) -> None:
        """Bootstrap the chosen library without creating a second source."""
        root = tmp_path / "chosen"
        root.mkdir()
        config = tmp_path / "config.json"

        StarterConfig(ProfileRegistry(), environment).write(
            config, root, force=False, with_guidance=True
        )

        loaded = ConfigLoader(ProfileRegistry(), environment).load(config)
        assert loaded.sources["library"].path == root, "Expected chosen source routed"
        assert (root / "skills/skillsinc/SKILL.md").is_file(), (
            "Expected guidance in the chosen source"
        )
        assert not environment.default_library.exists(), (
            "Expected no parallel default library"
        )

    def test_write_with_source_creates_nothing_else(
        self, tmp_path: Path, environment: Environment
    ) -> None:
        """Leave the default library alone when the user names a source."""
        StarterConfig(ProfileRegistry(), environment).write(
            tmp_path / "config.json", tmp_path / "mine", force=False
        )

        library = environment.config_home / "skillsinc" / "library"
        assert not library.exists(), f"Expected no default library at {library}"
        assert not (tmp_path / "mine").exists(), "Expected --source left untouched"

    @pytest.mark.parametrize("with_guidance", [False, True])
    def test_existing_config_requires_force(
        self, tmp_path: Path, environment: Environment, *, with_guidance: bool
    ) -> None:
        """Refuse accidental overwrite of existing user configuration."""
        path = tmp_path / "config.json"
        path.write_text("User config", encoding="utf-8")

        with pytest.raises(ConfigurationError, match="pass --force"):
            StarterConfig(ProfileRegistry(), environment).write(
                path, None, force=False, with_guidance=with_guidance
            )

        assert path.read_text(encoding="utf-8") == "User config", (
            "Expected existing config preserved"
        )
        assert not environment.default_library.exists(), "Expected no library writes"

    @pytest.mark.parametrize("with_guidance", [False, True])
    def test_toml_path_is_refused_before_writing(
        self, tmp_path: Path, environment: Environment, *, with_guidance: bool
    ) -> None:
        """Refuse a ``.toml`` target, even with force; plan could not read it."""
        path = tmp_path / "config.toml"
        path.write_text("version = 1\n", encoding="utf-8")

        with pytest.raises(ConfigurationError, match=r"config\.json"):
            StarterConfig(ProfileRegistry(), environment).write(
                path, None, force=True, with_guidance=with_guidance
            )

        assert path.read_text(encoding="utf-8") == "version = 1\n", (
            "Expected the TOML file left untouched"
        )

    def test_force_overwrites_existing_config(
        self, tmp_path: Path, environment: Environment
    ) -> None:
        """Explicit force persists the generated configuration."""
        path = tmp_path / "config.json"
        path.write_text("old", encoding="utf-8")

        result = StarterConfig(ProfileRegistry(), environment).write(
            path, None, force=True
        )

        assert path.read_text(encoding="utf-8") == result, (
            f"Expected generated config persisted, got {result!r}"
        )
        assert '"version": 1,' in result, (
            f"Expected generated config persisted, got {result!r}"
        )

    @pytest.mark.parametrize("installed", [False, True])
    @pytest.mark.parametrize("with_guidance", [False, True])
    def test_starter_loads_as_valid_configuration(
        self,
        tmp_path: Path,
        environment: Environment,
        *,
        installed: bool,
        with_guidance: bool,
    ) -> None:
        """The written JSONC loads, with or without a detected harness."""
        if installed:
            (environment.home / ".claude").mkdir(parents=True)
        path = tmp_path / "config.json"
        StarterConfig(ProfileRegistry(), environment).write(
            path, None, force=False, with_guidance=with_guidance
        )

        config = ConfigLoader(ProfileRegistry(), environment).load(path)

        expected = ("claude",) if installed else ()
        result = tuple(name for route in config.routes for name in route.destinations)
        assert result == expected, f"Expected routes to {expected}, got {config!r}"
