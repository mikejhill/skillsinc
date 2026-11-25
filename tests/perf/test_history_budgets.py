"""Time budgets for the sync history: recording must never slow a sync.

A full history (the largest ``history.keep``, with the most detailed records)
must still take an append and a newest-first read well within a second.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from skillsinc.history import (
    MAXIMUM_CHANGES,
    ChangeRecord,
    SyncHistory,
    SyncRecord,
    Trigger,
)

START: Final = datetime(2026, 11, 25, tzinfo=UTC)
# Records in a history at its default size, each as detailed as a record gets
DEFAULT_KEEP: Final = 500
# Seconds
APPEND_BUDGET: Final = 0.25
READ_NEWEST_BUDGET: Final = 1.0


def _record(index: int) -> SyncRecord:
    """Return a record listing the most changes a record holds."""
    started = START + timedelta(minutes=index)
    return SyncRecord(
        id=f"r{index}",
        started=started,
        finished=started + timedelta(seconds=1),
        trigger=Trigger.TRAY_SCHEDULED,
        ok=True,
        config="/home/me/.config/skillsinc/config.json",
        counts={"create": MAXIMUM_CHANGES},
        changes=tuple(
            ChangeRecord("claude", "create", f"skills/skill-{n}/SKILL.md")
            for n in range(MAXIMUM_CHANGES)
        ),
    )


def _full(tmp_path: Path) -> SyncHistory:
    """Return a history holding twice ``DEFAULT_KEEP`` detailed records, untrimmed."""
    history = SyncHistory(tmp_path, keep=DEFAULT_KEEP)
    line = _record(0).to_json()
    text = json.dumps(line, separators=(",", ":")) + "\n"
    history.path.write_text(text * (DEFAULT_KEEP * 2), encoding="utf-8")
    return history


def test_append_to_a_full_history_fits_the_budget(tmp_path: Path) -> None:
    """The append that triggers a trim still takes a fraction of a second."""
    history = _full(tmp_path)

    start = time.perf_counter()
    history.append(_record(1))
    elapsed = time.perf_counter() - start

    assert elapsed < APPEND_BUDGET, f"Append took {elapsed:.3f}s"
    assert len(history.path.read_text(encoding="utf-8").splitlines()) == DEFAULT_KEEP


def test_reading_the_newest_runs_fits_the_budget(tmp_path: Path) -> None:
    """The tray's history window reads the newest runs of a full file quickly."""
    history = _full(tmp_path)

    start = time.perf_counter()
    records = history.records(limit=50)
    elapsed = time.perf_counter() - start

    assert len(records) == 50
    assert elapsed < READ_NEWEST_BUDGET, f"Read took {elapsed:.3f}s"
