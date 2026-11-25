"""The sync history: every run recorded, read back newest first, kept bounded."""

from __future__ import annotations

import io
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.conftest import Builder, Sandbox

from skillsinc.__main__ import Application
from skillsinc.config import DestinationConfig
from skillsinc.history import (
    HISTORY_FILE,
    MAXIMUM_CHANGES,
    ChangeRecord,
    SyncHistory,
    SyncRecord,
    SyncRecorder,
    Trigger,
)
from skillsinc.jsonc import Jsonc
from skillsinc.paths import Environment
from skillsinc.plan import Action, Change, DestinationPlan, Plan
from skillsinc.usersettings import HistoryOptions, SettingsStore, UserSettings

START = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def _record(index: int = 0, **changes: Any) -> SyncRecord:  # noqa: ANN401
    """Return a successful record ``index`` minutes after START, with ``changes``."""
    started = START + timedelta(minutes=index)
    base = SyncRecord(
        id=f"id{index}",
        started=started,
        finished=started + timedelta(seconds=2),
        trigger=Trigger.MANUAL,
        ok=True,
        config="/c/config.json",
        counts={"create": 1},
    )
    return replace(base, **changes)


class TestRecord:
    """A record summarizes a run and survives a JSON round trip."""

    def test_round_trip_keeps_every_field(self) -> None:
        """to_json and from_json are inverses."""
        record = _record(
            profiles=("work", "home"),
            version="1.2.3",
            destinations=3,
            errors=("a: broke",),
            ok=False,
            warnings=2,
            changes=(ChangeRecord("claude", "create", "skills/x/SKILL.md"),),
            truncated=True,
        )

        assert SyncRecord.from_json(record.to_json()) == record

    def test_summary_counts_changes_or_names_the_failure(self) -> None:
        """Success lists changes; failure shows the first error and a count."""
        assert _record(counts={"create": 2, "delete": 1}).summary() == (
            "2 create, 1 delete"
        )
        assert _record(counts={}).summary() == "no changes"
        failed = _record(ok=False, errors=("first", "second", "third"))
        assert failed.summary() == "first (+2 more)"
        assert _record(ok=False, errors=()).summary() == "failed"

    def test_duration_and_changed(self) -> None:
        """Duration is in seconds; changed totals every action."""
        record = _record(counts={"create": 2, "update": 3})

        assert record.duration == 2.0
        assert record.changed == 5

    @pytest.mark.parametrize(
        "data",
        [
            {},
            {"id": "x", "started": "nope", "finished": "nope", "trigger": "manual"},
            {
                "id": "x",
                "started": START.isoformat(),
                "finished": START.isoformat(),
                "trigger": "telepathy",
                "ok": True,
            },
            {
                "id": "x",
                "started": START.isoformat(),
                "finished": START.isoformat(),
                "trigger": "manual",
                "ok": True,
                "counts": [1],
            },
        ],
    )
    def test_malformed_record_is_a_value_error(self, data: dict[str, object]) -> None:
        """Every malformed shape raises ValueError, never anything else."""
        with pytest.raises(ValueError, match="malformed history record"):
            SyncRecord.from_json(data)


class TestOfPlan:
    """A record built from a plan counts unblocked changes and every error."""

    @staticmethod
    def _destination(
        name: str, count: int, *, errors: tuple[str, ...] = ()
    ) -> DestinationPlan:
        return DestinationPlan(
            DestinationConfig(name, "generic", Path(name)),
            changes=tuple(
                Change(Action.CREATE, f"f{index}", None) for index in range(count)
            ),
            errors=errors,
        )

    def test_blocked_destinations_and_errors(self) -> None:
        """Blocked changes are not counted; source and destination errors fail it."""
        plan = Plan(
            (
                self._destination("good", 3),
                self._destination("bad", 5, errors=("edited",)),
            ),
            {"library": "unreadable"},
            source_warnings={"remote": "stale"},
        )

        record = SyncRecord.of_plan(
            plan,
            started=START,
            finished=START,
            trigger=Trigger.TRAY,
            config=Path("c.json"),
            profiles=None,
            version="1",
            errors=["good: disk full"],
        )

        assert record.counts == {"create": 3}
        assert record.errors == (
            "library: unreadable",
            "bad: edited",
            "good: disk full",
        )
        assert (record.ok, record.warnings, record.destinations) == (False, 1, 2)

    def test_changes_are_capped(self) -> None:
        """Only the first MAXIMUM_CHANGES changes are kept, and that is flagged."""
        plan = Plan(
            (self._destination("big", MAXIMUM_CHANGES + 5),),
            {},
        )

        record = SyncRecord.of_plan(
            plan,
            started=START,
            finished=START,
            trigger=Trigger.MANUAL,
            config=Path("c.json"),
            profiles=("a",),
            version="1",
        )

        assert len(record.changes) == MAXIMUM_CHANGES
        assert record.truncated is True
        assert record.counts == {"create": MAXIMUM_CHANGES + 5}
        assert record.ok is True


