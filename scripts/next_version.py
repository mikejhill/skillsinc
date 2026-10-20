# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Compute the next release version from Conventional Commits since the last tag."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Sequence
from enum import IntEnum
from typing import Final

HEADER: Final = re.compile(r"^(?P<type>[a-z]+)(?:\([^)]*\))?(?P<bang>!)?: ")
TAG: Final = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
SEPARATOR: Final = "\x1e"


class Bump(IntEnum):
    """Version bump levels, ordered by significance."""

    NONE = 0
    PATCH = 1
    MINOR = 2
    MAJOR = 3


class NextVersion:
    """Derive ``MAJOR.MINOR.PATCH`` from tags and commit messages.

    While the major version is 0, breaking changes bump the minor version.
    """

    @classmethod
    def main(cls, argv: Sequence[str] | None = None) -> None:
        """Print the next version, or nothing when no release is due."""
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument(
            "--force-patch",
            action="store_true",
            help="release a patch even without feat/fix commits",
        )
        args = parser.parse_args(argv)
        version = cls().compute(force_patch=bool(args.force_patch))
        if version:
            sys.stdout.write(version + "\n")

    def compute(self, *, force_patch: bool) -> str:
        """Return the next version, or an empty string."""
        tag = self._last_tag()
        current = self._parse(tag) if tag else (0, 0, 0)
        bump = max(
            (self.classify(message) for message in self._messages(tag)),
            default=Bump.NONE,
        )
        if bump is Bump.NONE and force_patch:
            bump = Bump.PATCH
        return self.apply(current, bump)

    @staticmethod
    def classify(message: str) -> Bump:
        """Return the bump a single commit message calls for."""
        match = HEADER.match(message)
        breaking = "BREAKING CHANGE:" in message or "BREAKING-CHANGE:" in message
        if match is None:
            return Bump.MAJOR if breaking else Bump.NONE
        if breaking or match.group("bang"):
            return Bump.MAJOR
        kind = match.group("type")
        if kind == "feat":
            return Bump.MINOR
        if kind in {"fix", "perf"}:
            return Bump.PATCH
        return Bump.NONE

    @staticmethod
    def apply(current: tuple[int, int, int], bump: Bump) -> str:
        """Return ``current`` bumped by ``bump``, or an empty string for no bump."""
        major, minor, patch = current
        if bump is Bump.MAJOR and major == 0:
            bump = Bump.MINOR
        match bump:
            case Bump.MAJOR:
                return f"{major + 1}.0.0"
            case Bump.MINOR:
                return f"{major}.{minor + 1}.0"
            case Bump.PATCH:
                return f"{major}.{minor}.{patch + 1}"
            case Bump.NONE:
                return ""

    # Internals

    @staticmethod
    def _git(*args: str) -> str:
        """Run git and return its stdout."""
        result = subprocess.run(  # noqa: S603 — fixed git arguments
            ["git", *args],  # noqa: S607 — git is resolved from PATH in CI
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout if result.returncode == 0 else ""

    def _last_tag(self) -> str:
        """Return the most recent ``vX.Y.Z`` tag reachable from HEAD."""
        return self._git("describe", "--tags", "--abbrev=0", "--match", "v*").strip()

    def _messages(self, tag: str) -> list[str]:
        """Return full commit messages since ``tag`` (or all history)."""
        span = f"{tag}..HEAD" if tag else "HEAD"
        output = self._git("log", f"--format=%B{SEPARATOR}", span)
        return [
            message.strip() for message in output.split(SEPARATOR) if message.strip()
        ]

    @staticmethod
    def _parse(tag: str) -> tuple[int, int, int]:
        """Return the numeric parts of a ``vX.Y.Z`` tag."""
        match = TAG.match(tag)
        if match is None:
            msg = f"unsupported tag format: {tag}"
            raise SystemExit(msg)
        return int(match.group(1)), int(match.group(2)), int(match.group(3))


if __name__ == "__main__":
    NextVersion.main()
