# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Render a JUnit XML report as a GitHub job summary and failure annotations."""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Failure:
    """One failed or errored test case."""

    name: str
    message: str


@dataclass(frozen=True, slots=True)
class Totals:
    """Counts across every test suite in a report."""

    tests: int
    failures: int
    errors: int
    skipped: int

    @property
    def passed(self) -> int:
        """Return the number of passing tests."""
        return self.tests - self.failures - self.errors - self.skipped


class JUnitSummary:
    """Summarize a JUnit XML file for the GitHub Actions run page."""

    def __init__(self, path: Path, title: str) -> None:
        self._path = path
        self._title = title

    @classmethod
    def main(cls, argv: Sequence[str] | None = None) -> None:
        """Print the summary to stdout and annotations for failures."""
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("report", type=Path, help="JUnit XML file")
        parser.add_argument("--title", default="Tests", help="summary heading")
        args = parser.parse_args(argv)
        sys.exit(cls(Path(args.report), str(args.title)).run())

    def run(self) -> int:
        """Write the summary; return 1 if the report is missing."""
        if not self._path.is_file():
            sys.stdout.write(f"### {self._title}\n\nNo test report was produced.\n")
            return 1
        root = ET.parse(self._path).getroot()  # noqa: S314 — trusted local file
        totals = self._totals(root)
        failures = self._failures(root)
        sys.stdout.write(self._markdown(totals, failures))
        for failure in failures:
            sys.stderr.write(f"::error title={failure.name}::{failure.message}\n")
        return 0

    # Internals

    @staticmethod
    def _totals(root: ET.Element) -> Totals:
        """Add up counts from every ``testsuite`` element."""
        suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
        counts = [
            sum(int(suite.get(key, "0")) for suite in suites)
            for key in ("tests", "failures", "errors", "skipped")
        ]
        return Totals(*counts)

    @staticmethod
    def _failures(root: ET.Element) -> list[Failure]:
        """Collect failed and errored test cases."""
        failures: list[Failure] = []
        for case in root.iter("testcase"):
            problem = case.find("failure")
            if problem is None:
                problem = case.find("error")
            if problem is None:
                continue
            name = f"{case.get('classname', '')}::{case.get('name', '')}"
            message = (problem.get("message") or "failed").splitlines()[0][:300]
            failures.append(Failure(name, message))
        return failures

    def _markdown(self, totals: Totals, failures: Sequence[Failure]) -> str:
        """Return the job-summary Markdown."""
        status = "passed" if not failures else "failed"
        lines = [
            f"### {self._title}: {status}",
            "",
            "| Passed | Failed | Errors | Skipped | Total |",
            "| -----: | -----: | -----: | ------: | ----: |",
            f"| {totals.passed} | {totals.failures} | {totals.errors}"
            f" | {totals.skipped} | {totals.tests} |",
        ]
        if failures:
            lines.extend(["", "| Test | Message |", "| ---- | ------- |"])
            lines.extend(
                f"| `{failure.name}` | {failure.message.replace('|', '/')} |"
                for failure in failures
            )
        return "\n".join(lines) + "\n"


if __name__ == "__main__":
    JUnitSummary.main()
