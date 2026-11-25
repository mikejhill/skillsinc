"""settings.json: typed loading, precise errors, and comment-keeping saves."""

from __future__ import annotations

import json
import stat
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from hypothesis import given
from hypothesis import strategies as st
from jsonschema import Draft202012Validator

from skillsinc.jsonc import Jsonc
from skillsinc.paths import Environment
from skillsinc.schema import SETTINGS_SCHEMA_FILE, SettingsSchema
from skillsinc.usersettings import (
    HistoryOptions,
    NotificationOptions,
    ScheduleOptions,
    SettingsFileError,
    SettingsStore,
    StartupOptions,
    UpdateOptions,
    UserSettings,
)

COMMENTED = """\
// My SkillSinc tray settings
{
  "$schema": "./settings.schema.json",
  "version": 1,
  "schedule": {
    // Every ten minutes is plenty
    "interval": "10m",
    "profiles": ["work"],
  },
  /* Quiet, please */
  "notifications": { "failures": false },
}
"""


def _schema() -> dict[str, Any]:
    """Return the bundled settings schema."""
    return cast("dict[str, Any]", json.loads(SettingsSchema.text()))


def _schema_errors(document: object) -> list[str]:
    """Return schema validation messages for ``document``."""
    validator = Draft202012Validator(_schema())
    return [error.message for error in validator.iter_errors(document)]


class TestParse:
    """Every key reads into its typed field; omissions take defaults."""

    def test_missing_file_is_every_default(self, tmp_path: Path) -> None:
        """No settings.json means defaults, and nothing is written."""
        store = SettingsStore(tmp_path / "settings.json")

        assert store.load() == UserSettings(), "Expected defaults"
        assert not store.exists(), "Expected no file written by a load"

    def test_defaults_match_the_documented_values(self) -> None:
        """Five-minute schedule at start, failures only, check but don't install."""
        settings = UserSettings()

        assert settings.schedule == ScheduleOptions(
            enabled=True,
            interval=timedelta(minutes=5),
            profiles=None,
            run_at_start=True,
        ), f"Got {settings.schedule!r}"
        assert settings.notifications == NotificationOptions(
            failures=True, changes=False
        )
        assert settings.updates == UpdateOptions(check=True, install=False)
        assert settings.startup == StartupOptions(launch_at_login=False)
        assert settings.history == HistoryOptions(keep=500)

    def test_commented_file_reads_with_defaults_for_the_rest(self) -> None:
        """Comments and trailing commas are allowed, as in config.json."""
        settings = SettingsStore.parse(COMMENTED)

        assert settings.schedule.interval == timedelta(minutes=10)
        assert settings.schedule.profiles == ("work",)
        assert settings.notifications.failures is False
        assert settings.notifications.changes is False, "Expected default"
        assert settings.updates == UpdateOptions(), "Expected default updates"

    def test_empty_file_is_every_default(self) -> None:
        """A blank file is an empty object."""
        assert SettingsStore.parse("  \n") == UserSettings()

    def test_null_profiles_select_the_active_profiles(self) -> None:
        """``null`` means the profiles active on this machine."""
        settings = SettingsStore.parse('{"schedule": {"profiles": null}}')

        assert settings.schedule.profiles is None

    def test_config_path_resolves_beside_settings_json(
        self, environment: Environment
    ) -> None:
        """A relative ``config`` is beside settings.json; ``~`` is the home."""
        relative = SettingsStore.parse('{"config": "work/config.json"}')
        home = SettingsStore.parse('{"config": "~/skillsinc.json"}')

        assert relative.config_file(environment) == (
            environment.default_config_file.parent / "work" / "config.json"
        )
        assert home.config_file(environment) == environment.home / "skillsinc.json"
        assert UserSettings().config_file(environment) == (
            environment.default_config_file
        )

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("[]", "the file must hold an object"),
            ("{", "not valid JSON"),
            ('{"version": 2}', "version must be 1"),
            ('{"surprise": 1}', "surprise is not a setting"),
            ('{"schedule": {"often": true}}', "schedule.often is not a setting"),
            ('{"schedule": 5}', "schedule must be an object"),
            ('{"schedule": {"enabled": "yes"}}', "schedule.enabled must be true"),
            ('{"schedule": {"interval": "soon"}}', "invalid interval"),
            ('{"schedule": {"interval": "30s"}}', "must be from 1m to 31d"),
            ('{"schedule": {"interval": 5}}', "schedule.interval must be a non-empty"),
            ('{"schedule": {"profiles": "work"}}', "must be a list of names"),
            ('{"schedule": {"profiles": [""]}}', "must be a list of names"),
            ('{"schedule": {"profiles": ["a", "a"]}}', "must not repeat a name"),
            ('{"history": {"keep": 5}}', "history keep must be 10-100000"),
            ('{"history": {"keep": "all"}}', "history.keep must be a whole number"),
            ('{"history": {"keep": true}}', "history.keep must be a whole number"),
            ('{"config": ""}', "config must be a non-empty string"),
            ('{"schedule": {"enabled": null}}', "schedule.enabled must not be null"),
            ('{"history": {"keep": null}}', "history.keep must not be null"),
            ('{"schedule": null}', "schedule must not be null"),
            ('{"schedule": {"interval": "1000000000d"}}', "settings:"),
            pytest.param("[" * 100_000 + "]" * 100_000, "not valid JSON", id="nested"),
        ],
    )
    def test_invalid_values_name_the_key(self, text: str, message: str) -> None:
        """Errors say which setting is wrong and why."""
        with pytest.raises(SettingsFileError, match=message):
            SettingsStore.parse(text)

    def test_unreadable_file_is_a_settings_error(self, tmp_path: Path) -> None:
        """Bytes that are not UTF-8 are reported, not raised raw."""
        path = tmp_path / "settings.json"
        path.write_bytes(b"\xff\xfe{")

        with pytest.raises(SettingsFileError, match="cannot read"):
            SettingsStore(path).load()

    def test_oversized_file_is_refused_unread(self, tmp_path: Path) -> None:
        """A huge file is not a settings file."""
        path = tmp_path / "settings.json"
        path.write_text("{" + " " * (1 << 20) + "}", encoding="utf-8")

        with pytest.raises(SettingsFileError, match="too large"):
            SettingsStore(path).load()

    def test_short_interval_is_refused_by_the_model(self) -> None:
        """The model refuses an interval under a minute, however it is built."""
        with pytest.raises(ValueError, match="at least 1m"):
            ScheduleOptions(interval=timedelta(seconds=30))


