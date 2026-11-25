"""When the next scheduled sync is due."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from skillsinc.tray.timing import HEARTBEAT, ScheduleState, describe_delta

START = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
FIVE = timedelta(minutes=5)


def _state(**changes: object) -> ScheduleState:
    values: dict[str, object] = {
        "enabled": True,
        "interval": FIVE,
        "last_started": None,
        "started_at": START,
        "run_at_start": True,
    }
    values.update(changes)
    return ScheduleState(**values)  # ty: ignore[invalid-argument-type]


class TestNextDue:
    """The next sync follows the last run, the start, and any pause."""

    def test_first_run_is_at_start(self) -> None:
        """With run_at_start and no history, a sync is due at once."""
        assert _state().next_due() == START

    def test_without_run_at_start_waits_an_interval(self) -> None:
        """Without run_at_start, the first sync waits one interval."""
        assert _state(run_at_start=False).next_due() == START + FIVE

    def test_run_before_start_still_runs_at_start(self) -> None:
        """A sync from before the tray started does not cancel the start run."""
        last = START - timedelta(minutes=1)

        assert _state(last_started=last).next_due() == START
        assert _state(last_started=last, run_at_start=False).next_due() == last + FIVE

    def test_later_runs_follow_the_last_sync(self) -> None:
        """Any sync since start, manual or scheduled, resets the interval."""
        last = START + timedelta(minutes=2)

        assert _state(last_started=last).next_due() == last + FIVE

    def test_disabled_or_paused_has_no_due_time(self) -> None:
        """Scheduling off, or paused until resumed, means nothing is due."""
        assert _state(enabled=False).next_due() is None
        assert _state(paused=True).next_due() is None
        assert _state(enabled=False).wait(START) is None
        assert not _state(paused=True).is_due(START + timedelta(days=1))

    def test_pause_postpones_to_its_end(self) -> None:
        """A timed pause moves an earlier due time to the pause's end."""
        until = START + timedelta(hours=1)

        assert _state(paused_until=until).next_due() == until
        late = START + timedelta(hours=2)
        assert _state(last_started=late, paused_until=until).next_due() == late + FIVE

    def test_due_and_wait(self) -> None:
        """Due at or after the time; the wait is capped by the heartbeat."""
        state = _state(run_at_start=False)

        assert not state.is_due(START)
        assert state.is_due(START + FIVE)
        assert state.wait(START) == HEARTBEAT
        assert state.wait(START + FIVE - timedelta(seconds=10)) == timedelta(seconds=10)
        assert state.wait(START + timedelta(hours=1)) == timedelta(0)

    @given(
        offset=st.integers(min_value=-(10**6), max_value=10**6),
        minutes=st.integers(min_value=1, max_value=60 * 24 * 31),
        last=st.none() | st.integers(min_value=-(10**6), max_value=10**6),
    )
    def test_wait_is_never_negative_or_beyond_the_heartbeat(
        self, offset: int, minutes: int, last: int | None
    ) -> None:
        """However the clock moves, the tray checks again within a minute."""
        state = _state(
            interval=timedelta(minutes=minutes),
            last_started=START + timedelta(seconds=last) if last is not None else None,
        )

        wait = state.wait(START + timedelta(seconds=offset))

        assert wait is not None
        assert timedelta(0) <= wait <= HEARTBEAT


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (-5, "less than a minute"),
        (30, "less than a minute"),
        (60, "1 min"),
        (59 * 60, "59 min"),
        (60 * 60, "1 h"),
        (125 * 60, "2 h 5 min"),
        (3 * 86400, "3 days"),
    ],
)
def test_describe_delta(seconds: int, text: str) -> None:
    """Durations read naturally in menus."""
    assert describe_delta(timedelta(seconds=seconds)) == text
