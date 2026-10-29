"""Mutation gates require actual, complete verdicts for the selected scope."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "mutation_report.py"
COMMIT = "ab" * 20
RUN_ID = "40799850706"
RUN_ATTEMPT = "2"


def _report(tmp_path: Path, text: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the trusted reporter on a synthetic results file without mutmut."""
    results = tmp_path / "results.txt"
    results.write_text(text, encoding="utf-8")
    return subprocess.run(  # noqa: S603 - fixed trusted script and synthetic arguments
        [sys.executable, str(SCRIPT), str(results), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _outcomes(*statuses: str, module: str = "sample") -> str:
    """Build one uniquely named mutant per outcome."""
    return "\n".join(
        f"skillsinc.{module}.xcheck__mutmut_{index}: {status}"
        for index, status in enumerate(statuses)
    )


def test_known_status_policy_counts_every_classified_outcome(tmp_path: Path) -> None:
    """Timeouts count as caught; suspicious and no-tests outcomes stay missed."""
    result = _report(
        tmp_path,
        _outcomes(
            "killed",
            "timeout",
            "caught by type check",
            "segfault",
            "survived",
            "no tests",
            "suspicious",
        ),
        "--fail-under",
        "57",
    )

    assert result.returncode == 0, f"Expected a complete passing gate: {result}"
    assert "Classified score: 57.1%" in result.stdout, result.stdout
    assert "4 of 7 classified mutant outcomes caught" in result.stdout, result.stdout
    assert "Completeness: complete" in result.stdout, result.stdout
    assert "No tests and suspicious outcomes count as missed" in result.stdout, (
        result.stdout
    )


@pytest.mark.parametrize("status", ["not checked", "new upstream status", "", "None"])
def test_unclassified_outcome_cannot_pass_a_gate(tmp_path: Path, status: str) -> None:
    """A 100% score over known outcomes cannot conceal an unclassified mutant."""
    result = _report(tmp_path, _outcomes("killed", status), "--fail-under", "85")

    assert result.returncode == 1, f"Expected incomplete results to fail: {result}"
    assert "Classified score: 100.0%" in result.stdout, result.stdout
    assert "1 classified, 1 unclassified, 2 selected" in result.stdout, result.stdout
    assert "Mutation results are incomplete" in result.stderr, result.stderr
    assert "| `skillsinc.sample` | 100.0% | 1 | 0 | 1 |" in result.stdout, result.stdout


@pytest.mark.parametrize("text", ["", "unrelated output\n", _outcomes("not checked")])
def test_no_classified_outcomes_have_no_score(tmp_path: Path, text: str) -> None:
    """Empty or wholly unclassified data never yields a fabricated 100%."""
    result = _report(tmp_path, text, "--fail-under", "0")

    assert result.returncode == 1, f"Expected an empty enforced gate to fail: {result}"
    assert "Classified score: N/A" in result.stdout, result.stdout
    assert "100.0%" not in result.stdout, result.stdout
    assert "Completeness: incomplete" in result.stdout, result.stdout


def test_missing_status_separator_is_an_unclassified_candidate(tmp_path: Path) -> None:
    """A mutant line without a colon cannot disappear from the denominator."""
    text = _outcomes("killed") + "\nskillsinc.other.xcheck__mutmut_1"
    result = _report(tmp_path, text, "--fail-under", "85")

    assert result.returncode == 1, f"Expected missing verdict to fail: {result}"
    assert "1 missing status" in result.stdout, result.stdout
    assert "| `skillsinc.other` | N/A | 0 | 0 | 1 |" in result.stdout, result.stdout


def test_report_only_output_labels_incomplete_data(tmp_path: Path) -> None:
    """Report-only mode shows incompleteness without claiming a gate passed."""
    result = _report(tmp_path, _outcomes("killed", "not checked"))

    assert result.returncode == 0, f"Expected report-only completion: {result}"
    assert "Completeness: incomplete" in result.stdout, result.stdout
    assert "1 unclassified" in result.stdout, result.stdout
    assert "pass" not in result.stdout.lower(), result.stdout


def test_scope_filters_unclassified_candidates_elsewhere(tmp_path: Path) -> None:
    """Unexecuted modules outside an explicit PR scope do not fail its gate."""
    text = _outcomes("killed") + "\n" + _outcomes("not checked", module="other")
    result = _report(
        tmp_path, text, "--fail-under", "100", "--only", "skillsinc.sample.*"
    )

    assert result.returncode == 0, f"Expected complete selected scope to pass: {result}"
    assert "1 classified, 0 unclassified, 1 selected" in result.stdout, result.stdout
    assert "skillsinc.other" not in result.stdout, result.stdout


def test_empty_selected_scope_cannot_pass(tmp_path: Path) -> None:
    """A misspelled module selection cannot turn an empty report into a pass."""
    result = _report(
        tmp_path,
        _outcomes("killed"),
        "--fail-under",
        "85",
        "--only",
        "skillsinc.missing.*",
    )

    assert result.returncode == 1, f"Expected no selected candidates to fail: {result}"
    assert "0 selected candidates" in result.stderr, result.stderr


def test_complete_low_score_still_fails(tmp_path: Path) -> None:
    """Completeness does not weaken the requested score threshold."""
    result = _report(tmp_path, _outcomes("killed", "survived"), "--fail-under", "85")

    assert result.returncode == 1, f"Expected score below floor to fail: {result}"
    assert "Mutation score 50.0% is below the 85.0% minimum" in result.stderr, (
        result.stderr
    )


@pytest.mark.parametrize("floor", ["-1", "101", "nan", "inf"])
def test_invalid_floor_is_a_usage_error(tmp_path: Path, floor: str) -> None:
    """Out-of-range and nonfinite floors cannot bypass score enforcement."""
    result = _report(tmp_path, _outcomes("survived"), "--fail-under", floor)

    assert result.returncode == 2, f"Expected an invalid-floor usage error: {result}"
    assert "must be between 0 and 100" in result.stderr, result.stderr


def _telemetry_flags(target: Path) -> tuple[str, ...]:
    """Bind an output to an exact synthetic workflow run and attempt."""
    return (
        "--json-output",
        str(target),
        "--commit",
        COMMIT.upper(),
        "--run-id",
        RUN_ID,
        "--run-attempt",
        RUN_ATTEMPT,
    )


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (("killed", "timeout"), ("healthy", 0, 100.0, 2, 0, 0)),
        (("killed",) * 17 + ("survived",) * 3, ("healthy", 0, 85.0, 17, 3, 0)),
        (("killed", "survived"), ("findings", 1, 50.0, 1, 1, 0)),
        (("killed", "not checked"), ("incomplete", 1, 100.0, 1, 0, 1)),
        (("not checked",), ("incomplete", 1, None, 0, 0, 1)),
        ((), ("incomplete", 1, None, 0, 0, 0)),
    ],
)
def test_json_telemetry_records_outcomes_before_gate_exit(
    tmp_path: Path,
    statuses: tuple[str, ...],
    expected: tuple[str, int, float | None, int, int, int],
) -> None:
    """The publisher gets exact identities, real counts, and honest classifications."""
    classification, code, score, detected, missed, unknown = expected
    target = tmp_path / "summary.json"
    result = _report(
        tmp_path, _outcomes(*statuses), "--fail-under", "85", *_telemetry_flags(target)
    )

    assert result.returncode == code, f"Expected gate exit {code}: {result}"
    assert target.is_file(), f"Expected JSON before gate exit {code}: {result}"
    telemetry = json.loads(target.read_text(encoding="utf-8"))
    assert telemetry == {
        "schema_version": 1,
        "commit": COMMIT,
        "run_id": RUN_ID,
        "run_attempt": RUN_ATTEMPT,
        "scope": "full-package",
        "complete": classification != "incomplete",
        "candidate_count": len(statuses),
        "classified_count": detected + missed,
        "unclassified_count": unknown,
        "detected": detected,
        "missed": missed,
        "score": score,
        "threshold": 85.0,
        "outcomes": {status: statuses.count(status) for status in statuses},
        "result": classification,
    }, f"Expected bounded real counts and run identity, got {telemetry!r}"


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--commit", "bad"),
        ("--commit", "a" * 39),
        ("--commit", "g" * 40),
        ("--commit", "a" * 40 + "\n"),
        ("--run-id", "0"),
        ("--run-id", "-1"),
        ("--run-id", "1.0"),
        ("--run-id", "1\n"),
        ("--run-id", "\uff11\uff12\uff13"),
        ("--run-id", "1" * 21),
        ("--run-attempt", "0"),
        ("--run-attempt", "-1"),
        ("--run-attempt", "1.0"),
        ("--run-attempt", "\uff11"),
    ],
)
def test_invalid_json_identity_writes_nothing(
    tmp_path: Path, flag: str, value: str
) -> None:
    """Identity validation fails as usage before telemetry or report output."""
    target = tmp_path / "summary.json"
    flags = list(_telemetry_flags(target))
    flags[flags.index(flag) + 1] = value
    result = _report(tmp_path, _outcomes("killed"), "--fail-under", "85", *flags)

    assert result.returncode == 2, f"Expected invalid identity to fail safely: {result}"
    assert result.stdout == "", f"Expected no report for invalid identity: {result}"
    assert not target.exists(), f"Expected no JSON for invalid identity: {result}"


