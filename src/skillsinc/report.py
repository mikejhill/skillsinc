"""Render plans, results, and profile tables as text or versioned JSON."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Final

from skillsinc.applier import DestinationResult
from skillsinc.config import SourceConfig
from skillsinc.models import Kind
from skillsinc.plan import Action, Change, DestinationPlan, Plan
from skillsinc.profiles.base import Profile
from skillsinc.remote import RemoteCache

REPORT_VERSION: Final = 1
SKILL_FILE_DEPTH: Final = 3  # skills/<name>/<file>
SYMBOLS: Final = {
    Action.CREATE: "+",
    Action.UPDATE: "~",
    Action.DELETE: "-",
    Action.ADOPT: "!",
    Action.TRACK: "=",
    Action.FORGET: "?",
}


@dataclass(frozen=True, slots=True)
class SourceStatus:
    """One source's health for ``skillsinc status``."""

    name: str
    type: str
    """``local``, ``github``, or ``git``."""
    location: str
    state: str
    """``ok``, ``missing``, ``not-fetched``, or ``failing``."""
    library: str | None = None
    ref: str | None = None
    commit: str | None = None
    updated_at: str | None = None
    checked_at: str | None = None
    succeeded_at: str | None = None
    failures: int = 0
    error: str | None = None
    category: str | None = None
    hint: str | None = None

    @property
    def healthy(self) -> bool:
        """Return True unless the source is missing or its last refresh failed."""
        return self.state in {"ok", "not-fetched"}

    @classmethod
    def of(cls, source: SourceConfig, cache: RemoteCache) -> SourceStatus:
        """Describe ``source`` from disk alone, without contacting a remote."""
        remote = source.remote
        if remote is None:
            found = source.path.is_dir()
            return cls(
                source.name,
                "local",
                str(source.path),
                "ok" if found else "missing",
                library=str(source.path),
            )
        stored = cache.state(source)
        library = cache.library(source)
        if stored.failing:
            state = "failing"
        elif library is None:
            state = "not-fetched"
        else:
            state = "ok"
        return cls(
            source.name,
            "github" if remote.github else "git",
            remote.label,
            state,
            library=None if library is None else str(library),
            ref=remote.ref,
            commit=stored.commit,
            updated_at=stored.updated_at,
            checked_at=stored.checked_at,
            succeeded_at=stored.succeeded_at,
            failures=stored.failures,
            error=stored.error,
            category=stored.category,
            hint=stored.hint,
        )


