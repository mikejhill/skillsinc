"""Test plan status, action counts, and write classification."""

from __future__ import annotations

from pathlib import Path

import pytest

from skillsinc.config import DestinationConfig
from skillsinc.plan import Action, Change, DestinationPlan, Plan


class TestChange:
    """Actions distinguish filesystem mutations from ownership-only updates."""

    @pytest.mark.parametrize("action", list(Action))
    def test_writes_classifies_filesystem_changes(self, action: Action) -> None:
        """Only create, update, adopt, and delete mutate destination files."""
        change = Change(action, "file", None)
        expected = action not in {Action.TRACK, Action.FORGET}

        result = change.writes

        assert result is expected, f"Expected {action} writes={expected}, got {result}"


class TestDestinationPlan:
    """Destination summaries count each planned action independently."""

    def test_counts_changes_by_action(self, tmp_path: Path) -> None:
        """Aggregate repeated actions and omit zero-count actions."""
        changes = (
            Change(Action.CREATE, "one", None),
            Change(Action.CREATE, "two", None),
            Change(Action.FORGET, "old", None),
        )
        plan = DestinationPlan(
            DestinationConfig("target", "generic", tmp_path), changes
        )

        result = plan.counts()

        assert result == {"create": 2, "forget": 1}, (
            f"Expected two creates and one forget, got {result!r}"
        )


class TestPlan:
    """Plan status excludes blocked changes and accounts for failed sources."""

    @pytest.mark.parametrize(
        ("errors", "source_errors", "expected"),
        [
            ((), {}, True),
            (("blocked",), {}, False),
            ((), {"library": "missing"}, False),
        ],
    )
    def test_ok_requires_sources_and_destinations_to_succeed(
        self,
        tmp_path: Path,
        errors: tuple[str, ...],
        source_errors: dict[str, str],
        *,
        expected: bool,
    ) -> None:
        """Either a blocked destination or failed source makes the plan unsuccessful."""
        destination = DestinationPlan(
            DestinationConfig("target", "generic", tmp_path), errors=errors
        )

        result = Plan((destination,), source_errors).ok

        assert result is expected, f"Expected plan success {expected}, got {result}"
