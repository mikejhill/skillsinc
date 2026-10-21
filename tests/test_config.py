"""Test strict configuration tables, route filters, and environment paths."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import Builder, Sandbox

from skillsinc.config import ConfigLoader, RouteConfig, Table
from skillsinc.exceptions import ConfigurationError
from skillsinc.models import Kind
from skillsinc.paths import Environment
from skillsinc.profiles.registry import ProfileRegistry


class TestConfigLoader:
    """Strict JSONC loading and semantic validation."""

    def test_loads_explicit_paths_and_routes(self, sandbox: Sandbox) -> None:
        """Resolve the shared config builder into exact typed settings."""
        config = sandbox.configure()

        assert config.sources["library"].path == sandbox.library, (
            f"Expected {sandbox.library}, got {config.sources!r}"
        )
        assert config.destinations["target"].path == sandbox.root, (
            f"Expected {sandbox.root}, got {config.destinations!r}"
        )

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ('{"version": 2}', "unsupported version"),
            ('{"version": true}', "integer"),
            ('{"version": 1, "extra": true}', "unknown key"),
            ("version = 1", "invalid JSONC"),
            ('{"version": 1, "version": 1}', "duplicate key"),
            ("[]", "expected a JSON object"),
            ('{"version": 1 /* unterminated', "invalid JSONC"),
            ('{"version": 1, "sources": {"x": {}}}', "path is required"),
            ('{"version": 1, "destinations": {"generic": {}}}', "path is required"),
            ('{"version": 1, "destinations": {"nope": {}}}', "unknown profile"),
            ('{"version": 1, "routes": [{}]}', "at least one"),
            ('{"version": 1, "routes": null}', "null is not allowed"),
            (
                '{"version": 1, "routes": [{"sources": ["a"], "include": null}]}',
                "null is not allowed",
            ),
            (
                '{"version": 1, "routes": [{"sources": ["x"], "destinations": ["y"]}]}',
                "unknown source/destination",
            ),
            (
                (
                    '{"version": 1, "destinations": {'
                    '"a": {"profile": "generic", "path": "out"},'
                    ' "b": {"profile": "generic", "path": "out/sub"}}}'
                ),
                "overlap",
            ),
            (
                (
                    '{"version": 1, "destinations": {'
                    '"a": {"profile": "generic", "path": "out"},'
                    ' "b": {"profile": "generic", "path": "out"}}}'
                ),
                "overlap",
            ),
        ],
    )
    def test_rejects_invalid_configuration(
        self, tmp_path: Path, environment: Environment, text: str, message: str
    ) -> None:
        """Reject malformed, unknown, incomplete, and overlapping configuration."""
        path = tmp_path / "config.jsonc"
        path.write_text(text, encoding="utf-8")

        with pytest.raises(ConfigurationError, match=message):
            ConfigLoader(ProfileRegistry(), environment).load(path)

    def test_missing_config_reports_init_hint(
        self, tmp_path: Path, environment: Environment
    ) -> None:
        """Missing configuration raises a usage error with recovery guidance."""
        with pytest.raises(ConfigurationError, match="run `skillsinc init`"):
            ConfigLoader(ProfileRegistry(), environment).load(tmp_path / "missing")

    def test_toml_configuration_reports_migration(
        self, tmp_path: Path, environment: Environment
    ) -> None:
        """A TOML file, given directly or left beside the default, points at JSONC."""
        legacy = tmp_path / "config.toml"
        legacy.write_text("version = 1\n", encoding="utf-8")
        loader = ConfigLoader(ProfileRegistry(), environment)

        for path in (legacy, tmp_path / "config.jsonc"):
            with pytest.raises(
                ConfigurationError, match=r"rewrite it as config\.jsonc"
            ):
                loader.load(path)

    def test_defaults_and_custom_layout(
        self, tmp_path: Path, environment: Environment
    ) -> None:
        """Expand home paths, apply layout overrides, and default harness roots."""
        path = tmp_path / "config.jsonc"
        path.write_text(
            """\ufeff// Comments, a byte-order mark, and trailing commas are allowed.
            {
              "version": 1,
              "sources": {
                "x": {
                  "path": "~/lib",  // home-relative
                  "exclude": [],
                  /* layout overrides */
                  "layout": { "skills": "packages", "plugins": "kits", },
                },
              },
              "destinations": { "claude": { "enabled": false } },
            }
            """,
            encoding="utf-8",
        )

        config = ConfigLoader(ProfileRegistry(), environment).load(path)

        assert config.sources["x"].path == environment.home / "lib", (
            f"Expected fake home library, got {config.sources!r}"
        )
        assert config.sources["x"].layout[Kind.SKILL] == "packages", (
            f"Expected packages layout, got {config.sources['x'].layout!r}"
        )
        assert config.sources["x"].plugins == "kits", (
            f"Expected kits plugin folder, got {config.sources['x']!r}"
        )
        assert config.destinations["claude"].enabled is False, (
            f"Expected disabled Claude, got {config.destinations!r}"
        )


class TestTable:
    """Typed accessors reject invalid values and track unknown keys."""

    @pytest.mark.parametrize(
        ("method", "value"),
        [
            ("string", ""),
            ("string", 1),
            ("strings", "x"),
            ("strings", [""]),
            ("boolean", 1),
            ("integer", True),
            ("table", []),
            ("tables", []),
            ("tables", {"x": 1}),
            ("table_list", {}),
            ("table_list", [1]),
        ],
    )
    def test_rejects_wrong_field_type(self, method: str, value: object) -> None:
        """Each accessor gives a contextual configuration error for wrong types."""
        table = Table({"value": value}, "section")
        operation = table.boolean if method == "boolean" else getattr(table, method)
        arguments = {"default": False} if method == "boolean" else {}

        with pytest.raises(ConfigurationError, match=r"section.*expected"):
            operation("value", **arguments)

    def test_absent_values_use_defaults(self) -> None:
        """Missing optional fields produce their documented empty defaults."""
        table = Table({}, "empty")

        result = (
            table.string("s"),
            table.strings("ss"),
            table.integer("i"),
            table.table("t"),
            table.tables("ts"),
            table.table_list("tl"),
            table.boolean("b", default=True),
        )
        table.finish()

        assert result == (None, (), None, None, {}, [], True), (
            f"Expected empty/default values, got {result!r}"
        )


class TestRouteConfig:
    """Source membership and include/exclude precedence."""

    @pytest.mark.parametrize(
        ("source", "include", "exclude", "expected"),
        [
            ("library", ("skills/*",), (), True),
            ("other", ("*/*",), (), False),
            ("library", (), (), False),
            ("library", ("*/*",), ("skills/sample",), False),
        ],
    )
    def test_filters_by_source_and_selector(
        self,
        source: str,
        include: tuple[str, ...],
        exclude: tuple[str, ...],
        *,
        expected: bool,
    ) -> None:
        """Exclude filters override includes and unrelated sources never route."""
        route = RouteConfig(("library",), ("target",), include=include, exclude=exclude)

        result = route.selects(Builder.item(source=source))

        assert result is expected, f"Expected selection {expected}, got {result}"

    @pytest.mark.parametrize(
        ("include", "exclude", "expected"),
        [
            (("skills/*",), (), True),
            (("plugins/kit/*",), (), True),
            (("plugins/other/*",), (), False),
            (("*/*",), ("plugins/kit/*",), False),
        ],
    )
    def test_plugin_items_match_by_plugin_path(
        self, include: tuple[str, ...], exclude: tuple[str, ...], *, expected: bool
    ) -> None:
        """A route selects or drops a whole plugin through ``plugins/<name>/``."""
        route = RouteConfig(("library",), ("target",), include=include, exclude=exclude)

        result = route.selects(Builder.item(plugin="kit"))

        assert result is expected, f"Expected selection {expected}, got {result}"


class TestEnvironment:
    """User path expansion respects the injected environment."""

    @pytest.mark.parametrize("raw", ["~", "~/child", "~\\child", "relative"])
    def test_expands_fake_home_and_relative_paths(
        self, environment: Environment, raw: str
    ) -> None:
        """Expand home prefixes and resolve relative paths against the caller base."""
        expected = (
            environment.home
            if raw == "~"
            else environment.home / ("relative" if raw == "relative" else "child")
        )

        result = environment.expand(raw, environment.home)

        assert result == expected, f"Expected {expected}, got {result}"

    def test_expands_windows_separators_on_every_platform(
        self, environment: Environment
    ) -> None:
        """Treat backslashes after ``~`` as separators so configs are portable."""
        expected = environment.home / "nested" / "child"

        result = environment.expand("~\\nested\\child", environment.home)

        assert result == expected, f"Expected {expected}, got {result}"

    def test_relative_variables_fall_back(self, tmp_path: Path) -> None:
        """Ignore relative XDG variables and use fake-home defaults."""
        environment = Environment(tmp_path, {"XDG_CONFIG_HOME": "relative"}, "linux")

        result = (environment.config_home, environment.state_home)

        assert result == (tmp_path / ".config", tmp_path / ".local/state"), (
            f"Expected home fallbacks, got {result!r}"
        )
