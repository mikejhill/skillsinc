"""The settings window: edit settings.json and config.json, then save both."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QTableWidget,
    QWidget,
)
from pytestqt.qtbot import QtBot
from tests.tray.conftest import Tray, add_profiles

from skillsinc.jsonc import Jsonc
from skillsinc.models import JsonValue
from skillsinc.tray import settings_window as module
from skillsinc.tray.settings_window import (
    ElementDialog,
    Field,
    SectionTab,
    SettingsWindow,
    cell,
)
from skillsinc.usersettings import ScheduleOptions, SettingsStore, UserSettings

pytestmark = pytest.mark.ux


def _config(tray: Tray) -> Any:  # noqa: ANN401 - tests index freely
    text = tray.sandbox.config_path.read_text(encoding="utf-8")
    return json.loads(Jsonc.blank_trailing_commas(Jsonc.blank_comments(text)))


def _cell(table: QTableWidget, row: int, column: int) -> str:
    item = table.item(row, column)
    assert item is not None, f"No cell at {row}, {column}"
    return item.text()


def _check(listing: QWidget, *names: str) -> None:
    assert isinstance(listing, QListWidget)
    for row in range(listing.count()):
        item = listing.item(row)
        item.setCheckState(
            Qt.CheckState.Checked if item.text() in names else Qt.CheckState.Unchecked
        )


def _text(dialog: ElementDialog, key: str, value: str) -> None:
    widget = dialog.widgets[key]
    if isinstance(widget, QPlainTextEdit):
        widget.setPlainText(value)
        return
    assert isinstance(widget, QLineEdit)
    widget.setText(value)


def _disable_target(window: SettingsWindow) -> None:
    """Make a pending configuration edit: switch the destination off."""
    tab = window.section_tabs["destinations"]
    tab.table.selectRow(0)
    tab.edit()
    dialog = _dialog(tab)
    enabled = dialog.widgets["enabled"]
    assert isinstance(enabled, QCheckBox)
    enabled.setChecked(False)
    dialog.try_accept()


def _dialog(tab: SectionTab) -> ElementDialog:
    assert tab.dialog is not None
    return tab.dialog


@pytest.fixture
def window(tray: Tray) -> Iterator[SettingsWindow]:
    """The settings window, opened from the tray menu."""
    tray.controller.tray.action("settings").trigger()
    opened = tray.controller.settings_window
    assert opened is not None
    yield opened
    opened.close()


class TestGeneral:
    """The General tab round-trips settings.json."""

    def test_shows_the_saved_settings(self, window: SettingsWindow) -> None:
        """Every control starts at the value in settings.json."""
        assert window.interval.text() == "5m"
        assert not window.schedule_enabled.isChecked()
        assert window.notify_failures.isChecked()
        assert window.keep.value() == 500
        assert window.config_path.text() == ""
        assert not window.profiles.isEnabled()

    def test_saving_writes_settings_and_applies_them(
        self, tray: Tray, window: SettingsWindow
    ) -> None:
        """Save writes settings.json and the tray uses it at once."""
        window.schedule_enabled.setChecked(True)
        window.interval.setText("2h")
        window.run_at_start.setChecked(False)
        window.notify_changes.setChecked(True)
        window.install_updates.setChecked(True)
        window.keep.setValue(50)

        assert window.save()

        saved = SettingsStore.default(tray.sandbox.environment).load()
        assert saved.schedule == ScheduleOptions(
            interval=timedelta(hours=2), run_at_start=False
        )
        assert saved.notifications.changes
        assert saved.updates.install
        assert saved.history.keep == 50
        assert tray.controller.settings == saved
        assert tray.controller.tray.state.next_sync == "Next sync in 2 h"
        assert not window.isVisible()

    def test_invalid_interval_keeps_the_window_open(
        self, window: SettingsWindow
    ) -> None:
        """A bad interval is reported and nothing is saved."""
        window.interval.setText("30s")

        assert not window.save()
        assert "Settings:" in window.error.text()
        assert window.isVisible()

    def test_scheduled_profiles_are_chosen_from_the_configuration(
        self, tray: Tray
    ) -> None:
        """Only checked profiles are scheduled when the box is ticked."""
        add_profiles(tray.sandbox.config_path, {"work": {}, "home": {"default": False}})
        tray.controller.show_settings()
        window = tray.controller.settings_window
        assert window is not None
        window.only_profiles.setChecked(True)
        _check(window.profiles, "home")

        assert window.save()

        saved = SettingsStore.default(tray.sandbox.environment).load()
        assert saved.schedule.profiles == ("home",)

    def test_browse_fills_the_configuration_path(
        self, window: SettingsWindow, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Browse… puts the chosen file in the box; cancelling changes nothing."""
        chosen = [str(tmp_path / "other.json"), ""]
        monkeypatch.setattr(
            module.QFileDialog,
            "getOpenFileName",
            staticmethod(lambda *_args: (chosen.pop(0), "")),
        )

        window.browse()
        window.browse()

        assert window.config_path.text() == str(tmp_path / "other.json")
        assert window.settings().config == tmp_path / "other.json"


