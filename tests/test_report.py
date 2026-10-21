"""Test readable summaries, verbose details, and versioned JSON results."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skillsinc.applier import DestinationResult
from skillsinc.config import DestinationConfig
from skillsinc.plan import Action, Change, DestinationPlan, Plan, Skip
from skillsinc.report import Reporter


class TestReporter:
    """Reports preserve actionable detail while collapsing repetitive skill entries."""

    @staticmethod
    def plan(tmp_path: Path) -> Plan:
        """Build a mixed report with changes, warnings, and conversion losses."""
        destination = DestinationConfig("target", "generic", tmp_path)
        changes = (
            Change(Action.CREATE, "skills/sample/SKILL.md", None),
            Change(Action.CREATE, "skills/sample/ref.md", None),
            Change(
                Action.UPDATE,
                "AGENTS.md",
                "old",
                block=True,
                note="overwrites local edits",
            ),
        )
        skipped = (
            Skip("unsupported", "no location"),
            Skip("lossy", "drops tools", lossy=True),
        )
        return Plan(
            (
                DestinationPlan(
                    destination,
                    changes=changes,
                    skipped=skipped,
                    notes=("accepted loss",),
                ),
            ),
            {},
        )

    def test_plan_warnings_appear_in_text_and_json(self, tmp_path: Path) -> None:
        """Duplicate-load warnings reach both people and programs."""
        plan = Plan(self.plan(tmp_path).destinations, {}, warnings=("twice",))

        text = Reporter().plan_text(plan, applied=False)
        data = json.loads(Reporter().plan_json(plan))

        assert "warning: twice" in text, f"Expected warning line, got {text!r}"
        assert data["warnings"] == ["twice"], f"Expected warnings key, got {data!r}"

    @pytest.mark.parametrize("verbose", [False, True])
    def test_verbose_controls_collapsing_and_unsupported_details(
        self, tmp_path: Path, *, verbose: bool
    ) -> None:
        """Expand files and show conversion details only in verbose output."""
        plan = self.plan(tmp_path)

        result = Reporter(verbose=verbose).plan_text(plan, applied=False)

        expected_path = (
            "skills/sample/SKILL.md" if verbose else "skills/sample/ (2 files)"
        )
        assert expected_path in result, (
            f"Expected skill detail and warnings, got {result!r}"
        )
        assert "skipped (lossy) lossy" in result, (
            f"Expected skill detail and warnings, got {result!r}"
        )
        assert "[managed block]" in result, (
            f"Expected skill detail and warnings, got {result!r}"
        )
        assert ("skipped unsupported" in result) is verbose, (
            f"Expected verbose details {verbose}, got {result!r}"
        )
        assert ("note: accepted loss" in result) is verbose, (
            f"Expected verbose details {verbose}, got {result!r}"
        )
        assert "Run `skillsinc sync` to apply" in result, (
            f"Expected preview apply guidance, got {result!r}"
        )

    def test_applied_summary_omits_preview_guidance(self, tmp_path: Path) -> None:
        """Applied reports describe actions without asking the user to apply again."""
        result = Reporter().plan_text(self.plan(tmp_path), applied=True)

        assert "Run `skillsinc sync`" not in result, (
            f"Expected applied counts only, got {result!r}"
        )
        assert "2 create, 1 update" in result, (
            f"Expected applied counts only, got {result!r}"
        )

    def test_empty_plan_explains_configuration(self) -> None:
        """An empty destination list tells the user to enable destinations."""
        result = Reporter().plan_text(Plan((), {}), applied=False)

        assert "No enabled destinations" in result, (
            f"Expected empty configuration guidance, got {result!r}"
        )
        assert "Summary: no changes." in result, (
            f"Expected empty configuration guidance, got {result!r}"
        )

    def test_blocked_errors_are_collapsed_and_counted(self, tmp_path: Path) -> None:
        """Collapse repeated skill conflicts while retaining source failure detail."""
        destination = DestinationPlan(
            DestinationConfig("target", "generic", tmp_path),
            errors=(
                "skills/sample/SKILL.md: conflict",
                "skills/sample/ref.md: conflict",
                "root failure",
            ),
        )

        result = Reporter().plan_text(
            Plan((destination,), {"library": "missing source"}), applied=False
        )

        assert "skills/sample/ (2 files): conflict" in result, (
            f"Expected collapsed destination and source errors, got {result!r}"
        )
        assert "root failure" in result, (
            f"Expected collapsed destination and source errors, got {result!r}"
        )
        assert "error: missing source" in result, (
            f"Expected collapsed destination and source errors, got {result!r}"
        )
        assert "1 destination(s) blocked" in result, (
            f"Expected collapsed destination and source errors, got {result!r}"
        )

    def test_json_includes_apply_failure_and_actual_count(self, tmp_path: Path) -> None:
        """Report application failures and successful prefixes in JSON."""
        plan = self.plan(tmp_path)
        results = (DestinationResult("target", 1, "changed since planning"),)

        output = Reporter().plan_json(plan, results)

        result = json.loads(output)
        assert (
            result["version"],
            result["ok"],
            result["applied"],
            result["destinations"][0]["applied"],
            result["destinations"][0]["errors"],
        ) == (1, False, True, 1, ["changed since planning"]), (
            f"Expected versioned apply failure and count, got {output}"
        )

    def test_json_preview_has_no_applied_results(self, tmp_path: Path) -> None:
        """Preview JSON distinguishes planning from application."""
        output = Reporter().plan_json(self.plan(tmp_path))

        result = json.loads(output)
        assert result["applied"] is False, f"Expected unapplied preview, got {output}"
        assert result["destinations"][0]["applied"] == 0, (
            f"Expected unapplied preview, got {output}"
        )

    def test_results_text_reports_only_failures(self) -> None:
        """Text results omit successful destinations and explain stopped failures."""
        result = Reporter().results_text(
            (DestinationResult("okay", 2), DestinationResult("failed", 1, "disk error"))
        )

        assert result == "error: failed: stopped after 1 change(s): disk error\n", (
            f"Expected one failure line, got {result!r}"
        )
