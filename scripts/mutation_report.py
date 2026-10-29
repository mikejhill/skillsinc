# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Summarize a mutmut run: score per module, surviving mutants, and a gate.

A mutant is a small change to the code (``<`` to ``<=``, ``True`` to
``False``, a dropped call). The tests should fail for each one; a mutant they
let through ("survived") marks behavior no test checks.

Reads the results of the last ``mutmut run`` from ``mutmut results --all
true``, prints a Markdown report, and with ``--fail-under`` rejects incomplete
or empty results as well as scores below the requested floor.

Killed, timed-out, type-check-rejected, and segfaulting mutants count as caught.
Survivors, mutants with no tests, and suspicious outcomes count as missed.
Other statuses are unclassified; they never establish a passing score.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, TypedDict

if TYPE_CHECKING:
    from collections.abc import Sequence

DETECTED: Final = frozenset({"killed", "timeout", "caught by type check", "segfault"})
UNDETECTED: Final = frozenset({"survived", "no tests", "suspicious"})
MAX_PERCENT: Final = 100
TELEMETRY_THRESHOLD: Final = 85.0
COMMIT_SHA: Final = re.compile(r"[0-9a-fA-F]{40}")
RUN_ID: Final = re.compile(r"[1-9][0-9]{0,19}")
OUTCOME_POLICY: Final = (
    "Caught includes killed, timeout, caught by type check, and segfault. "
    "No tests and suspicious outcomes count as missed. "
    "Enforced gates require nonempty, fully classified results."
)


class MutationSummary(TypedDict):
    """Versioned telemetry consumed by the asynchronous findings publisher."""

    schema_version: int
    commit: str
    run_id: str
    run_attempt: str
    scope: str
    complete: bool
    candidate_count: int
    classified_count: int
    unclassified_count: int
    detected: int
    missed: int
    score: float | None
    threshold: float
    outcomes: dict[str, int]
    result: Literal["healthy", "findings", "incomplete"]


