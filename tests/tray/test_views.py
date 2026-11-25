"""The history views: run details, table cells, and keyboard handling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from skillsinc.history import ChangeRecord, SyncRecord, Trigger
from skillsinc.tray.history_view import (
    HistoryModel,
    HistoryPopup,
    HistoryWindow,
    details,
)

START = datetime(2026, 11, 25, 9, 30, tzinfo=UTC)


def _record(**changes: object) -> SyncRecord:
    values: dict[str, object] = {
        "id": "r1",
        "started": START,
        "finished": START + timedelta(seconds=3),
        "trigger": Trigger.TRAY,
        "ok": False,
        "config": "/home/me/.config/skillsinc/config.json",
        "warnings": 2,
        "errors": ("source library is missing",),
        "changes": (ChangeRecord("claude", "create", "skills/a/SKILL.md"),),
        "counts": {"create": 3},
        "truncated": True,
    }
    values.update(changes)
    return SyncRecord(**values)  # ty: ignore[invalid-argument-type]


def _nothing() -> None:
    """Ignore a button."""


class TestDetails:
    """The selected run's details name everything it recorded."""

    def test_warnings_errors_and_truncated_changes(self) -> None:
        """Every section appears, with how many changes were left out."""
        text = details(_record())

        assert "Warnings: 2" in text
        assert "  source library is missing" in text
        assert "create  claude: skills/a/SKILL.md" in text
        assert "… and 2 more" in text


class TestModel:
    """Cells, colors, and tooltips."""

    def test_invalid_index_and_unknown_role_have_no_data(self, qtbot: QtBot) -> None:
        """Qt asks for roles the table does not use; they are empty."""
        del qtbot
        model = HistoryModel()
        model.set_records([_record()])
        index = model.index(0, 1)

        assert model.data(QModelIndex()) is None
        assert model.data(index, Qt.ItemDataRole.DecorationRole) is None
        assert model.data(index, Qt.ItemDataRole.ToolTipRole) == _record().summary()
        assert model.headerData(0, Qt.Orientation.Vertical) is None


class TestWindows:
    """Keyboard and selection handling."""

    def test_clearing_the_selection_keeps_the_details(self, qtbot: QtBot) -> None:
        """Moving to no row leaves the last details in place."""
        window = HistoryWindow(lambda: [_record()], _nothing, _nothing)
        qtbot.addWidget(window)
        window.refresh()
        shown = window.details.toPlainText()

        window.table.setCurrentIndex(QModelIndex())

        assert window.details.toPlainText() == shown

    def test_popup_ignores_keys_other_than_escape(self, qtbot: QtBot) -> None:
        """Only Escape closes the popup."""
        popup = HistoryPopup(_nothing, _nothing, _nothing)
        qtbot.addWidget(popup)
        popup.populate("Last sync failed", [_record()])
        popup.show()

        key = QKeyEvent(
            QKeyEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier
        )
        QApplication.sendEvent(popup, key)

        assert popup.isVisible()

    def test_escape_hides_the_popup(self, qtbot: QtBot) -> None:
        """Escape dismisses the popup."""
        popup = HistoryPopup(_nothing, _nothing, _nothing)
        qtbot.addWidget(popup)
        popup.show()

        key = QKeyEvent(
            QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier
        )
        QApplication.sendEvent(popup, key)

        assert not popup.isVisible()
