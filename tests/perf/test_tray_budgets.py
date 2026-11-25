"""Time budgets for the tray: menus and windows must open without a pause.

A tray rebuilds its menu after every sync and each minute, and opens the
history window on demand, so each must stay well under what a user notices
even with a large catalog and a full history.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

# Before Qt loads: no display is needed
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6", reason="the tray extra is not installed")

from pytestqt.qtbot import QtBot

from skillsinc.history import (
    MAXIMUM_CHANGES,
    ChangeRecord,
    SyncHistory,
    SyncRecord,
    Trigger,
)
from skillsinc.tray.catalog import Catalog, ProfileEntry
from skillsinc.tray.history_view import HistoryPopup, HistoryWindow
from skillsinc.tray.menu import MenuState, TrayCommands, TrayIcon

START: Final = datetime(2026, 11, 25, tzinfo=UTC)
PROFILES: Final = 200
RECORDS: Final = 1000
# Seconds
MENU_BUDGET: Final = 0.1
WINDOW_BUDGET: Final = 1.0
POPUP_BUDGET: Final = 0.2


def _nothing(*_args: object) -> None:
    """Ignore a menu command."""


def _commands() -> TrayCommands:
    return TrayCommands(
        sync=_nothing,
        cancel=_nothing,
        pause=_nothing,
        resume=_nothing,
        history=_nothing,
        popup=_nothing,
        settings=_nothing,
        open_config=_nothing,
        open_state=_nothing,
        about=_nothing,
        exit=_nothing,
        update=_nothing,
    )


def _records(tmp_path: Path) -> list[SyncRecord]:
    """Write and read back a full history of detailed records."""
    history = SyncHistory(tmp_path, keep=RECORDS)
    for index in range(RECORDS):
        started = START + timedelta(minutes=index)
        history.append(
            SyncRecord(
                id=f"r{index}",
                started=started,
                finished=started + timedelta(seconds=2),
                trigger=Trigger.TRAY_SCHEDULED,
                ok=index % 7 != 0,
                config="/home/me/.config/skillsinc/config.json",
                profiles=("work",),
                counts={"create": MAXIMUM_CHANGES},
                changes=tuple(
                    ChangeRecord("claude", "create", f"skills/skill-{n}/SKILL.md")
                    for n in range(MAXIMUM_CHANGES)
                ),
            )
        )
    return history.records()


def _timed(action: object) -> float:
    assert callable(action)
    began = time.perf_counter()
    action()
    return time.perf_counter() - began


def test_menu_rebuild_with_many_profiles(qtbot: QtBot) -> None:
    """Rebuilding the menu for a large catalog stays instant."""
    del qtbot
    icon = TrayIcon(_commands())
    catalog = Catalog(
        profiles=tuple(
            ProfileEntry(f"profile-{n}", f"Profile {n}", active=n % 2 == 0, reason="x")
            for n in range(PROFILES)
        ),
        defined=True,
    )
    state = MenuState(
        status="Last sync 1 min ago", next_sync="Next in 4 min", catalog=catalog
    )

    elapsed = min(_timed(lambda: icon.update_menu(state)) for _ in range(5))

    assert len(icon.names()) > PROFILES
    assert elapsed < MENU_BUDGET, f"Menu rebuild took {elapsed:.3f}s"


def test_history_window_with_a_full_history(qtbot: QtBot, tmp_path: Path) -> None:
    """Opening the history window over a full history stays under a second."""
    records = _records(tmp_path)
    window = HistoryWindow(lambda limit: records[:limit], _nothing, _nothing)
    qtbot.addWidget(window)

    elapsed = _timed(lambda: (window.refresh(), window.show()))

    assert window.model.rowCount() == RECORDS
    assert elapsed < WINDOW_BUDGET, f"History window took {elapsed:.3f}s"


def test_popup_with_recent_syncs(qtbot: QtBot, tmp_path: Path) -> None:
    """The click popup shows recent syncs at once."""
    records = _records(tmp_path)
    popup = HistoryPopup(_nothing, _nothing, _nothing)
    qtbot.addWidget(popup)

    elapsed = _timed(
        lambda: (popup.populate("Last sync 1 min ago", records[:10]), popup.show())
    )

    assert elapsed < POPUP_BUDGET, f"Popup took {elapsed:.3f}s"