class TestSave:
    """Saving writes every value, keeps comments, and round-trips."""

    def test_new_file_holds_every_setting_and_the_schema(self, tmp_path: Path) -> None:
        """A first save writes $schema, every key, and the schema copy."""
        store = SettingsStore(tmp_path / "skillsinc" / "settings.json")

        assert store.save(UserSettings()) is True, "Expected the file written"

        data = json.loads(store.path.read_text(encoding="utf-8"))
        assert data["$schema"] == f"./{SETTINGS_SCHEMA_FILE}"
        assert data == {"$schema": data["$schema"], **UserSettings().to_json()}
        schema = store.path.parent / SETTINGS_SCHEMA_FILE
        assert schema.read_text(encoding="utf-8") == SettingsSchema.text()
        assert store.load() == UserSettings(), "Expected a round trip"

    def test_change_keeps_comments_and_layout(self, tmp_path: Path) -> None:
        """Only changed values are spliced; every comment stays."""
        path = tmp_path / "settings.json"
        path.write_text(COMMENTED, encoding="utf-8")
        store = SettingsStore(path)
        settings = store.load()
        changed = UserSettings(
            schedule=ScheduleOptions(
                interval=timedelta(hours=1), profiles=settings.schedule.profiles
            ),
            notifications=settings.notifications,
        )

        store.save(changed)

        text = path.read_text(encoding="utf-8")
        for comment in ("// My SkillSinc", "// Every ten minutes", "/* Quiet"):
            assert comment in text, f"Expected {comment!r} kept: {text}"
        assert '"interval": "1h"' in text, f"Expected the new interval: {text}"
        assert store.load() == changed, "Expected a round trip"

    def test_unchanged_save_does_not_write(self, tmp_path: Path) -> None:
        """Saving what is already there leaves the file alone."""
        store = SettingsStore(tmp_path / "settings.json")
        store.save(UserSettings())
        before = store.path.stat().st_mtime_ns

        assert store.save(UserSettings()) is False, "Expected no write"
        assert store.path.stat().st_mtime_ns == before

    def test_byte_order_mark_is_kept(self, tmp_path: Path) -> None:
        """A BOM written by an editor survives a save."""
        path = tmp_path / "settings.json"
        path.write_text("﻿{}", encoding="utf-8")

        SettingsStore(path).save(
            UserSettings(updates=UpdateOptions(check=False, install=False))
        )

        assert path.read_bytes().startswith(b"\xef\xbb\xbf"), "Expected the BOM"
        assert SettingsStore(path).load().updates.check is False

    def test_blank_existing_file_is_filled(self, tmp_path: Path) -> None:
        """An empty file is treated as an empty object."""
        path = tmp_path / "settings.json"
        path.write_text("\n", encoding="utf-8")

        SettingsStore(path).save(UserSettings())

        assert SettingsStore(path).load() == UserSettings()

    def test_saving_over_a_non_object_refuses(self, tmp_path: Path) -> None:
        """A file that is not an object is not overwritten."""
        path = tmp_path / "settings.json"
        path.write_text("[1, 2]", encoding="utf-8")

        with pytest.raises(SettingsFileError, match="not a JSON object"):
            SettingsStore(path).save(UserSettings())
        assert path.read_text(encoding="utf-8") == "[1, 2]", "Expected file kept"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
    def test_save_keeps_the_file_permissions(self, tmp_path: Path) -> None:
        """A private settings file stays private after a save."""
        path = tmp_path / "settings.json"
        path.write_text("{}", encoding="utf-8")
        path.chmod(0o600)

        SettingsStore(path).save(UserSettings())

        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_concurrent_edit_is_not_overwritten(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An editor's save between read and replace wins; the save refuses."""
        path = tmp_path / "settings.json"
        path.write_text("{}", encoding="utf-8")

        def edit(_target: Path) -> bool:
            path.write_text('{"version": 1}', encoding="utf-8")
            return False

        monkeypatch.setattr(SettingsSchema, "refresh", edit)

        with pytest.raises(SettingsFileError, match="changed while saving"):
            SettingsStore(path).save(UserSettings())
        assert path.read_text(encoding="utf-8") == '{"version": 1}'

    def test_file_created_meanwhile_is_not_overwritten(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A file that appears during a first save is left alone."""
        path = tmp_path / "settings.json"

        def create(_target: Path) -> bool:
            path.write_text("{}", encoding="utf-8")
            return False

        monkeypatch.setattr(SettingsSchema, "refresh", create)

        with pytest.raises(SettingsFileError, match="changed while saving"):
            SettingsStore(path).save(UserSettings())
        assert path.read_text(encoding="utf-8") == "{}"

    def test_store_lives_beside_the_user_configuration(
        self, environment: Environment
    ) -> None:
        """settings.json is next to config.json."""
        store = SettingsStore.default(environment)

        assert store.path == environment.default_config_file.parent / "settings.json"

    @given(
        enabled=st.booleans(),
        minutes=st.integers(min_value=1, max_value=60 * 24),
        profiles=st.none()
        | st.lists(
            st.text(alphabet="abcdefghij-_", min_size=1, max_size=8),
            unique=True,
            max_size=4,
        ),
        flags=st.lists(st.booleans(), min_size=6, max_size=6),
        keep=st.integers(min_value=10, max_value=100_000),
    )
    def test_any_settings_round_trip_and_match_the_schema(
        self,
        enabled: bool,  # noqa: FBT001 - Hypothesis argument
        minutes: int,
        profiles: list[str] | None,
        flags: list[bool],
        keep: int,
    ) -> None:
        """Whatever is saved loads back equal and satisfies the bundled schema."""
        settings = UserSettings(
            config=Path("work.json") if flags[5] else None,
            schedule=ScheduleOptions(
                enabled=enabled,
                interval=timedelta(minutes=minutes),
                profiles=tuple(profiles) if profiles is not None else None,
                run_at_start=flags[0],
            ),
            notifications=NotificationOptions(failures=flags[1], changes=flags[2]),
            updates=UpdateOptions(check=flags[3], install=flags[4]),
            startup=StartupOptions(launch_at_login=flags[5]),
            history=HistoryOptions(keep=keep),
        )
        document = settings.to_json()
        text = json.dumps(document)

        assert SettingsStore.parse(text) == settings, "Expected a round trip"
        assert _schema_errors(document) == [], "Expected the schema to accept it"


class TestSchema:
    """The bundled schema describes exactly what the loader accepts."""

    def test_schema_is_valid_and_allows_comments(self) -> None:
        """VS Code reads allowComments and allowTrailingCommas from the schema."""
        schema = _schema()
        Draft202012Validator.check_schema(schema)
        assert schema["allowComments"] is True
        assert schema["allowTrailingCommas"] is True

    def test_schema_keys_match_the_model(self) -> None:
        """Every section and key the model writes is in the schema, and no more."""
        properties = _schema()["properties"]
        written = UserSettings().to_json()

        assert set(properties) == {"$schema", *written}, f"Got {sorted(properties)}"
        for section, values in written.items():
            if isinstance(values, dict):
                keys = set(properties[section]["properties"])
                assert keys == set(values), f"{section}: {keys} != {set(values)}"

    def test_schema_defaults_match_the_model(self) -> None:
        """Each documented default is the model's default."""
        properties = _schema()["properties"]
        for section, values in UserSettings().to_json().items():
            if not isinstance(values, dict):
                continue
            for key, value in values.items():
                documented = properties[section]["properties"][key]["default"]
                assert documented == value, f"{section}.{key}: {documented}"

    @pytest.mark.parametrize(
        "document",
        [
            {"version": 2},
            {"schedule": {"profiles": ["a", "a"]}},
            {"history": {"keep": 5}},
            {"startup": {"launch": True}},
        ],
    )
    def test_schema_rejects_what_the_loader_rejects(
        self, document: dict[str, object]
    ) -> None:
        """Editors flag the same mistakes the tray reports."""
        assert _schema_errors(document), f"Expected {document} rejected"
        with pytest.raises(SettingsFileError):
            SettingsStore.parse(json.dumps(document))


class TestJsoncSetPath:
    """Setting a nested member splices text and keeps everything else."""

    def test_replaces_a_nested_value_in_place(self) -> None:
        """Only the value's text changes."""
        text = '{\n  "a": {\n    // note\n    "b": 1,\n  },\n}\n'

        updated = Jsonc.set_path(text, ["a", "b"], 2)

        assert updated == text.replace('"b": 1', '"b": 2'), updated

    def test_creates_missing_objects(self) -> None:
        """Missing parents are created as nested objects."""
        updated = Jsonc.set_path("{}", ["a", "b"], value=True)

        assert json.loads(updated) == {"a": {"b": True}}

    def test_replaces_a_non_object_parent(self) -> None:
        """A scalar where an object belongs becomes the object."""
        updated = Jsonc.set_path('{"a": 1}', ["a", "b"], "x")

        assert json.loads(updated) == {"a": {"b": "x"}}

    def test_equal_value_leaves_text_alone(self) -> None:
        """Setting the current value returns the same text, even reformatted."""
        text = '{"a": [1,2,],}'

        assert Jsonc.set_path(text, ["a"], [1, 2]) is text

    def test_empty_path_is_refused(self) -> None:
        """There is no member at the root."""
        with pytest.raises(ValueError, match="non-empty path"):
            Jsonc.set_path("{}", [], 1)