@dataclass(frozen=True, slots=True)
class Telemetry:
    """Validated identity, output path, and threshold for a full-package report."""

    path: Path
    commit: str
    run_id: str
    run_attempt: str
    threshold: float
    scope: str

    @classmethod
    def parse(
        cls, args: argparse.Namespace, parser: argparse.ArgumentParser
    ) -> Telemetry | None:
        """Require complete telemetry arguments before reading or writing files."""
        provided = (
            args.json_output is not None,
            args.commit is not None,
            args.run_id is not None,
            args.run_attempt is not None,
        )
        if not any(provided):
            return None
        if not all(provided):
            parser.error(
                "--json-output, --commit, --run-id, and --run-attempt "
                "are required together"
            )
        if args.fail_under is None:
            parser.error("--json-output requires an explicit --fail-under threshold")
        if args.fail_under != TELEMETRY_THRESHOLD:
            parser.error(
                "--json-output requires --fail-under 85 for the repository gate"
            )
        if COMMIT_SHA.fullmatch(args.commit) is None:
            parser.error("--commit must be a full 40-character hexadecimal commit SHA")
        if RUN_ID.fullmatch(args.run_id) is None:
            parser.error(
                "--run-id must be a positive ASCII decimal ID of at most 20 digits"
            )
        if RUN_ID.fullmatch(args.run_attempt) is None:
            parser.error(
                "--run-attempt must be a positive ASCII decimal ID of at most 20 digits"
            )
        if args.only:
            parser.error("full-package JSON telemetry cannot be combined with --only")
        return cls(
            args.json_output,
            args.commit.lower(),
            args.run_id,
            args.run_attempt,
            args.fail_under,
            args.scope,
        )

    def write(self, report: MutationReport) -> None:
        """Write real classified counts even when the enforced gate will fail."""
        self.path.write_text(
            json.dumps(report.summary(self), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


@dataclass(slots=True)
class ModuleScore:
    """Mutant outcomes for one module."""

    detected: int = 0
    undetected: list[str] = field(default_factory=list)
    unclassified: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        """Classified mutants that count toward the score."""
        return self.detected + len(self.undetected)

    @property
    def candidates(self) -> int:
        """All selected mutant outcomes, including unclassified results."""
        return self.total + len(self.unclassified)

    @property
    def score(self) -> float | None:
        """Share caught among classified mutants, or None when there are none."""
        return None if self.total == 0 else 100 * self.detected / self.total

    @property
    def score_text(self) -> str:
        """Render a classified score without inventing one for empty results."""
        score = self.score
        return "N/A" if score is None else f"{score:.1f}%"


@dataclass(slots=True)
class MutationReport:
    """Scores by module, parsed from ``mutmut results --all true``."""

    modules: dict[str, ModuleScore] = field(
        default_factory=lambda: defaultdict(ModuleScore)
    )
    statuses: Counter[str] = field(default_factory=Counter)

    @classmethod
    def parse(cls, text: str, only: Sequence[str] = ()) -> MutationReport:
        """Return the report for mutmut's ``name: status`` lines.

        With ``only``, keep just the mutants matching one of those mutmut
        patterns (``skillsinc.blocks.*``).
        """
        report = cls()
        for line in text.splitlines():
            name, _, status = line.strip().partition(":")
            if "__mutmut_" not in name:
                continue
            if only and not any(fnmatch.fnmatchcase(name, p) for p in only):
                continue
            status = status.strip() or "missing status"
            report.statuses[status] += 1
            module = report.modules[name.rpartition(".")[0]]
            if status in DETECTED:
                module.detected += 1
            elif status in UNDETECTED:
                module.undetected.append(name)
            else:
                module.unclassified.append(name)
        return report

    @property
    def overall(self) -> ModuleScore:
        """All modules combined."""
        total = ModuleScore()
        for module in self.modules.values():
            total.detected += module.detected
            total.undetected += module.undetected
            total.unclassified += module.unclassified
        return total

    @property
    def complete(self) -> bool:
        """Whether every selected candidate has a known outcome and some exist."""
        overall = self.overall
        return overall.total > 0 and not overall.unclassified

    def summary(self, telemetry: Telemetry) -> MutationSummary:
        """Return counts, score, and classification bound to this run's identity."""
        overall = self.overall
        score = overall.score
        result: Literal["healthy", "findings", "incomplete"] = "incomplete"
        if self.complete and score is not None:
            result = "findings" if score < telemetry.threshold else "healthy"
        return {
            "schema_version": 1,
            "commit": telemetry.commit,
            "run_id": telemetry.run_id,
            "run_attempt": telemetry.run_attempt,
            "scope": telemetry.scope,
            "complete": self.complete,
            "candidate_count": overall.candidates,
            "classified_count": overall.total,
            "unclassified_count": len(overall.unclassified),
            "detected": overall.detected,
            "missed": len(overall.undetected),
            "score": score,
            "threshold": telemetry.threshold,
            "outcomes": dict(sorted(self.statuses.items())),
            "result": result,
        }

    def markdown(self, *, show_diffs: int) -> str:
        """Return the report as Markdown, with diffs for the first survivors."""
        overall = self.overall
        lines = [
            "### Mutation testing",
            "",
            f"**Classified score: {overall.score_text}** - {overall.detected} of "
            f"{overall.total} classified mutant outcomes caught.",
            "",
            f"Completeness: {'complete' if self.complete else 'incomplete'}; "
            f"{overall.total} classified, {len(overall.unclassified)} unclassified, "
            f"{overall.candidates} selected candidate outcomes.",
            "",
            OUTCOME_POLICY,
            "",
            "Outcomes: "
            + (
                ", ".join(f"{n} {s}" for s, n in sorted(self.statuses.items()))
                or "none"
            ),
            "",
            "| Module | Classified score | Caught | Missed | Unclassified |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
        ranked = sorted(self.modules.items(), key=lambda item: item[1].score or 0.0)
        lines += [
            f"| `{name}` | {m.score_text} | {m.detected} | {len(m.undetected)} "
            f"| {len(m.unclassified)} |"
            for name, m in ranked
        ]
        missed = overall.undetected
        if missed and show_diffs > 0:
            lines += ["", f"#### First {min(show_diffs, len(missed))} missed mutants"]
            lines += ["", "Inspect these missed outcomes to strengthen the tests.", ""]
            for name in missed[:show_diffs]:
                lines += ["```diff", self.diff(name), "```"]
        return "\n".join(lines) + "\n"

    @staticmethod
    def diff(name: str) -> str:
        """Return mutmut's diff for mutant ``name``."""
        shown = subprocess.run(  # noqa: S603 — fixed program, name from mutmut
            ["mutmut", "show", name],  # noqa: S607 — run inside the uv environment
            capture_output=True,
            text=True,
            check=False,
        )
        return shown.stdout.strip() or shown.stderr.strip()


def main() -> int:
    """Print the report and enforce complete results when a floor is supplied."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "results",
        type=Path,
        help="File holding the output of `mutmut results --all true`.",
    )
    parser.add_argument(
        "--fail-under",
        type=float,
        default=None,
        help="Enforce complete results and this score in percent "
        "(default: report only).",
    )
    parser.add_argument(
        "--show-diffs",
        type=int,
        default=0,
        help="Include the diffs of this many missed mutants.",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        default=[],
        metavar="PATTERN",
        help="Report only mutants matching these mutmut patterns.",
    )
    parser.add_argument(
        "--json-output", type=Path, help="Write versioned full-package telemetry here."
    )
    parser.add_argument("--commit", help="Full commit SHA for JSON telemetry.")
    parser.add_argument("--run-id", help="GitHub Actions run ID for JSON telemetry.")
    parser.add_argument(
        "--run-attempt", help="GitHub Actions run attempt for JSON telemetry."
    )
    parser.add_argument(
        "--scope",
        choices=["full-package"],
        default="full-package",
        help="Scope of JSON telemetry (default: full-package).",
    )
    args = parser.parse_args()
    if args.fail_under is not None and not 0 <= args.fail_under <= MAX_PERCENT:
        parser.error("--fail-under must be between 0 and 100")
    telemetry = Telemetry.parse(args, parser)
    report = MutationReport.parse(
        args.results.read_text(encoding="utf-8"), only=args.only
    )
    sys.stdout.write(report.markdown(show_diffs=args.show_diffs))
    if telemetry is not None:
        telemetry.write(report)
    if args.fail_under is None:
        return 0
    if not report.complete:
        overall = report.overall
        sys.stderr.write(
            "Mutation results are incomplete: "
            f"{overall.candidates} selected candidates, "
            f"{len(overall.unclassified)} unclassified outcomes; "
            "a nonempty, fully classified result is required\n"
        )
        return 1
    score = report.overall.score
    if score is not None and score < args.fail_under:
        sys.stderr.write(
            f"Mutation score {score:.1f}% is below the {args.fail_under}% minimum\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