class TestRecorder:
    """The recorder writes one record per run, into the right directory."""

    def test_failure_after_success_is_not_recorded_twice(self, tmp_path: Path) -> None:
        """An error raised after the record was written adds nothing."""
        recorder = SyncRecorder(
            state=tmp_path / "user",
            keep=10,
            trigger=Trigger.MANUAL,
            config=Path("c.json"),
            requested=(),
            version="1",
            clock=lambda: START,
        )
        recorder.locate(tmp_path / "project")
        recorder.applied(Plan((), {}), profiles=None)

        assert recorder.failed("late") is None
        records = SyncHistory(tmp_path / "project", keep=10).records()
        assert [record.ok for record in records] == [True]
        assert SyncHistory(tmp_path / "user", keep=10).records() == []


class TestStore:
    """Append, read newest first, skip garbage, trim."""

    def test_records_are_newest_first_and_limited(self, tmp_path: Path) -> None:
        """Reading returns the latest runs first."""
        history = SyncHistory(tmp_path, keep=100)
        for index in range(5):
            history.append(_record(index))

        ids = [record.id for record in history.records(limit=3)]

        assert ids == ["id4", "id3", "id2"], f"Got {ids}"
        last = history.last()
        assert last is not None
        assert last.id == "id4"

    def test_missing_file_has_no_records(self, tmp_path: Path) -> None:
        """A machine that never synced has an empty history."""
        history = SyncHistory(tmp_path / "missing", keep=10)

        assert history.records() == []
        assert history.last() is None

    def test_unreadable_lines_are_skipped(self, tmp_path: Path) -> None:
        """A torn or hand-edited line does not hide the others."""
        history = SyncHistory(tmp_path, keep=10)
        history.append(_record(0))
        with history.path.open("a", encoding="utf-8") as file:
            file.write('{"id": "torn"\n[1, 2]\n{"id": "x"}\n\n')
        history.append(_record(1))

        assert [record.id for record in history.records()] == ["id1", "id0"]

    def test_file_is_trimmed_to_keep_once_far_over(self, tmp_path: Path) -> None:
        """After twice ``keep`` records, the oldest are dropped down to ``keep``."""
        history = SyncHistory(tmp_path, keep=10)
        for index in range(21):
            history.append(_record(index, changes=(ChangeRecord("d", "a", "p"),) * 3))

        lines = history.path.read_text(encoding="utf-8").splitlines()

        assert len(lines) == 10, f"Expected 10 kept, got {len(lines)}"
        assert history.records()[-1].id == "id11"

    def test_clear_removes_every_record(self, tmp_path: Path) -> None:
        """Clearing deletes the file."""
        history = SyncHistory(tmp_path, keep=10)
        history.append(_record())

        history.clear()

        assert history.records() == []
        assert not history.path.exists()

    def test_write_failure_is_logged_not_raised(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """History must never fail a sync."""
        blocker = tmp_path / "state"
        blocker.write_text("a file where the directory belongs", encoding="utf-8")

        SyncHistory(blocker, keep=10).append(_record())

        assert "Could not record the sync" in caplog.text


def _history(environment: Environment) -> list[SyncRecord]:
    """Return the user's recorded runs, newest first."""
    return SyncHistory(environment.state_directory, keep=1000).records()


class TestSyncRecordsHistory:
    """``skillsinc sync`` records every run, whatever its outcome."""

    def test_successful_sync_records_its_changes(self, sandbox: Sandbox) -> None:
        """A first sync records what it created and the trigger."""
        sandbox.configure()

        status, _ = sandbox.run("sync", "--trigger", "tray")

        records = _history(sandbox.environment)
        assert status == 0
        assert len(records) == 1, f"Got {records}"
        record = records[0]
        assert (record.ok, record.trigger) == (True, Trigger.TRAY)
        assert record.counts.get("create", 0) > 0, f"Got {record.counts}"
        assert record.config == str(sandbox.config_path)
        assert record.profiles is None, "Expected no profiles in this config"
        assert record.changes, "Expected the changes listed"
        assert record.version, "Expected the release recorded"

    def test_converged_sync_records_no_changes(self, sandbox: Sandbox) -> None:
        """A second run is recorded too, with nothing changed."""
        sandbox.configure()
        sandbox.run("sync")

        sandbox.run("sync")

        newest = _history(sandbox.environment)[0]
        assert (newest.ok, newest.summary()) == (True, "no changes")
        assert newest.trigger is Trigger.MANUAL

    def test_plan_records_nothing(self, sandbox: Sandbox) -> None:
        """Previewing is not a sync."""
        sandbox.configure()

        sandbox.run("plan")

        assert _history(sandbox.environment) == []

    def test_configuration_error_is_recorded_as_a_failure(
        self, sandbox: Sandbox
    ) -> None:
        """A sync that cannot load its configuration is still in the history."""
        sandbox.config_path.parent.mkdir(parents=True)
        sandbox.config_path.write_text("{ not json", encoding="utf-8")

        status, _ = sandbox.run("sync", "--profile", "work")

        records = _history(sandbox.environment)
        assert status == 2
        assert len(records) == 1, f"Got {records}"
        assert records[0].ok is False
        assert records[0].profiles == ("work",), "Expected the requested profiles"
        assert records[0].errors, "Expected the error recorded"

    def test_lock_conflict_is_recorded_as_a_failure(self, sandbox: Sandbox) -> None:
        """A sync refused by another run's lock is recorded."""
        sandbox.configure()
        lock = sandbox.environment.state_directory / "sync.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("1\n", encoding="utf-8")

        status, _ = sandbox.run("sync")

        records = _history(sandbox.environment)
        assert status == 1
        assert "another SkillSinc run is in progress" in records[0].errors[0]

    def test_profiles_record_the_active_set(self, sandbox: Sandbox) -> None:
        """With profiles, the record names the ones that applied."""
        Builder.write_config(
            sandbox.config_path, sandbox.library, (("target", "generic", sandbox.root),)
        )
        text = sandbox.config_path.read_text(encoding="utf-8")
        data = json.loads(Jsonc.blank_trailing_commas(Jsonc.blank_comments(text)))
        data["profiles"] = {"work": {"default": True}, "home": {"default": False}}
        data["routes"][0]["profiles"] = ["work"]
        sandbox.config_path.write_text(json.dumps(data), encoding="utf-8")

        status, output = sandbox.run("sync")

        assert status == 0, output
        assert _history(sandbox.environment)[0].profiles == ("work",)

    def test_history_size_follows_settings(self, sandbox: Sandbox) -> None:
        """``history.keep`` in settings.json bounds the file."""
        sandbox.configure()
        SettingsStore.default(sandbox.environment).save(
            UserSettings(history=HistoryOptions(keep=10))
        )

        for _ in range(21):
            sandbox.run("sync")

        path = sandbox.environment.state_directory / HISTORY_FILE
        assert len(path.read_text(encoding="utf-8").splitlines()) <= 20

    def test_invalid_settings_do_not_stop_a_sync(
        self, sandbox: Sandbox, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A broken settings.json is reported and the sync goes ahead."""
        sandbox.configure()
        store = SettingsStore.default(sandbox.environment)
        store.path.write_text('{"history": {"keep": 1}}', encoding="utf-8")

        status, _ = sandbox.run("sync")

        assert status == 0
        assert len(_history(sandbox.environment)) == 1
        assert "keeping 500 history records" in capsys.readouterr().err

    def test_scheduled_job_marks_its_runs(self, environment: Environment) -> None:
        """``schedule install`` registers ``sync --trigger scheduled``."""
        out = io.StringIO()
        status = Application(environment, out=out).run(
            ["schedule", "install", "--scheduler", "cron", "--dry-run"]
        )

        assert status == 0
        assert "sync --trigger scheduled" in out.getvalue(), out.getvalue()


class TestHistoryCommand:
    """``skillsinc history`` lists recent runs of one configuration."""

    def test_empty_history_says_so(self, sandbox: Sandbox) -> None:
        """Before any sync there is nothing to list."""
        sandbox.configure()

        status, output = sandbox.run("history")

        assert (status, output) == (0, "No syncs recorded yet.\n")

    def test_text_lists_runs_newest_first(self, sandbox: Sandbox) -> None:
        """Each row shows when, trigger, result, duration, profiles, and summary."""
        sandbox.configure()
        sandbox.run("sync", "--trigger", "tray-scheduled")
        sandbox.run("sync")

        status, output = sandbox.run("history")

        lines = output.splitlines()
        assert status == 0
        assert lines[0].split() == [
            "STARTED",
            "TRIGGER",
            "RESULT",
            "TIME",
            "PROFILES",
            "SUMMARY",
        ]
        assert "manual" in lines[1], f"Expected newest first: {output}"
        assert "no changes" in lines[1], output
        assert "tray-scheduled" in lines[2], output
        assert "create" in lines[2], output

    def test_json_and_limit(self, sandbox: Sandbox) -> None:
        """``--json -n 1`` returns the newest run as a versioned report."""
        sandbox.configure()
        sandbox.run("sync")
        sandbox.run("sync", "--trigger", "tray")

        status, output = sandbox.run("history", "--json", "-n", "1")

        data = json.loads(output)
        assert status == 0
        assert data["version"] == 1
        assert [run["trigger"] for run in data["runs"]] == ["tray"]

    def test_failed_run_shows_failed(self, sandbox: Sandbox) -> None:
        """A failure is marked and summarized by its error."""
        sandbox.config_path.parent.mkdir(parents=True)
        sandbox.config_path.write_text("{ not json", encoding="utf-8")
        sandbox.run("sync")

        status, output = sandbox.run("history")

        assert status == 0
        assert "FAILED" in output, output

    def test_other_configurations_are_left_out(self, sandbox: Sandbox) -> None:
        """Runs of another configuration are not listed."""
        sandbox.configure()
        sandbox.run("sync")
        other = sandbox.environment.home / "other.json"
        Builder.write_config(
            other, sandbox.library, (("o", "generic", sandbox.root / "o"),)
        )
        sandbox.run("sync", "--config", str(other))

        _, output = sandbox.run("history", "--json")

        configs = {run["config"] for run in json.loads(output)["runs"]}
        assert configs == {str(sandbox.config_path)}, f"Got {configs}"

    def test_project_runs_are_read_from_the_project(self, sandbox: Sandbox) -> None:
        """A project's syncs live in its own state directory and are listed."""
        project = sandbox.environment.home / "work"
        config = project / ".config" / "skillsinc" / "config.json"
        Builder.write_config(
            config, sandbox.library, (("t", "generic", project / "t"),)
        )
        text = config.read_text(encoding="utf-8")
        config.write_text(
            text.replace('"version": 1,', '"version": 1, "project": "../..",', 1),
            encoding="utf-8",
        )
        assert sandbox.run("sync", "--config", str(config))[0] == 0

        _, output = sandbox.run("history", "--json", "--config", str(config))

        runs = json.loads(output)["runs"]
        assert [run["ok"] for run in runs] == [True], output
        assert (project / ".config" / "skillsinc" / "state" / HISTORY_FILE).is_file()

    @pytest.mark.parametrize("limit", ["0", "-1", "lots"])
    def test_limit_must_be_positive(self, sandbox: Sandbox, limit: str) -> None:
        """A non-positive or non-numeric ``--limit`` is a usage error."""
        with pytest.raises(SystemExit) as raised:
            sandbox.run("history", "--limit", limit)

        assert raised.value.code == 2
