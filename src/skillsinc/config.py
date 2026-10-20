"""Load and validate the TOML configuration: sources, destinations, and routes."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from itertools import pairwise
from pathlib import Path
from typing import Final, NoReturn

from skillsinc.exceptions import ConfigurationError
from skillsinc.models import Customization, Kind
from skillsinc.paths import Environment
from skillsinc.profiles.registry import ProfileRegistry

CONFIG_VERSION: Final = 1
DEFAULT_EXCLUDE: Final = (".*", "INTENT.md", "*.intent.md", "__pycache__", "*.pyc")
ALL_ITEMS: Final = ("*/*",)


@dataclass(frozen=True, slots=True)
class SourceConfig:
    """A local library directory of authored customizations."""

    name: str
    path: Path
    layout: Mapping[Kind, str]
    exclude: tuple[str, ...] = DEFAULT_EXCLUDE


@dataclass(frozen=True, slots=True)
class DestinationConfig:
    """A harness directory that receives customizations."""

    name: str
    profile: str
    path: Path
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class RouteConfig:
    """Which items flow from which sources to which destinations."""

    sources: tuple[str, ...]
    destinations: tuple[str, ...]
    include: tuple[str, ...] = ALL_ITEMS
    exclude: tuple[str, ...] = ()
    allow_lossy: bool = False

    def selects(self, item: Customization) -> bool:
        """Return True if ``item`` comes from a routed source and passes the filters."""
        if item.source not in self.sources:
            return False
        selector = item.selector
        included = any(fnmatchcase(selector, pattern) for pattern in self.include)
        excluded = any(fnmatchcase(selector, pattern) for pattern in self.exclude)
        return included and not excluded


@dataclass(frozen=True, slots=True)
class Config:
    """A validated configuration file."""

    path: Path
    sources: Mapping[str, SourceConfig]
    destinations: Mapping[str, DestinationConfig]
    routes: tuple[RouteConfig, ...]


class Table:
    """Typed, strict accessor over one TOML table; reports unknown keys."""

    def __init__(self, data: Mapping[str, object], where: str) -> None:
        self._data = data
        self._where = where
        self._read: set[str] = set()

    def string(self, key: str, default: str | None = None) -> str | None:
        """Return a string value, or ``default`` when absent."""
        value = self._take(key)
        if value is None:
            return default
        if not isinstance(value, str) or not value:
            self._fail(key, "a non-empty string")
        return value

    def strings(self, key: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
        """Return a list of strings, or ``default`` when absent."""
        value = self._take(key)
        if value is None:
            return default
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item for item in value
        ):
            self._fail(key, "a list of non-empty strings")
        return tuple(str(item) for item in value)

    def boolean(self, key: str, *, default: bool) -> bool:
        """Return a boolean value, or ``default`` when absent."""
        value = self._take(key)
        if value is None:
            return default
        if not isinstance(value, bool):
            self._fail(key, "true or false")
        return value

    def integer(self, key: str) -> int | None:
        """Return an integer value, or None when absent."""
        value = self._take(key)
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            self._fail(key, "an integer")
        return value

    def table(self, key: str) -> Table | None:
        """Return a nested table, or None when absent."""
        value = self._take(key)
        if value is None:
            return None
        return self._child(value, f"{self._where}.{key}")

    def tables(self, key: str) -> dict[str, Table]:
        """Return a table of named sub-tables."""
        value = self._take(key)
        if value is None:
            return {}
        if not isinstance(value, dict):
            self._fail(key, "a table")
        return {
            str(name): self._child(entry, f"{self._where}.{key}.{name}")
            for name, entry in value.items()
        }

    def table_list(self, key: str) -> list[Table]:
        """Return an array of tables."""
        value = self._take(key)
        if value is None:
            return []
        if not isinstance(value, list):
            self._fail(key, "an array of tables ([[...]])")
        return [
            self._child(entry, f"{self._where}.{key}[{index}]")
            for index, entry in enumerate(value)
        ]

    def finish(self) -> None:
        """Raise if the table holds keys nobody read (usually typos)."""
        unknown = sorted(set(self._data) - self._read)
        if unknown:
            msg = f"{self._where}: unknown key(s): {', '.join(unknown)}"
            raise ConfigurationError(msg)

    # Internals

    def _take(self, key: str) -> object:
        """Mark ``key`` as read and return its raw value."""
        self._read.add(key)
        return self._data.get(key)

    def _child(self, value: object, where: str) -> Table:
        """Wrap a nested table, rejecting non-table values."""
        if not isinstance(value, dict):
            msg = f"{where}: expected a table"
            raise ConfigurationError(msg)
        return Table({str(key): item for key, item in value.items()}, where)

    def _fail(self, key: str, expected: str) -> NoReturn:
        """Raise a type error for ``key``."""
        msg = f"{self._where}.{key}: expected {expected}"
        raise ConfigurationError(msg)


class ConfigLoader:
    """Read a configuration file into validated ``Config`` values."""

    def __init__(self, profiles: ProfileRegistry, environment: Environment) -> None:
        self._profiles = profiles
        self._environment = environment

    def load(self, path: Path) -> Config:
        """Parse and validate ``path``.

        Raises:
            ConfigurationError: If the file is missing, malformed, or inconsistent.
        """
        root = Table(self._read(path), path.name)
        version = root.integer("version")
        if version != CONFIG_VERSION:
            msg = f"{path}: unsupported version {version!r} (expected {CONFIG_VERSION})"
            raise ConfigurationError(msg)
        base = path.parent
        sources = {
            name: self._source(name, table, base)
            for name, table in root.tables("sources").items()
        }
        destinations = {
            name: self._destination(name, table, base)
            for name, table in root.tables("destinations").items()
        }
        routes = tuple(self._route(table) for table in root.table_list("routes"))
        root.finish()

        config = Config(path, sources, destinations, routes)
        self._check_references(config)
        self._check_overlaps(config)
        return config

    # Sections

    def _source(self, name: str, table: Table, base: Path) -> SourceConfig:
        """Build one source."""
        raw = table.string("path")
        if raw is None:
            msg = f"sources.{name}.path is required"
            raise ConfigurationError(msg)
        layout = self._layout(table.table("layout"))
        exclude = table.strings("exclude", DEFAULT_EXCLUDE)
        table.finish()
        return SourceConfig(name, self._environment.expand(raw, base), layout, exclude)

    @staticmethod
    def _layout(table: Table | None) -> dict[Kind, str]:
        """Return each kind's directory, applying ``[sources.<name>.layout]``."""
        layout = {kind: kind.value for kind in Kind}
        if table is None:
            return layout
        for kind in Kind:
            directory = table.string(kind.value)
            if directory is not None:
                layout[kind] = directory
        table.finish()
        return layout

    def _destination(self, name: str, table: Table, base: Path) -> DestinationConfig:
        """Build one destination, defaulting its path from the profile."""
        profile_name = table.string("profile", name) or name
        profile = self._profiles.get(profile_name)
        raw = table.string("path")
        enabled = table.boolean("enabled", default=True)
        table.finish()
        if raw is not None:
            path = self._environment.expand(raw, base)
        else:
            default = profile.default_path(self._environment)
            if default is None:
                msg = (
                    f"destinations.{name}.path is required for profile '{profile.name}'"
                )
                raise ConfigurationError(msg)
            path = default
        return DestinationConfig(name, profile.name, path, enabled)

    @staticmethod
    def _route(table: Table) -> RouteConfig:
        """Build one route."""
        route = RouteConfig(
            sources=table.strings("sources"),
            destinations=table.strings("destinations"),
            include=table.strings("include", ALL_ITEMS),
            exclude=table.strings("exclude"),
            allow_lossy=table.boolean("allow_lossy", default=False),
        )
        table.finish()
        return route

    # Validation

    @staticmethod
    def _check_references(config: Config) -> None:
        """Every route must name existing sources and destinations."""
        for index, route in enumerate(config.routes):
            where = f"routes[{index}]"
            if not route.sources or not route.destinations:
                msg = f"{where}: needs at least one source and one destination"
                raise ConfigurationError(msg)
            missing = [name for name in route.sources if name not in config.sources]
            missing += [
                name for name in route.destinations if name not in config.destinations
            ]
            if missing:
                msg = f"{where}: unknown source/destination: {', '.join(missing)}"
                raise ConfigurationError(msg)

    @staticmethod
    def _check_overlaps(config: Config) -> None:
        """Destinations may not share or nest directories; ownership would clash."""
        destinations = sorted(config.destinations.values(), key=lambda d: d.path)
        for first, second in pairwise(destinations):
            if second.path == first.path or second.path.is_relative_to(first.path):
                msg = (
                    f"destinations '{first.name}' and '{second.name}' overlap"
                    f" ({first.path} and {second.path})"
                )
                raise ConfigurationError(msg)

    # Internals

    @staticmethod
    def _read(path: Path) -> dict[str, object]:
        """Read and parse the TOML file."""
        try:
            with path.open("rb") as handle:
                data = tomllib.load(handle)
        except FileNotFoundError as err:
            msg = f"configuration file not found: {path} (run `skillsinc init`)"
            raise ConfigurationError(msg) from err
        except tomllib.TOMLDecodeError as err:
            msg = f"{path}: invalid TOML: {err}"
            raise ConfigurationError(msg) from err
        return {str(key): value for key, value in data.items()}
