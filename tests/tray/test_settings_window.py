"""The settings window: edit settings.json and config.json, then save both."""

from __future__ import annotations

import json
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
    QTableWidget,
    QWidget,
)
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
    assert isinstance(widget, QLineEdit)
    widget.setText(value)


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
        _text(route, "include", "skills/*, agents/*")
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
            "include": ["skills/*", "agents/*"],
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
        assert data["destinations"]["target"]["enabled"] is False

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

    def test_broken_configuration_disables_the_tabs(self, tray: Tray) -> None:
        """An unreadable config.json leaves only the General tab."""
        tray.sandbox.config_path.write_text("{", encoding="utf-8")

        tray.controller.show_settings()

        window = tray.controller.settings_window
        assert window is not None
        assert window.tabs.count() == 1
        assert "unavailable" in window.error.text()
        assert window.save(), window.error.text()

    def test_settings_edited_meanwhile_are_not_overwritten(
        self, tray: Tray, window: SettingsWindow
    ) -> None:
        """A settings.json changed since opening is reported, not replaced."""
        store = SettingsStore.default(tray.sandbox.environment)
        window.keep.setValue(60)
        window.schedule_enabled.setChecked(True)
        store.path.write_text("not json", encoding="utf-8")

        assert not window.save()
        assert window.error.text()

    def test_reopening_reloads(self, tray: Tray, window: SettingsWindow) -> None:
        """Opening the window again shows the files as they are now."""
        SettingsStore.default(tray.sandbox.environment).save(
            UserSettings(schedule=ScheduleOptions(interval=timedelta(hours=3)))
        )

        tray.controller.show_settings()

        assert window.interval.text() == "3h"


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
