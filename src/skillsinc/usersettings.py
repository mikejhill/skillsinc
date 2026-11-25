"""User settings for SkillSinc as an application: ``settings.json``.

``config.json`` says *what* to sync. ``settings.json``, beside it, says how
SkillSinc behaves on this machine when it runs as its own scheduler: how
often to sync and which profiles, which notifications to show, whether to
check for updates, and whether to start at sign-in. The tray reads and writes
it; the command line never needs it.

The file is JSONC like ``config.json``. Saving changes only the values that
changed, so comments and formatting stay.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Final, NoReturn

from skillsinc.exceptions import ConfigurationError
from skillsinc.jsonc import Jsonc
from skillsinc.models import JsonValue
from skillsinc.paths import Environment
from skillsinc.schedule import format_interval, parse_interval
from skillsinc.schema import BOM, SettingsSchema, write_atomically

SETTINGS_VERSION: Final = 1
SETTINGS_FILE: Final = "settings.json"
DEFAULT_SCHEDULE_INTERVAL: Final = timedelta(minutes=5)
MINIMUM_SCHEDULE_INTERVAL: Final = timedelta(minutes=1)
DEFAULT_HISTORY_KEEP: Final = 500
HISTORY_KEEP_RANGE: Final = (10, 100_000)
# A settings file larger than this is not a settings file; never read it whole
MAXIMUM_SETTINGS_BYTES: Final = 1 << 20


class SettingsFileError(ConfigurationError):
    """``settings.json`` is unreadable, malformed, or holds an invalid value."""


@dataclass(frozen=True, slots=True)
class ScheduleOptions:
    """When the tray syncs on its own."""

    enabled: bool = True
    interval: timedelta = DEFAULT_SCHEDULE_INTERVAL
    profiles: tuple[str, ...] | None = None
    """Profiles each scheduled sync selects; None selects the active ones."""
    run_at_start: bool = True
    """Sync as soon as the tray starts, then every ``interval``."""

    def __post_init__(self) -> None:
        """Reject intervals shorter than a minute.

        Raises:
            ValueError: If ``interval`` is shorter than a minute.
        """
        if self.interval < MINIMUM_SCHEDULE_INTERVAL:
            msg = (
                "schedule interval must be at least"
                f" {format_interval(MINIMUM_SCHEDULE_INTERVAL)},"
                f" got {format_interval(self.interval)}"
            )
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class NotificationOptions:
    """Which tray notifications appear after a sync."""

    failures: bool = True
    changes: bool = False
    """Also notify after a sync that changed files."""


@dataclass(frozen=True, slots=True)
class UpdateOptions:
    """Whether the tray looks for, and installs, new SkillSinc releases."""

    check: bool = True
    install: bool = False
    """Install a release found by an automatic check without asking."""


@dataclass(frozen=True, slots=True)
class StartupOptions:
    """Whether the tray starts when the user signs in."""

    launch_at_login: bool = False


@dataclass(frozen=True, slots=True)
class HistoryOptions:
    """How many sync runs the history keeps."""

    keep: int = DEFAULT_HISTORY_KEEP

    def __post_init__(self) -> None:
        """Keep the history size within its range.

        Raises:
            ValueError: If ``keep`` is outside ``HISTORY_KEEP_RANGE``.
        """
        low, high = HISTORY_KEEP_RANGE
        if not low <= self.keep <= high:
            msg = f"history keep must be {low}-{high}, got {self.keep}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class UserSettings:
    """Everything in ``settings.json``."""

    config: Path | None = None
    """The configuration the tray syncs; None is the user configuration."""
    schedule: ScheduleOptions = field(default_factory=ScheduleOptions)
    notifications: NotificationOptions = field(default_factory=NotificationOptions)
    updates: UpdateOptions = field(default_factory=UpdateOptions)
    startup: StartupOptions = field(default_factory=StartupOptions)
    history: HistoryOptions = field(default_factory=HistoryOptions)

    def to_json(self) -> dict[str, JsonValue]:
        """Return the settings as the JSON ``settings.json`` holds."""
        schedule = self.schedule
        return {
            "version": SETTINGS_VERSION,
            "config": str(self.config) if self.config is not None else None,
            "schedule": {
                "enabled": schedule.enabled,
                "interval": format_interval(schedule.interval),
                "profiles": list(schedule.profiles)
                if schedule.profiles is not None
                else None,
                "run_at_start": schedule.run_at_start,
            },
            "notifications": {
                "failures": self.notifications.failures,
                "changes": self.notifications.changes,
            },
            "updates": {
                "check": self.updates.check,
                "install": self.updates.install,
            },
            "startup": {"launch_at_login": self.startup.launch_at_login},
            "history": {"keep": self.history.keep},
        }

    def config_file(self, environment: Environment) -> Path:
        """Return the configuration file these settings sync."""
        if self.config is None:
            return environment.default_config_file
        return environment.expand(str(self.config), environment.config_home)


class _Reader:
    """Read typed values from one parsed object, naming bad keys precisely."""

    def __init__(self, data: dict[str, JsonValue], where: str) -> None:
        self._data = data
        self._where = where
        self._seen: set[str] = set()

    def section(self, key: str) -> _Reader:
        """Return a reader for the object at ``key`` (empty when absent)."""
        value = self._take(key)
        if value is None:
            return _Reader({}, self._name(key))
        if not isinstance(value, dict):
            self._fail(key, "must be an object")
        return _Reader(value, self._name(key))

    def boolean(self, key: str, *, default: bool) -> bool:
        """Return the boolean at ``key``, else ``default``."""
        value = self._take(key)
        if value is None:
            return default
        if not isinstance(value, bool):
            self._fail(key, "must be true or false")
        return value

    def integer(self, key: str, default: int) -> int:
        """Return the integer at ``key``, else ``default``."""
        value = self._take(key)
        if value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, int):
            self._fail(key, "must be a whole number")
        return value

    def text(self, key: str) -> str | None:
        """Return the non-empty string at ``key``, else None."""
        value = self._take(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            self._fail(key, "must be a non-empty string")
        return value

    def names(self, key: str) -> tuple[str, ...] | None:
        """Return the list of distinct non-empty strings at ``key``, else None."""
        value = self._take(key)
        if value is None:
            return None
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            self._fail(key, "must be a list of names, or null")
        names = tuple(str(item) for item in value)
        if len(set(names)) != len(names):
            self._fail(key, "must not repeat a name")
        return names

    def finish(self) -> None:
        """Reject keys nothing read."""
        unknown = sorted(set(self._data) - self._seen - {"$schema"})
        if unknown:
            self._fail(unknown[0], "is not a setting")

    def fail(self, key: str, problem: str) -> NoReturn:
        """Raise a precise error about ``key``."""
        self._fail(key, problem)

    def _take(self, key: str) -> JsonValue | None:
        self._seen.add(key)
        return self._data.get(key)

    def _name(self, key: str) -> str:
        return f"{self._where}.{key}" if self._where else key

    def _fail(self, key: str, problem: str) -> NoReturn:
        raise SettingsFileError(f"settings: {self._name(key)} {problem}")


class SettingsStore:
    """Load and save ``settings.json``, keeping its comments and layout."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @classmethod
    def beside(cls, config: Path) -> SettingsStore:
        """Return the store for the ``settings.json`` next to ``config``."""
        return cls(config.parent / SETTINGS_FILE)

    @classmethod
    def default(cls, environment: Environment) -> SettingsStore:
        """Return the store beside the user configuration."""
        return cls.beside(environment.default_config_file)

    @property
    def path(self) -> Path:
        """Return the settings file."""
        return self._path

    def exists(self) -> bool:
        """Return True if the settings file exists."""
        return self._path.is_file()

    def load(self) -> UserSettings:
        """Return the saved settings, or the defaults when there is no file.

        Raises:
            SettingsFileError: If the file is unreadable or invalid.
        """
        if not self._path.exists():
            return UserSettings()
        return self.parse(self._read().removeprefix(BOM))

    @staticmethod
    def parse(text: str) -> UserSettings:
        """Return the settings in JSONC ``text``.

        Raises:
            SettingsFileError: If ``text`` is malformed or holds an invalid value.
        """
        try:
            plain = Jsonc.blank_trailing_commas(Jsonc.blank_comments(text))
            data: object = json.loads(plain) if plain.strip() else {}
        except ValueError as err:
            msg = f"settings: not valid JSON ({err})"
            raise SettingsFileError(msg) from err
        if not isinstance(data, dict):
            msg = "settings: the file must hold an object"
            raise SettingsFileError(msg)
        return SettingsStore._settings(_Reader(data, ""))

    def save(self, settings: UserSettings) -> bool:
        """Write ``settings``, changing only values that differ; True if written.

        A new file starts with ``$schema`` naming the schema copy, which is
        refreshed beside it.

        Raises:
            SettingsFileError: If the existing file is unreadable or not an object.
            OSError: If the file cannot be written.
        """
        values = settings.to_json()
        if self._path.exists():
            raw = self._read()
            bom = BOM if raw.startswith(BOM) else ""
            text = raw.removeprefix(BOM)
            if not text.strip():
                text = "{}\n"
        else:
            bom = ""
            reference = SettingsSchema.local_reference()
            text = json.dumps({"$schema": reference}, indent=2) + "\n"
        updated = text
        try:
            for path, value in _leaves(values):
                updated = Jsonc.set_path(updated, path, value)
        except ValueError as err:
            msg = f"settings: {self._path} is not a JSON object ({err})"
            raise SettingsFileError(msg) from err
        if self.parse(updated) != settings:  # pragma: no cover - guard
            msg = "settings: saving would not round-trip"
            raise SettingsFileError(msg)
        SettingsSchema.refresh(self._path)
        if updated == text and self._path.exists():
            return False
        write_atomically(self._path, bom + updated)
        return True

    def _read(self) -> str:
        try:
            if self._path.stat().st_size > MAXIMUM_SETTINGS_BYTES:
                msg = f"settings: {self._path} is too large to be a settings file"
                raise SettingsFileError(msg)
            return self._path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as err:
            msg = f"settings: cannot read {self._path} ({err})"
            raise SettingsFileError(msg) from err

    @staticmethod
    def _settings(root: _Reader) -> UserSettings:
        version = root.integer("version", SETTINGS_VERSION)
        if version != SETTINGS_VERSION:
            root.fail("version", f"must be {SETTINGS_VERSION}")
        config = root.text("config")
        schedule = root.section("schedule")
        notifications = root.section("notifications")
        updates = root.section("updates")
        startup = root.section("startup")
        history = root.section("history")
        defaults = UserSettings()
        try:
            interval_text = schedule.text("interval")
            interval = (
                parse_interval(interval_text)
                if interval_text is not None
                else defaults.schedule.interval
            )
            result = UserSettings(
                config=Path(config) if config is not None else None,
                schedule=ScheduleOptions(
                    enabled=schedule.boolean("enabled", default=True),
                    interval=interval,
                    profiles=schedule.names("profiles"),
                    run_at_start=schedule.boolean("run_at_start", default=True),
                ),
                notifications=NotificationOptions(
                    failures=notifications.boolean("failures", default=True),
                    changes=notifications.boolean("changes", default=False),
                ),
                updates=UpdateOptions(
                    check=updates.boolean("check", default=True),
                    install=updates.boolean("install", default=False),
                ),
                startup=StartupOptions(
                    launch_at_login=startup.boolean("launch_at_login", default=False)
                ),
                history=HistoryOptions(
                    keep=history.integer("keep", DEFAULT_HISTORY_KEEP)
                ),
            )
        except ValueError as err:
            msg = f"settings: {err}"
            raise SettingsFileError(msg) from err
        for reader in (root, schedule, notifications, updates, startup, history):
            reader.finish()
        return result


def _leaves(
    values: dict[str, JsonValue], prefix: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], JsonValue]]:
    """Yield each non-object value in ``values`` with its key path."""
    for key, value in values.items():
        path = (*prefix, key)
        if isinstance(value, dict):
            yield from _leaves(value, path)
        else:
            yield path, value