class TestConfiguration:
    """The element tabs edit config.json; Save validates before writing."""

    def test_tables_list_each_element(self, window: SettingsWindow) -> None:
        """Each tab shows one row per element."""
        sources = window.section_tabs["sources"].table
        routes = window.section_tabs["routes"].table

        assert sources.rowCount() == 1
        assert _cell(sources, 0, 0) == "library"
        assert _cell(routes, 0, 0) == "library"
        assert _cell(routes, 0, 1) == "target"

    def test_add_a_destination_and_route_then_save(
        self, tray: Tray, window: SettingsWindow
    ) -> None:
        """New elements are validated and written; comments stay."""
        destinations = window.section_tabs["destinations"]
        destinations.add()
        dialog = _dialog(destinations)
        assert dialog.name_edit is not None
        dialog.name_edit.setText("extra")
        harness = dialog.widgets["profile"]
        assert isinstance(harness, QComboBox)
        harness.setCurrentText("generic")
        _text(dialog, "path", str(tray.sandbox.root.parent / "extra"))
        dialog.try_accept()

        routes = window.section_tabs["routes"]
        routes.add()
        route = _dialog(routes)
        _check(route.widgets["sources"], "library")
        _check(route.widgets["destinations"], "extra")
        _text(route, "include", "skills/*\nagents/[a,b]*")
        lossy = route.widgets["allow_lossy"]
        assert isinstance(lossy, QCheckBox)
        lossy.setChecked(True)
        route.try_accept()

        assert window.save(), window.error.text()

        data = _config(tray)
        assert data["destinations"]["extra"] == {
            "profile": "generic",
            "path": str(tray.sandbox.root.parent / "extra"),
        }
        assert data["routes"][1] == {
            "sources": ["library"],
            "destinations": ["extra"],
            "include": ["skills/*", "agents/[a,b]*"],
            "allow_lossy": True,
        }
        text = tray.sandbox.config_path.read_text(encoding="utf-8")
        assert text.startswith("// written by the test suite")

    def test_rename_edit_and_remove(self, tray: Tray, window: SettingsWindow) -> None:
        """Renaming a source follows into routes; disabling and removing work."""
        sources = window.section_tabs["sources"]
        sources.table.selectRow(0)
        sources.edit()
        source = _dialog(sources)
        assert source.name_edit is not None
        source.name_edit.setText("mine")
        source.try_accept()
        destinations = window.section_tabs["destinations"]
        destinations.table.selectRow(0)
        destinations.edit()
        destination = _dialog(destinations)
        enabled = destination.widgets["enabled"]
        assert isinstance(enabled, QCheckBox)
        enabled.setChecked(False)
        assert destination.name_edit is not None
        destination.name_edit.setText("renamed")
        destination.try_accept()
        routes = window.section_tabs["routes"]
        routes.table.selectRow(0)
        routes.edit()
        route = _dialog(routes)
        _text(route, "exclude", "")
        route.try_accept()

        assert window.save(), window.error.text()

        data = _config(tray)
        assert list(data["sources"]) == ["mine"]
        assert data["routes"][0]["sources"] == ["mine"]
        assert data["destinations"]["renamed"]["enabled"] is False
        assert data["destinations"]["renamed"]["profile"] == "generic"
        assert data["routes"][0]["destinations"] == ["renamed"]

    def test_invalid_configuration_is_not_written(
        self, tray: Tray, window: SettingsWindow
    ) -> None:
        """Removing the only source fails validation; nothing changes on disk."""
        before = tray.sandbox.config_path.read_text(encoding="utf-8")
        sources = window.section_tabs["sources"]
        sources.table.selectRow(0)
        sources.remove()

        assert not window.save()
        assert "library" in window.error.text()
        assert tray.sandbox.config_path.read_text(encoding="utf-8") == before
        assert window.isVisible()

    def test_profiles_and_route_removal(
        self, tray: Tray, window: SettingsWindow
    ) -> None:
        """A profile can be added and a route removed (then re-added)."""
        profiles = window.section_tabs["profiles"]
        profiles.add()
        profile = _dialog(profiles)
        assert profile.name_edit is not None
        profile.name_edit.setText("work")
        _text(profile, "description", "Work machines")
        profile.try_accept()
        routes = window.section_tabs["routes"]
        kept = _cell(routes.table, 0, 0)
        routes.table.selectRow(0)
        routes.remove()
        assert routes.table.rowCount() == 0
        routes.add()
        route = _dialog(routes)
        _check(route.widgets["sources"], kept)
        _check(route.widgets["destinations"], "target")
        _check(route.widgets["profiles"], "work")
        route.try_accept()

        assert window.save(), window.error.text()

        data = _config(tray)
        assert data["profiles"] == {"work": {"description": "Work machines"}}
        assert data["routes"][0]["profiles"] == ["work"]

    def test_nothing_selected_edits_and_removes_nothing(
        self, window: SettingsWindow
    ) -> None:
        """Edit and Remove need a selected row."""
        tab = window.section_tabs["sources"]
        tab.table.clearSelection()
        tab.table.setCurrentCell(-1, -1)

        tab.edit()
        tab.remove()

        assert tab.dialog is None
        assert tab.table.rowCount() == 1

    def test_names_must_be_given_and_unique(self, window: SettingsWindow) -> None:
        """A missing or repeated name keeps the dialog open."""
        tab = window.section_tabs["sources"]
        tab.add()
        dialog = _dialog(tab)
        assert dialog.name_edit is not None

        dialog.try_accept()
        empty = dialog.error.text()
        dialog.name_edit.setText("library")
        dialog.try_accept()

        assert empty == "Enter a name."
        assert "already exists" in dialog.error.text()
        assert dialog.result() == 0


