"""Load and validate the JSONC configuration: sources, destinations, and routes."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from fnmatch import fnmatchcase
from itertools import pairwise
from pathlib import Path
from typing import Final, NoReturn

from skillsinc.entries import JsonDocument
from skillsinc.exceptions import ConfigurationError
from skillsinc.jsonc import Jsonc
from skillsinc.models import Customization, Kind
from skillsinc.paths import Environment
from skillsinc.profiles.registry import ProfileRegistry
from skillsinc.schedule import parse_interval

CONFIG_VERSION: Final = 1
DEFAULT_EXCLUDE: Final = (".*", "INTENT.md", "*.intent.md", "__pycache__", "*.pyc")
ALL_ITEMS: Final = ("*/*",)
PLUGINS: Final = "plugins"
SELECTOR_KINDS: Final = frozenset({"*", *(kind.value for kind in Kind)})
GITHUB_REPOSITORY: Final = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
COMMIT: Final = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
# ``scheme://userinfo@``: any userinfo may be a token, except an SSH user name
USERINFO: Final = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^/@\s]*)@")
SSH_SCHEMES: Final = frozenset({"ssh://", "git+ssh://", "ssh+git://"})
LOCATIONS: Final = ("path", "github", "git")
TOML_RETIRED: Final = (
    "TOML configuration is no longer read; rewrite it as config.json, see"
    " https://github.com/mikejhill/skillsinc/blob/main/docs/guides/configuration.md"
    "#migrating-from-toml"
)


def mask(text: str) -> str:
    """Hide credentials in any URL inside ``text``.

    Userinfo is hidden whole, since ``https://TOKEN@host`` carries a secret
    without a colon; only a bare SSH user name such as ``ssh://git@`` stays.
    """

    def hide(match: re.Match[str]) -> str:
        scheme, userinfo = match.group(1), match.group(2)
        if scheme.lower() in SSH_SCHEMES and ":" not in userinfo:
            return match.group(0)
        return f"{scheme}***@"

    return USERINFO.sub(hide, text)


@dataclass(frozen=True, slots=True)
class RemoteConfig:
    """Where a remote source's Git repository lives and which commit to read."""

    url: str
    label: str
    """How reports name the repository: ``owner/repo`` or the URL."""
    ref: str | None = None
    """Branch, tag, or full commit SHA; None follows the remote's ``HEAD``."""
    subdirectory: str = ""
    refresh: timedelta = timedelta(0)
    """Shortest time between remote checks; zero checks on every run."""
    github: bool = False

    @property
    def pinned(self) -> bool:
        """Return True if ``ref`` names one immutable commit."""
        return self.ref is not None and COMMIT.match(self.ref) is not None

    def cache_key(self, name: str) -> str:
        """Return the cache directory name: the source name plus a digest."""
        digest = hashlib.sha256(f"{self.url}\n{self.ref or ''}".encode()).hexdigest()
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)
        return f"{safe}-{digest[:12]}"


@dataclass(frozen=True, slots=True)
class SourceConfig:
    """A library of authored customizations: a local directory or a Git remote.

    For a remote source, ``path`` is its cache directory; the reader asks
    the remote cache which checkout to read.
    """

    name: str
    path: Path
    layout: Mapping[Kind, str]
    exclude: tuple[str, ...] = DEFAULT_EXCLUDE
    plugins: str = PLUGINS
    disabled: tuple[str, ...] = ()
    remote: RemoteConfig | None = None
    include: tuple[str, ...] = ALL_ITEMS
    """Selectors of the items this source offers; everything else is left out."""

    def includes(self, item: Customization) -> bool:
        """Return True if ``item`` matches one of the source's ``include`` selectors."""
        return any(
            fnmatchcase(selector, pattern)
            for selector in item.selectors
            for pattern in self.include
        )

    def disables(self, item: Customization) -> bool:
        """Match source-owned items against case-sensitive canonical selectors."""
        return item.source == self.name and any(
            fnmatchcase(selector, pattern)
            for selector in item.selectors
            for pattern in self.disabled
        )


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
        included = self._matches(item, self.include)
        excluded = self._matches(item, self.exclude)
        return included and not excluded

    @staticmethod
    def _matches(item: Customization, patterns: tuple[str, ...]) -> bool:
        """Return True if any of ``item``'s selectors matches any pattern."""
        return any(
            fnmatchcase(selector, pattern)
            for selector in item.selectors
            for pattern in patterns
        )


@dataclass(frozen=True, slots=True)
class Config:
    """A validated configuration file."""

    path: Path
    sources: Mapping[str, SourceConfig]
    destinations: Mapping[str, DestinationConfig]
    routes: tuple[RouteConfig, ...]