class Reporter:
    """Format SkillSinc output for people (text) or programs (JSON)."""

    def __init__(self, *, verbose: bool = False) -> None:
        self._verbose = verbose

    # Plans

    def plan_text(self, plan: Plan, *, applied: bool) -> str:
        """Return a readable plan, one section per destination."""
        lines: list[str] = [f"Project: {plan.project}"] if plan.project else []
        lines.extend(f"error: {message}" for message in plan.source_errors.values())
        lines.extend(f"warning: {message}" for message in plan.source_warnings.values())
        for destination in plan.destinations:
            lines.extend(self._destination_text(destination))
        if not plan.destinations:
            lines.append("No enabled destinations are routed. Edit the configuration.")
        if plan.warnings:
            lines.append("")
            lines.extend(f"warning: {warning}" for warning in plan.warnings)
        lines.append("")
        lines.append(self._summary(plan, applied=applied))
        return "\n".join(lines) + "\n"

    def plan_json(
        self, plan: Plan, results: Sequence[DestinationResult] | None = None
    ) -> str:
        """Return the plan (and, after a sync, the results) as versioned JSON."""
        outcome = {result.name: result for result in results or ()}
        data = {
            "version": REPORT_VERSION,
            "ok": plan.ok and all(result.error is None for result in outcome.values()),
            "applied": results is not None,
            "project": plan.project or None,
            "source_errors": dict(plan.source_errors),
            "source_warnings": dict(plan.source_warnings),
            "warnings": list(plan.warnings),
            "destinations": [
                self._destination_json(
                    destination, outcome.get(destination.destination.name)
                )
                for destination in plan.destinations
            ],
        }
        return json.dumps(data, indent=2) + "\n"

    def results_text(self, results: Sequence[DestinationResult]) -> str:
        """Return one line per destination that failed while applying."""
        return "".join(
            f"error: {result.name}: stopped after {result.applied} change(s):"
            f" {result.error}\n"
            for result in results
            if result.error is not None
        )

    # Profiles

    @staticmethod
    def status_text(states: Sequence[SourceStatus]) -> str:
        """Return each source's state, with the error and remedy when failing."""
        lines: list[str] = []
        for status in states:
            ref = f" (ref {status.ref})" if status.ref is not None else ""
            lines.append(f"{status.name}: {status.type} {status.location}{ref}")
            details: list[tuple[str, str | None]] = [
                ("state", Reporter._state_label(status))
            ]
            if status.type != "local":
                commit = status.commit[:12] if status.commit is not None else None
                details += [
                    ("commit", commit),
                    ("updated", status.updated_at),
                    ("last checked", status.checked_at),
                    ("last success", status.succeeded_at),
                    ("error", status.error),
                    ("fix", status.hint),
                ]
            lines.extend(
                f"  {label}: {value}" for label, value in details if value is not None
            )
        if not states:
            lines.append("No sources are configured.")
        failing = [status.name for status in states if not status.healthy]
        lines.append("")
        lines.append(
            f"Needs attention: {', '.join(failing)}."
            if failing
            else "All sources are healthy."
        )
        return "\n".join(lines) + "\n"

    @staticmethod
    def status_json(states: Sequence[SourceStatus]) -> str:
        """Return each source's state as versioned JSON."""
        data = {
            "version": REPORT_VERSION,
            "ok": all(status.healthy for status in states),
            "sources": [
                {**asdict(status), "healthy": status.healthy} for status in states
            ],
        }
        return json.dumps(data, indent=2) + "\n"

    @staticmethod
    def _state_label(status: SourceStatus) -> str:
        """Return a readable state."""
        if status.state == "failing":
            plural = "es" if status.failures != 1 else ""
            serving = (
                "serving the cached copy"
                if status.library is not None
                else "nothing cached yet"
            )
            return f"failing ({status.failures} failed refresh{plural}; {serving})"
        if status.state == "not-fetched":
            return "not fetched yet; `skillsinc sync` fetches it"
        if status.state == "missing":
            return "directory not found"
        return "ok"

    @staticmethod
    def profiles_text(profiles: Sequence[Profile]) -> str:
        """Return a table of profiles and what each kind becomes."""
        header = ["profile", *[kind.value for kind in Kind], "description"]
        rows = [
            [
                profile.name,
                *[profile.support[kind].value for kind in Kind],
                profile.summary,
            ]
            for profile in profiles
        ]
        widths = [
            max(len(row[i]) for row in [header, *rows]) for i in range(len(header))
        ]
        return "".join(
            "  ".join(
                cell.ljust(width) for cell, width in zip(row, widths, strict=True)
            ).rstrip()
            + "\n"
            for row in [header, *rows]
        )

    # Internals

    def _destination_text(self, plan: DestinationPlan) -> list[str]:
        """Return the lines describing one destination."""
        destination = plan.destination
        kind = "removed from configuration" if plan.retired else destination.profile
        lines = ["", f"{destination.name} ({kind}) -> {destination.path}"]
        lines.extend(
            f"  error: {error}" for error in self._collapse_errors(plan.errors)
        )
        changes = [
            (change.path, self._change_label(change), self._change_suffix(change))
            for change in plan.changes
        ]
        lines.extend(
            f"  {label} {path}{suffix}"
            for path, label, suffix in self._collapse(changes)
        )
        for skip in plan.skipped:
            if skip.lossy:
                lines.append(f"  skipped (lossy) {skip.item}: {skip.reason}")
            elif self._verbose:
                lines.append(f"  skipped {skip.item}: {skip.reason}")
        if self._verbose:
            lines.extend(f"  note: {note}" for note in plan.notes)
        if not plan.errors and not plan.changes:
            lines.append("  up to date")
        return lines

    @staticmethod
    def _change_label(change: Change) -> str:
        """Return the symbol and action column for a change."""
        return f"{SYMBOLS[change.action]} {change.action.value:<6}"

    @staticmethod
    def _change_suffix(change: Change) -> str:
        """Return the block marker and note shown after a change's path."""
        suffix = " [managed block]" if change.block else ""
        if change.entry:
            suffix = f" [{change.entry}]"
        return suffix + (f"  ({change.note})" if change.note else "")

    def _collapse_errors(self, errors: Sequence[str]) -> list[str]:
        """Merge per-file errors with the same message inside one skill."""
        entries = []
        for error in errors:
            path, separator, message = error.partition(": ")
            if not separator:
                path, message = error, ""
            entries.append((path, "", f": {message}" if message else ""))
        return [f"{path}{message}" for path, _, message in self._collapse(entries)]

    def _collapse(
        self, entries: Sequence[tuple[str, str, str]]
    ) -> list[tuple[str, str, str]]:
        """Merge consecutive ``(path, label, suffix)`` entries within one skill.

        Unless verbose, ``skills/<name>/...`` files that share a label and suffix
        become one ``skills/<name>/ (N files)`` entry.
        """
        merged: list[tuple[str, str, str, int]] = []
        for path, label, suffix in entries:
            group = self._skill_directory(path) or path
            if merged and merged[-1][:3] == (group, label, suffix):
                merged[-1] = (group, label, suffix, merged[-1][3] + 1)
            else:
                merged.append((group, label, suffix, 1))

        collapsed: list[tuple[str, str, str]] = []
        for group, label, suffix, count in merged:
            plural = "" if count == 1 else "s"
            shown = f"{group} ({count} file{plural})" if group.endswith("/") else group
            collapsed.append((shown, label, suffix))
        return collapsed

    def _skill_directory(self, path: str) -> str | None:
        """Return ``skills/<name>/`` for a file inside a skill, unless verbose."""
        parts = path.split("/")
        if self._verbose or parts[0] != "skills" or len(parts) < SKILL_FILE_DEPTH:
            return None
        return f"skills/{parts[1]}/"

    @staticmethod
    def _summary(plan: Plan, *, applied: bool) -> str:
        """Return the closing summary line."""
        counts = dict.fromkeys(Action, 0)
        for destination in plan.destinations:
            if destination.blocked:
                continue
            for change in destination.changes:
                counts[change.action] += 1
        parts = [f"{count} {action.value}" for action, count in counts.items() if count]
        skipped = sum(len(destination.skipped) for destination in plan.destinations)
        blocked = sum(1 for destination in plan.destinations if destination.blocked)
        if skipped:
            parts.append(f"{skipped} skipped")
        accepted = sum(len(destination.notes) for destination in plan.destinations)
        if accepted:
            parts.append(f"{accepted} accepted conversion loss(es); -v lists them")
        if blocked:
            parts.append(f"{blocked} destination(s) blocked")
        summary = ", ".join(parts) if parts else "no changes"
        if applied or not plan.has_changes:
            return f"Summary: {summary}."
        return f"Summary: {summary}. Run `skillsinc sync` to apply."

    @staticmethod
    def _destination_json(
        plan: DestinationPlan, result: DestinationResult | None
    ) -> dict[str, object]:
        """Return one destination as a JSON-ready mapping."""
        destination = plan.destination
        return {
            "name": destination.name,
            "profile": destination.profile or None,
            "path": str(destination.path),
            "retired": plan.retired,
            "blocked": plan.blocked,
            "errors": list(plan.errors)
            + (
                [result.error]
                if result is not None and result.error is not None
                else []
            ),
            "changes": [
                {
                    "action": change.action.value,
                    "path": change.path,
                    "block": change.block,
                    "entry": change.entry or None,
                    "note": change.note,
                }
                for change in plan.changes
            ],
            "skipped": [
                {"item": skip.item, "reason": skip.reason, "lossy": skip.lossy}
                for skip in plan.skipped
            ],
            "notes": list(plan.notes),
            "applied": result.applied if result else 0,
        }