class TestProblems:
    """Unreadable files and concurrent edits are reported in the window."""

    def test_broken_settings_show_defaults(self, tray: Tray) -> None:
        """An invalid settings.json opens with defaults and says so."""
        store = SettingsStore.default(tray.sandbox.environment)
        store.path.write_text('{"history": {"keep": 1}}', encoding="utf-8")

        tray.controller.show_settings()

        window = tray.controller.settings_window
        assert window is not None
        assert "Showing defaults" in window.error.text()
        assert window.schedule_enabled.isChecked()
        window.close()

    def test_broken_configuration_disables_the_tabs(
        self, tray: Tray, tmp_path: Path
    ) -> None:
        """An unreadable config.json leaves only General, until a good one is named."""
        good = tray.sandbox.config_path.read_text(encoding="utf-8")
        tray.sandbox.config_path.write_text("{", encoding="utf-8")
        other = tmp_path / "other.json"
        other.write_text(good, encoding="utf-8")

        tray.controller.show_settings()

        window = tray.controller.settings_window
        assert window is not None
        assert window.tabs.count() == 1
        assert "unavailable" in window.error.text()
        assert not window.save()
        assert "cannot be read" in window.error.text()
        window.config_path.setText(str(other))
        assert window.save(), window.error.text()
        assert tray.controller.settings.config == other

    @pytest.mark.parametrize("edited", [True, False])
    def test_settings_edited_meanwhile_are_not_overwritten(
        self, tray: Tray, window: SettingsWindow, *, edited: bool
    ) -> None:
        """A valid settings.json edit made meanwhile stops Save; neither file changes.

        A pending configuration edit is not written either.
        """
        store = SettingsStore.default(tray.sandbox.environment)
        config_before = tray.sandbox.config_path.read_bytes()
        _disable_target(window)
        window.keep.setValue(60)
        if edited:
            store.save(UserSettings(schedule=ScheduleOptions(enabled=False)))
        else:
            store.path.unlink()
        settings_before = store.path.read_bytes() if store.path.exists() else None

        assert not window.save()

        assert "changed since it was read" in window.error.text()
        assert tray.sandbox.config_path.read_bytes() == config_before
        after = store.path.read_bytes() if store.path.exists() else None
        assert after == settings_before

    def test_reopening_reloads(self, tray: Tray, window: SettingsWindow) -> None:
        """Opening the window again shows the files as they are now."""
        window.close()
        SettingsStore.default(tray.sandbox.environment).save(
            UserSettings(schedule=ScheduleOptions(interval=timedelta(hours=3)))
        )

        tray.controller.show_settings()

        assert window.interval.text() == "3h"

    def test_showing_an_open_window_keeps_its_edits(
        self, tray: Tray, window: SettingsWindow
    ) -> None:
        """Settings again, while the window is open, only raises it."""
        window.interval.setText("2h")
        _disable_target(window)

        tray.controller.handle(["show", "settings"])

        assert window.interval.text() == "2h"
        assert window.document is not None
        assert window.document.changed

    def test_unparsable_settings_can_be_opened_and_are_left_alone(
        self, tray: Tray
    ) -> None:
        """settings.json that is not JSON: General is disabled, Save skips it."""
        store = SettingsStore.default(tray.sandbox.environment)
        store.path.write_text("not json", encoding="utf-8")
        tray.controller.show_settings()
        window = tray.controller.settings_window
        assert window is not None

        assert not window.general_page.isEnabled()
        assert "text editor" in window.error.text()
        window.open_settings_button.click()
        assert tray.opened == [store.path]
        _disable_target(window)
        assert window.save(), window.error.text()

        assert store.path.read_text(encoding="utf-8") == "not json"
        assert _config(tray)["destinations"]["target"]["enabled"] is False

    def test_configuration_edited_meanwhile_stops_a_settings_save(
        self, tray: Tray, window: SettingsWindow
    ) -> None:
        """A settings-only save still checks the configuration on disk."""
        store = SettingsStore.default(tray.sandbox.environment)
        before = store.path.read_bytes()
        window.keep.setValue(70)
        path = tray.sandbox.config_path
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

        assert not window.save()

        assert "changed since it was read" in window.error.text()
        assert store.path.read_bytes() == before

    def test_settings_write_failure_after_the_configuration(
        self, tray: Tray, window: SettingsWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If settings.json fails after config.json was written, both are named."""

        def fail(_write: object) -> bool:
            raise OSError("disk full")

        monkeypatch.setattr(module.SettingsWrite, "commit", fail)
        _disable_target(window)

        assert not window.save()

        assert "Saved" in window.error.text()
        assert "but not settings.json: disk full" in window.error.text()
        assert _config(tray)["destinations"]["target"]["enabled"] is False

    def test_configuration_write_failure_saves_nothing(
        self, tray: Tray, window: SettingsWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed configuration write leaves settings.json alone too."""

        def fail(_write: object) -> bool:
            raise OSError("read-only")

        monkeypatch.setattr(module.PendingWrite, "commit", fail)
        store = SettingsStore.default(tray.sandbox.environment)
        before = store.path.read_bytes()
        window.keep.setValue(80)

        assert not window.save()

        assert window.error.text() == "Nothing saved: read-only"
        assert store.path.read_bytes() == before

    def test_rebuilding_releases_the_old_tabs(
        self, qtbot: QtBot, window: SettingsWindow
    ) -> None:
        """Reloading deletes the previous configuration tabs."""
        old = window.section_tabs["sources"]

        with qtbot.waitSignal(old.destroyed, timeout=5000):
            window.load()

        assert window.tabs.count() == 5


class TestSelections:
    """Scheduled profiles, route filters, harnesses, and the configuration file."""

    def test_empty_profile_selection_is_refused(self, tray: Tray) -> None:
        """Ticking "only these profiles" with none ticked would sync more: refused."""
        add_profiles(tray.sandbox.config_path, {"work": {}})
        tray.controller.show_settings()
        window = tray.controller.settings_window
        assert window is not None
        window.only_profiles.setChecked(True)
        _check(window.profiles)

        assert not window.save()

        assert "tick at least one profile" in window.error.text()

    @pytest.mark.parametrize(("new", "expected"), [("office", ["office"]), ("", [])])
    def test_profile_rename_and_removal_follow_into_the_schedule(
        self, tray: Tray, new: str, expected: list[str]
    ) -> None:
        """A renamed scheduled profile stays ticked; a removed one is dropped."""
        add_profiles(tray.sandbox.config_path, {"work": {}, "home": {}})
        tray.controller.show_settings()
        window = tray.controller.settings_window
        assert window is not None
        window.only_profiles.setChecked(True)
        _check(window.profiles, "work")
        profiles = window.section_tabs["profiles"]
        profiles.table.selectRow(0)
        if new:
            profiles.edit()
            dialog = _dialog(profiles)
            assert dialog.name_edit is not None
            dialog.name_edit.setText(new)
            dialog.try_accept()
        else:
            profiles.remove()

        assert sorted(window._checked_profiles()) == expected
        listed = [
            window.profiles.item(i).text() for i in range(window.profiles.count())
        ]
        assert "work" not in listed

    def test_untouched_route_filters_are_kept_exactly(self, tray: Tray) -> None:
        """An empty include list and a glob with a comma survive an unrelated edit."""
        path = tray.sandbox.config_path
        data = _config(tray)
        data["routes"][0]["include"] = []
        data["routes"][0]["exclude"] = ["skills/[a,b]*"]
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tray.controller.show_settings()
        window = tray.controller.settings_window
        assert window is not None
        routes = window.section_tabs["routes"]
        routes.table.selectRow(0)
        routes.edit()
        route = _dialog(routes)
        lossy = route.widgets["allow_lossy"]
        assert isinstance(lossy, QCheckBox)
        lossy.setChecked(True)
        route.try_accept()

        assert window.save(), window.error.text()

        saved = _config(tray)["routes"][0]
        assert saved["include"] == []
        assert saved["exclude"] == ["skills/[a,b]*"]
        assert saved["allow_lossy"] is True

    @pytest.mark.parametrize(("new", "harness"), [("office", "codex"), ("codex", None)])
    def test_renaming_keeps_an_inferred_harness(
        self, tray: Tray, new: str, harness: str | None
    ) -> None:
        """A destination named for its harness keeps that harness when renamed."""
        path = tray.sandbox.config_path
        data = _config(tray)
        data["destinations"] = {"codex": {}}
        data["routes"][0]["destinations"] = ["codex"]
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tray.controller.show_settings()
        window = tray.controller.settings_window
        assert window is not None
        destinations = window.section_tabs["destinations"]
        destinations.table.selectRow(0)
        destinations.edit()
        dialog = _dialog(destinations)
        combo = dialog.widgets["profile"]
        assert isinstance(combo, QComboBox)
        assert combo.currentText() == "codex"
        assert dialog.name_edit is not None
        dialog.name_edit.setText(new)
        dialog.try_accept()

        assert window.document is not None
        assert window.document.section("destinations")[new].get("profile") == harness

    def test_switching_configuration_with_pending_edits_is_refused(
        self, tray: Tray, window: SettingsWindow, tmp_path: Path
    ) -> None:
        """Edits stay with their file: switching first needs Save or Cancel."""
        other = tmp_path / "other.json"
        other.write_bytes(tray.sandbox.config_path.read_bytes())
        _disable_target(window)

        window.config_path.setText(str(other))
        assert not window.save()

        assert "Save or cancel" in window.error.text()
        assert window.config_path.text() == ""
        assert window.document is not None
        assert window.document.path == tray.sandbox.config_path

    def test_switching_configuration_edits_the_new_file(
        self, tray: Tray, window: SettingsWindow, tmp_path: Path
    ) -> None:
        """With no pending edits, the tabs follow the chosen file and save there."""
        other = tmp_path / "other.json"
        before = tray.sandbox.config_path.read_bytes()
        other.write_bytes(before)
        window.config_path.setText(str(other))
        assert window.follow_config()
        _disable_target(window)

        assert window.save(), window.error.text()

        assert tray.sandbox.config_path.read_bytes() == before
        assert "false" in other.read_text(encoding="utf-8")
        assert tray.controller.settings.config == other

    @pytest.mark.skipif(sys.platform == "win32", reason="links need privileges")
    def test_linked_configuration_is_not_replaced(
        self, tray: Tray, tmp_path: Path
    ) -> None:
        """A config.json that is a link is refused, not replaced by a file."""
        path = tray.sandbox.config_path
        shared = tmp_path / "shared.json"
        shared.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(shared)
        tray.controller.show_settings()
        window = tray.controller.settings_window
        assert window is not None
        _disable_target(window)

        assert not window.save()

        assert "is a link" in window.error.text()
        assert path.is_symlink()


class TestPieces:
    """Cell text and the element dialog's value rules."""

    @pytest.mark.parametrize(
        ("value", "text"),
        [
            (None, ""),
            (True, "yes"),
            (False, "no"),
            (["a", "b"], "a, b"),
            ({"platform": "linux"}, "conditions"),
            ({}, ""),
            (3, "3"),
        ],
    )
    def test_cells(self, value: JsonValue | None, text: str) -> None:
        """Values read naturally in a table."""
        assert cell(value) == text

    def test_unknown_keys_and_defaults_are_kept(self, qtbot: object) -> None:
        """Keys the form does not show survive; untouched defaults stay absent."""
        del qtbot
        fields = (
            Field("description", "Description"),
            Field("default", "Default", "bool", default=True),
        )
        dialog = ElementDialog(
            "Profile", fields, {"when": {"platform": "linux"}}, {}, name="p"
        )

        assert dialog.table() == {"when": {"platform": "linux"}}
        assert dialog.name() == "p"

    def test_a_cleared_field_is_removed(self, qtbot: object) -> None:
        """Emptying a field that had a value drops the key."""
        del qtbot
        fields = (Field("description", "Description"),)
        dialog = ElementDialog("Profile", fields, {"description": "x"}, {}, name="p")
        _text(dialog, "description", "")

        assert dialog.table() == {}

    def test_open_settings_without_an_opener(self, tray: Tray) -> None:
        """A window built without a file opener ignores the request."""
        window = SettingsWindow(
            SettingsStore.default(tray.sandbox.environment),
            default_config=lambda _settings: tray.sandbox.config_path,
            validate=lambda _path, _text: pytest.fail("not validated"),
            on_saved=lambda: None,
        )

        window.open_settings_file()

        assert tray.opened == []
