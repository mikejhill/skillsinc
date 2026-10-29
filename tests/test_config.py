"""Test strict configuration tables, route filters, and environment paths."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from tests.conftest import Builder, Sandbox

from skillsinc.config import ConfigLoader, RouteConfig, SourceConfig, Table
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
        path = tmp_path / "config.json"
        path.write_text(text, encoding="utf-8")

        with pytest.raises(ConfigurationError, match=message):
            ConfigLoader(ProfileRegistry(), environment).load(path)

    def test_missing_config_reports_init_hint(
        self, tmp_path: Path, environment: Environment
    ) -> None:
        """Missing configuration raises a usage error with recovery guidance."""
        with pytest.raises(ConfigurationError, match="run `skillsinc init`"):
            ConfigLoader(ProfileRegistry(), environment).load(tmp_path / "missing")

    @pytest.mark.parametrize("suffix", ["", "child"])
    def test_rejects_aliased_destination_overlap(
        self, sandbox: Sandbox, tmp_path: Path, suffix: str
    ) -> None:
        """Dot-dot aliases cannot hide equal or nested destination directories."""
        parent = tmp_path / "alias-parent"
        parent.mkdir()
        alias = parent / ".." / "destination" / suffix
        target = tmp_path / "destination"
        Builder.write_config(
            sandbox.config_path,
            sandbox.library,
            (("alias", "generic", alias), ("target", "generic", target)),
        )

        with pytest.raises(ConfigurationError, match="overlap") as raised:
            sandbox.load()

        assert str(alias) in str(raised.value), (
            f"Expected original alias in diagnostic, got {raised.value}"
        )
        assert not target.exists(), "Expected no writes to overlapping destinations"

    @pytest.mark.parametrize(
        "error", [PermissionError("unavailable"), RuntimeError("symlink loop")]
    )
    def test_destination_resolution_failure_is_configuration_error(
        self,
        sandbox: Sandbox,
        monkeypatch: pytest.MonkeyPatch,
        error: OSError | RuntimeError,
    ) -> None:
        """Unresolvable destination paths fail before planning with a useful error."""
        sandbox.configure()
        original = Path.resolve

        def fail(path: Path, *, strict: bool = False) -> Path:
            """Fail only the configured destination without making any links."""
            if path == sandbox.root:
                raise error
            return original(path, strict=strict)

        monkeypatch.setattr(Path, "resolve", fail)

        with pytest.raises(ConfigurationError, match="cannot resolve destination"):
            sandbox.load()

    def test_toml_configuration_reports_migration(
        self, tmp_path: Path, environment: Environment
    ) -> None:
        """A TOML file, given directly or left beside the default, points at JSONC."""
        legacy = tmp_path / "config.toml"
        legacy.write_text("version = 1\n", encoding="utf-8")
        loader = ConfigLoader(ProfileRegistry(), environment)

        for path in (legacy, tmp_path / "config.json"):
            with pytest.raises(ConfigurationError, match=r"rewrite it as config\.json"):
                loader.load(path)

    def test_defaults_and_custom_layout(
        self, tmp_path: Path, environment: Environment
    ) -> None:
        """Expand home paths, apply layout overrides, and default harness roots."""
        path = tmp_path / "config.json"
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
        assert config.sources["x"].disabled == (), (
            f"Expected no disabled resources by default, got {config.sources['x']!r}"
        )
        assert config.destinations["claude"].enabled is False, (
            f"Expected disabled Claude, got {config.destinations!r}"
        )


class TestSourceDisabled:
    """Canonical source selectors disable items without changing authored files."""

    @staticmethod
    def load(
        tmp_path: Path, environment: Environment, disabled: object
    ) -> SourceConfig:
        """Load only a synthetic source definition with the supplied disabled field."""
        path = tmp_path / "disabled.json"
        data = {
            "version": 1,
            "sources": {"library": {"path": "library", "disabled": disabled}},
        }
        path.write_text(json.dumps(data), encoding="utf-8")
        return (
            ConfigLoader(ProfileRegistry(), environment).load(path).sources["library"]
        )

    def test_loads_disabled_selectors_as_immutable_source_state(
        self, tmp_path: Path, environment: Environment
    ) -> None:
        """The source retains canonical selectors, independent of filename excludes."""
        selectors = [f"{kind.value}/sample" for kind in Kind]
        selectors += [
            "plugins/kit/agents/reviewer",
            "plugins/k[ia]t/*/sam?le",
            "*/*",
            "resources/guide.v2",
        ]
        source = self.load(tmp_path, environment, selectors)

        assert source.disabled == tuple(selectors), (
            f"Expected source disable selectors {selectors!r}, got {source.disabled!r}"
        )
        assert source.exclude == Builder.source(tmp_path).exclude, (
            f"Expected filename excludes unchanged, got {source.exclude!r}"
        )

    @pytest.mark.parametrize("kind", list(Kind))
    def test_every_resource_kind_can_be_disabled(
        self, tmp_path: Path, kind: Kind
    ) -> None:
        """All authored item kinds use their same canonical kind/name identity."""
        source = replace(Builder.source(tmp_path), disabled=(f"{kind.value}/sample",))

        assert source.disables(Builder.item(kind=kind)), (
            f"Expected {kind.value}/sample to be disabled"
        )
        assert not source.disables(Builder.item(kind=kind, name="sample-other")), (
            f"Expected exact selector to keep {kind.value}/sample-other enabled"
        )

    @pytest.mark.parametrize(
        ("pattern", "expected"),
        [
            ("skills/sample", True),
            ("skills/sam*", True),
            ("skills/s?mple", True),
            ("skills/[st]ample", True),
            ("*/*", True),
            ("instructions/sample", False),
            ("skills/Sample", False),
            ("skills/sample-extra", False),
            ("plugins/kit/skills/sample", False),
        ],
    )
    def test_source_selectors_match_case_sensitive_route_globs(
        self, tmp_path: Path, pattern: str, *, expected: bool
    ) -> None:
        """Case, kind, and exact identities remain meaningful alongside globs."""
        source = replace(Builder.source(tmp_path), disabled=(pattern,))

        assert source.disables(Builder.item()) is expected, (
            f"Expected {pattern!r} to disable skills/sample: {expected}"
        )

    @pytest.mark.parametrize("kind", list(Kind))
    def test_plugin_alias_supports_globs_without_matching_unrelated_items(
        self, tmp_path: Path, kind: Kind
    ) -> None:
        """A plugin-qualified wildcard selector remains restricted to that plugin."""
        source = replace(Builder.source(tmp_path), disabled=("plugins/k?t/*/sam*",))

        assert source.disables(Builder.item(kind=kind, plugin="kit")), (
            f"Expected plugin kit's {kind} item to match its qualified selector"
        )
        assert not source.disables(Builder.item(kind=kind, plugin="other")), (
            "Expected a different plugin to remain enabled"
        )
        assert not source.disables(Builder.item(kind=kind)), (
            "Expected ordinary items with the same identity to remain enabled"
        )

    def test_disabled_selectors_are_source_scoped(self, tmp_path: Path) -> None:
        """An identically named item from another source is never disabled here."""
        source = replace(Builder.source(tmp_path), disabled=("*/*",))

        assert not source.disables(Builder.item(source="other")), (
            "Expected selectors to affect only the source that owns the setting"
        )

    def test_plain_selector_also_matches_a_plugin_item(self, tmp_path: Path) -> None:
        """As with route filters, a plugin item retains its kind/name alias."""
        source = replace(Builder.source(tmp_path), disabled=("skills/sample",))

        assert source.disables(Builder.item(plugin="kit")), (
            "Expected the unqualified selector to match the plugin item's alias"
        )

    def test_legacy_positional_constructor_keeps_existing_defaults(
        self, tmp_path: Path
    ) -> None:
        """Appending the field preserves existing positional SourceConfig callers."""
        layout = {kind: kind.value for kind in Kind}
        source = SourceConfig("library", tmp_path, layout, ("ignored",), "kits")

        assert (source.exclude, source.plugins, source.disabled) == (
            ("ignored",),
            "kits",
            (),
        ), f"Expected backwards-compatible source defaults, got {source!r}"
        assert not source.disables(Builder.item()), (
            "Expected nothing disabled by default"
        )

    def test_empty_disabled_list_keeps_resources_enabled(
        self, tmp_path: Path, environment: Environment
    ) -> None:
        """Removing all selectors reversibly restores ordinary source selection."""
        source = self.load(tmp_path, environment, [])

        assert source.disabled == (), (
            f"Expected empty source disabled state: {source!r}"
        )
        assert not source.disables(Builder.item()), (
            "Expected an empty list to disable none"
        )

    @pytest.mark.parametrize(
        "disabled",
        [None, "skills/foo", True, 1, {}, [""], [1], [None], ["skills/foo", 2]],
    )
    def test_rejects_wrong_disabled_field_types(
        self, tmp_path: Path, environment: Environment, disabled: object
    ) -> None:
        """Wrong container and element types identify the exact source setting."""
        with pytest.raises(ConfigurationError, match=r"sources\.library\.disabled"):
            self.load(tmp_path, environment, disabled)

    @pytest.mark.parametrize(
        "selector",
        [
            "skills",
            "skills/",
            "skills/ ",
            "/skills/foo",
            "skills//foo",
            "skills/foo/bar",
            "plugin/kit/skills/foo",
            "plugins/kit/skills",
            "plugins//skills/foo",
            "plugins/kit/skills/",
            "plugins/kit/unknown/foo",
            "unknown/foo",
            "sk*/foo",
            "skills\\foo",
            "plugins\\kit\\skills\\foo",
            "skills/..",
            "plugins/../skills/foo",
            "plugins/./skills/foo",
            "skills/.",
            "../foo",
            "C:/skills/foo",
            "//server/skills/foo",
            "plugins/kit/skills/foo/bar",
        ],
    )
    def test_rejects_noncanonical_disabled_selectors(
        self, tmp_path: Path, environment: Environment, selector: str
    ) -> None:
        """Malformed paths and unknown kinds fail early with repair guidance."""
        with pytest.raises(
            ConfigurationError, match=r"sources\.library\.disabled"
        ) as error:
            self.load(tmp_path, environment, [selector])

        assert "kind/name" in str(error.value), (
            f"Expected canonical selector guidance, got {error.value}"
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