@pytest.mark.parametrize(
    "missing", ["--json-output", "--commit", "--run-id", "--run-attempt"]
)
def test_telemetry_identity_and_output_are_required_together(
    tmp_path: Path, missing: str
) -> None:
    """Partial metadata cannot produce an apparently attributed telemetry file."""
    target = tmp_path / "summary.json"
    flags = list(_telemetry_flags(target))
    index = flags.index(missing)
    del flags[index : index + 2]
    result = _report(tmp_path, _outcomes("killed"), "--fail-under", "85", *flags)

    assert result.returncode == 2, f"Expected missing {missing} usage error: {result}"
    assert "required together" in result.stderr, result.stderr
    assert result.stdout == "", f"Expected no report for partial identity: {result}"
    assert not target.exists(), f"Expected no JSON without {missing}: {result}"


def test_json_telemetry_requires_a_threshold(tmp_path: Path) -> None:
    """A report-only invocation cannot manufacture a healthy telemetry result."""
    target = tmp_path / "summary.json"
    result = _report(tmp_path, _outcomes("killed"), *_telemetry_flags(target))

    assert result.returncode == 2, f"Expected missing threshold usage error: {result}"
    assert "explicit --fail-under" in result.stderr, result.stderr
    assert not target.exists(), "Expected no JSON without an explicit threshold"


@pytest.mark.parametrize("floor", ["0", "84", "100"])
def test_json_telemetry_cannot_lower_or_change_the_repository_floor(
    tmp_path: Path, floor: str
) -> None:
    """Published telemetry always uses the repository's fixed 85% gate."""
    target = tmp_path / "summary.json"
    result = _report(
        tmp_path, _outcomes("killed"), "--fail-under", floor, *_telemetry_flags(target)
    )

    assert result.returncode == 2, f"Expected altered telemetry floor error: {result}"
    assert "requires --fail-under 85" in result.stderr, result.stderr
    assert not target.exists(), "Expected no telemetry with an altered threshold"


def test_scoped_results_cannot_claim_full_package_telemetry(tmp_path: Path) -> None:
    """A selected subset must not be published as a full-package score."""
    target = tmp_path / "summary.json"
    result = _report(
        tmp_path,
        _outcomes("killed"),
        "--fail-under",
        "85",
        *_telemetry_flags(target),
        "--only",
        "skillsinc.sample.*",
    )

    assert result.returncode == 2, f"Expected subset telemetry to be refused: {result}"
    assert "cannot be combined with --only" in result.stderr, result.stderr
    assert not target.exists(), "Expected no falsely labeled full-package JSON"
