"""When the next scheduled sync is due: pure arithmetic, no clocks or Qt."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

# Never wait longer than this between checks, so sleep and clock changes are
# noticed within a minute
HEARTBEAT: Final = timedelta(seconds=60)


@dataclass(frozen=True, slots=True)
class ScheduleState:
    """Everything that decides the next scheduled sync."""

    enabled: bool
    interval: timedelta
    last_started: datetime | None
    """When the newest recorded sync of this configuration started."""
    started_at: datetime
    """When the tray started."""
    run_at_start: bool
    paused_until: datetime | None = None
    paused: bool = False
    """Paused until resumed, whatever ``paused_until`` says."""

    def next_due(self) -> datetime | None:
        """Return when the next scheduled sync is due, or None when none is.

        A run at start is due at once unless a sync started since the tray
        did; otherwise a sync is due one interval after the last one started,
        whatever started it. A pause postpones it to the pause's end.
        """
        if not self.enabled or self.paused:
            return None
        last = self.last_started
        if last is None:
            due = (
                self.started_at
                if self.run_at_start
                else self.started_at + self.interval
            )
        elif self.run_at_start and last < self.started_at:
            due = self.started_at
        else:
            due = last + self.interval
        if self.paused_until is not None and due < self.paused_until:
            due = self.paused_until
        return due

    def wait(self, now: datetime) -> timedelta | None:
        """Return how long to sleep before checking again, or None to stop.

        Never longer than ``HEARTBEAT``, never negative.
        """
        due = self.next_due()
        if due is None:
            return None
        return min(max(due - now, timedelta(0)), HEARTBEAT)

    def is_due(self, now: datetime) -> bool:
        """Return True if a scheduled sync should start at ``now``."""
        due = self.next_due()
        return due is not None and now >= due


def describe_delta(delta: timedelta) -> str:
    """Return a short, rounded description such as ``3 min`` or ``2 h 5 min``."""
    seconds = max(0, round(delta.total_seconds()))
    if seconds < 60:  # noqa: PLR2004 - one minute
        return "less than a minute"
    minutes = round(seconds / 60)
    if minutes < 60:  # noqa: PLR2004 - one hour
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:  # noqa: PLR2004 - two days
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    days = round(hours / 24)
    return f"{days} days"