class Table:
    """Typed, strict accessor over one JSON object; reports unknown keys."""

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
        """Return a nested object, or None when absent."""
        value = self._take(key)
        if value is None:
            return None
        return self._child(value, f"{self._where}.{key}")

    def tables(self, key: str) -> dict[str, Table]:
        """Return an object of named sub-objects."""
        value = self._take(key)
        if value is None:
            return {}
        if not isinstance(value, dict):
            self._fail(key, "an object")
        return {
            str(name): self._child(entry, f"{self._where}.{key}.{name}")
            for name, entry in value.items()
        }

    def table_list(self, key: str) -> list[Table]:
        """Return an array of objects."""
        value = self._take(key)
        if value is None:
            return []
        if not isinstance(value, list):
            self._fail(key, "an array of objects")
        return [
            self._child(entry, f"{self._where}.{key}[{index}]")
            for index, entry in enumerate(value)
        ]

    def finish(self) -> None:
        """Raise if the object holds keys nobody read (usually typos)."""
        unknown = sorted(set(self._data) - self._read)
        if unknown:
            msg = f"{self._where}: unknown key(s): {', '.join(unknown)}"
            raise ConfigurationError(msg)

    # Internals

    def _take(self, key: str) -> object:
        """Mark ``key`` as read and return its raw value, refusing an explicit null."""
        self._read.add(key)
        if key in self._data and self._data[key] is None:
            msg = f"{self._where}.{key}: null is not allowed; omit the key instead"
            raise ConfigurationError(msg)
        return self._data.get(key)

    def _child(self, value: object, where: str) -> Table:
        """Wrap a nested object, rejecting other values."""
        if not isinstance(value, dict):
            msg = f"{where}: expected an object"
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
        remote = self._remote(name, table)
        if raw is None and remote is None:
            msg = (
                f"sources.{name}.path is required, unless github or git names a remote"
            )
            raise ConfigurationError(msg)
        if raw is not None and remote is not None:
            msg = f"sources.{name} needs exactly one of {', '.join(LOCATIONS)}"
            raise ConfigurationError(msg)
        layout, plugins = self._layout(table.table("layout"))
        exclude = table.strings("exclude", DEFAULT_EXCLUDE)
        include = self._selectors(name, table, "include", ALL_ITEMS)
        if not include:
            msg = (
                f"sources.{name}.include: list at least one selector, or remove"
                " the routes that read this source"
            )
            raise ConfigurationError(msg)
        disabled = self._selectors(name, table, "disabled")
        table.finish()
        if remote is not None:
            path = self._environment.remote_cache / remote.cache_key(name)
        else:
            path = self._environment.expand(str(raw), base)
        return SourceConfig(
            name, path, layout, exclude, plugins, disabled, remote, include
        )

    @classmethod
    def _remote(cls, name: str, table: Table) -> RemoteConfig | None:
        """Build a source's remote settings, or None for a local source."""
        where = f"sources.{name}"
        github = table.string("github")
        url = table.string("git")
        ref = table.string("ref")
        subdirectory = table.string("subdirectory")
        refresh = table.string("refresh")
        if github is None and url is None:
            extra = [
                key
                for key, value in (
                    ("ref", ref),
                    ("subdirectory", subdirectory),
                    ("refresh", refresh),
                )
                if value is not None
            ]
            if extra:
                msg = f"{where}: {', '.join(extra)} applies only to github or git"
                raise ConfigurationError(msg)
            return None
        if github is not None and url is not None:
            msg = f"{where} needs exactly one of {', '.join(LOCATIONS)}"
            raise ConfigurationError(msg)
        if github is not None:
            if not GITHUB_REPOSITORY.match(github) or github.endswith(".git"):
                msg = f"{where}.github: expected 'owner/repo', got {github!r}"
                raise ConfigurationError(msg)
            url, label = f"https://github.com/{github}.git", github
        else:
            url = cls._url(where, str(url))
            label = mask(url)
        return RemoteConfig(
            url=url,
            label=label,
            ref=cls._ref(where, ref),
            subdirectory=cls._subdirectory(where, subdirectory or ""),
            refresh=cls._refresh(where, refresh),
            github=github is not None,
        )

    @staticmethod
    def _url(where: str, url: str) -> str:
        """Refuse URLs that embed credentials, without echoing them.

        Git's credentials belong in a credential helper, an SSH key, or a
        token variable, never in configuration, reports, or cache state.
        """
        if mask(url) != url:
            msg = (
                f"{where}.git: do not put credentials or a user name in the URL;"
                " use a Git credential helper, an SSH key, or GH_TOKEN for"
                " github sources"
            )
            raise ConfigurationError(msg)
        if url.startswith("-") or any(char.isspace() for char in url):
            msg = f"{where}.git: invalid repository URL {mask(url)!r}"
            raise ConfigurationError(msg)
        return url

    @staticmethod
    def _ref(where: str, ref: str | None) -> str | None:
        """Refuse refs Git would read as an option or that cannot name a ref."""
        if ref is not None and (
            ref.startswith("-")
            or any(char.isspace() or char in "~^:?*[\\" for char in ref)
            or ".." in ref
        ):
            msg = f"{where}.ref: invalid branch, tag, or commit {ref!r}"
            raise ConfigurationError(msg)
        return ref

    @staticmethod
    def _subdirectory(where: str, subdirectory: str) -> str:
        """Return a relative, forward-slash path inside the repository."""
        parts = [part for part in subdirectory.split("/") if part]
        if (
            "\\" in subdirectory
            or subdirectory.startswith("/")
            or any(part in {".", ".."} for part in parts)
            or ":" in subdirectory
        ):
            msg = (
                f"{where}.subdirectory: expected a relative path inside the"
                f" repository using '/', got {subdirectory!r}"
            )
            raise ConfigurationError(msg)
        return "/".join(parts)

    @staticmethod
    def _refresh(where: str, refresh: str | None) -> timedelta:
        """Parse the minimum time between remote checks."""
        if refresh is None:
            return timedelta(0)
        try:
            return parse_interval(refresh)
        except ValueError as err:
            msg = f"{where}.refresh: {err}"
            raise ConfigurationError(msg) from err

    @staticmethod
    def _selectors(
        name: str, table: Table, key: str, default: tuple[str, ...] = ()
    ) -> tuple[str, ...]:
        """Validate item selectors rather than interpreting them as filesystem paths."""
        selectors = table.strings(key, default)
        for selector in selectors:
            parts = selector.split("/")
            match parts:
                case [kind, _] | ["plugins", _, kind, _]:
                    shaped = kind in SELECTOR_KINDS
                case _:
                    shaped = False
            canonical = "\\" not in selector and all(
                part.strip() and part not in {".", ".."} for part in parts
            )
            if not canonical or not shaped:
                msg = (
                    f"sources.{name}.{key}: invalid selector {selector!r}; "
                    "use 'kind/name' or 'plugins/plugin/kind/name' with a supported "
                    "kind (or '*'), nonempty components, and forward slashes; "
                    "'.' and '..' components are not allowed"
                )
                raise ConfigurationError(msg)
        return selectors

    @staticmethod
    def _layout(table: Table | None) -> tuple[dict[Kind, str], str]:
        """Return each kind's and the plugins directory from ``layout``."""
        layout = {kind: kind.value for kind in Kind}
        if table is None:
            return layout, PLUGINS
        for kind in Kind:
            directory = table.string(kind.value)
            if directory is not None:
                layout[kind] = directory
        plugins = table.string(PLUGINS, PLUGINS) or PLUGINS
        table.finish()
        return layout, plugins

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
        destinations: list[tuple[Path, DestinationConfig]] = []
        for destination in config.destinations.values():
            try:
                resolved = destination.path.resolve()
            except (OSError, RuntimeError) as err:
                msg = (
                    f"cannot resolve destination '{destination.name}'"
                    f" ({destination.path}): {err}"
                )
                raise ConfigurationError(msg) from err
            destinations.append((resolved, destination))
        destinations.sort(key=lambda entry: entry[0])
        for (first_path, first), (second_path, second) in pairwise(destinations):
            if second_path == first_path or second_path.is_relative_to(first_path):
                msg = (
                    f"destinations '{first.name}' and '{second.name}' overlap"
                    f" ({first.path} and {second.path})"
                )
                raise ConfigurationError(msg)

    # Internals

    @staticmethod
    def _read(path: Path) -> dict[str, object]:
        """Read and parse the JSONC file: JSON plus comments and trailing commas."""
        if path.suffix == ".toml":
            msg = f"{path}: {TOML_RETIRED}"
            raise ConfigurationError(msg)
        try:
            text = path.read_text(encoding="utf-8-sig")
        except FileNotFoundError as err:
            legacy = path.with_suffix(".toml")
            hint = TOML_RETIRED if legacy.is_file() else "run `skillsinc init`"
            msg = f"configuration file not found: {path} ({hint})"
            raise ConfigurationError(msg) from err
        try:
            plain = Jsonc.blank_trailing_commas(Jsonc.blank_comments(text))
            data = JsonDocument.load(plain.encode())
        except ValueError as err:
            msg = f"{path}: invalid JSONC: {err}"
            raise ConfigurationError(msg) from err
        return dict(data)
