"""Run ``skillsinc`` as a separate process the way users and schedulers do."""

from __future__ import annotations

import json
import shutil
from importlib.metadata import version
from pathlib import Path

from tests.e2e.conftest import Cli, Harnesses

from skillsinc.jsonc import Jsonc


def _files(root: Path) -> list[str]:
    """Return every file under ``root`` as a sorted relative POSIX path."""
    return sorted(
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    )


class TestEntryPoint:
    """Process-level behavior that needs no configuration."""

    def test_version_matches_package_metadata(self, cli: Cli) -> None:
        """``--version`` prints the installed distribution's version."""
        result = cli.run("--version")

        expected = f"skillsinc {version('skillsinc')}"
        assert (result.status, result.stdout.strip()) == (0, expected), (
            f"Expected exit 0 and {expected!r}, got {result.status}: {result!r}"
        )

    def test_profiles_lists_every_harness(self, cli: Cli) -> None:
        """``profiles`` prints a row for each built-in harness."""
        result = cli.run("profiles")

        names = {line.split()[0] for line in result.stdout.splitlines()[1:] if line}
        expected = {"claude", "copilot", "vscode", "codex", "agents", "generic"}
        assert (result.status, names) == (0, expected), (
            f"Expected exit 0 and {expected!r}, got {result.status}: {names!r}"
        )

    def test_unknown_command_is_a_usage_error(self, cli: Cli) -> None:
        """An unknown command exits 2 with usage on stderr and nothing on stdout."""
        result = cli.run("frobnicate")

        assert (result.status, result.stdout) == (2, ""), (
            f"Expected exit 2 with empty stdout, got {result.status}: {result!r}"
        )
        assert "usage:" in result.stderr, f"Expected usage text, got {result.stderr!r}"

    def test_plan_without_configuration_is_a_configuration_error(
        self, cli: Cli
    ) -> None:
        """``plan`` before ``init`` exits 2, names the missing file, writes nothing."""
        result = cli.run("plan")

        assert result.status == 2, f"Expected exit 2, got {result.status}: {result!r}"
        assert "config.json" in result.stderr, (
            f"Expected the missing file named on stderr, got {result.stderr!r}"
        )
        assert _files(cli.home) == [], (
            f"Expected no files written, got {_files(cli.home)!r}"
        )


class TestFirstRun:
    """The quick start: init, plan, sync, and convergence on every harness."""

    def test_init_detects_every_installed_harness(
        self, cli: Cli, library: Path
    ) -> None:
        """``init`` routes the library to each harness whose folder exists."""
        Harnesses.install(cli.home)

        result = cli.run("init", "--source", str(library))

        text = cli.config_file.read_text(encoding="utf-8")
        config = json.loads(Jsonc.blank_trailing_commas(Jsonc.blank_comments(text)))
        routes = config["routes"][0]["destinations"]
        expected = ["claude", "copilot", "vscode", "codex", "agents"]
        assert (result.status, routes) == (0, expected), (
            f"Expected exit 0 routing to {expected!r}, got {result.status}: {routes!r}"
        )

    def test_plan_previews_without_writing(self, cli: Cli, library: Path) -> None:
        """``plan`` lists creates for every harness and leaves them empty."""
        Harnesses.install(cli.home)
        assert cli.run("init", "--source", str(library)).status == 0

        result = cli.run("plan")

        assert result.status == 0, f"Expected exit 0, got {result.status}: {result!r}"
        assert "create" in result.stdout, f"Expected creates, got {result.stdout!r}"
        written = [
            name
            for name in (".claude", ".copilot", ".codex", ".agents")
            if any((cli.home / name).iterdir())
        ]
        assert written == [], f"Expected no harness written, got {written!r}"

    def test_sync_writes_each_harness_then_converges(
        self, cli: Cli, library: Path
    ) -> None:
        """``sync`` writes every harness's layout; the next ``plan`` is empty."""
        Harnesses.install(cli.home)
        assert cli.run("init", "--source", str(library)).status == 0

        sync = cli.run("sync")
        after = cli.run("plan", "--json")

        assert sync.status == 0, f"Expected exit 0, got {sync.status}: {sync!r}"
        expected = {
            ".claude": [
                "agents/reviewer.md",
                "commands/explain.md",
                "rules/python.md",
                "skills/review/SKILL.md",
                "skills/review/references/checklist.md",
            ],
            ".copilot": [
                "agents/reviewer.agent.md",
                "instructions/python.instructions.md",
                "mcp-config.json",
            ],
            ".agents": [
                "skills/review/SKILL.md",
                "skills/review/references/checklist.md",
            ],
        }
        # Copilot CLI reads skills from ~/.agents, so it gets no copy of its own
        actual = {name: _files(cli.home / name) for name in expected}
        assert actual == expected, f"Expected {expected!r}, got {actual!r}"
        claude_json = json.loads((cli.home / ".claude.json").read_text("utf-8"))
        assert claude_json["mcpServers"]["github"]["command"] == "gh-mcp", (
            f"Expected the server in ~/.claude.json, got {claude_json!r}"
        )
        report = after.json()
        changes = [d["changes"] for d in report["destinations"] if d["changes"]]
        assert (after.status, report["ok"], changes) == (0, True, []), (
            f"Expected a converged plan, got {after.status}: {report!r}"
        )

    def test_intent_files_are_never_distributed(self, cli: Cli, library: Path) -> None:
        """``INTENT.md`` in the library reaches no harness."""
        Harnesses.install(cli.home)
        assert cli.run("init", "--source", str(library)).status == 0

        assert cli.run("sync").status == 0

        leaked = [path for path in _files(cli.home) if path.endswith("INTENT.md")]
        assert leaked == [], f"Expected no INTENT.md copies, got {leaked!r}"


class TestLibraryChanges:
    """Later syncs follow edits and removals in the library."""

    def test_edit_then_removal_reaches_every_harness(
        self, cli: Cli, library: Path
    ) -> None:
        """An edited skill is rewritten; a removed agent is deleted everywhere."""
        Harnesses.install(cli.home)
        assert cli.run("init", "--source", str(library)).status == 0
        assert cli.run("sync").status == 0
        skill = library / "skills/review/SKILL.md"
        skill.write_text(
            "---\nname: review\ndescription: Review a change\n---\nUpdated.\n",
            encoding="utf-8",
            newline="\n",
        )
        (library / "agents/reviewer.agent.md").unlink()

        result = cli.run("sync")

        body = (cli.home / ".claude/skills/review/SKILL.md").read_text("utf-8")
        assert (result.status, body.endswith("Updated.\n")) == (0, True), (
            f"Expected the edit synced, got {result.status}: {body!r}"
        )
        remaining = [
            path
            for path in (
                cli.home / ".claude/agents/reviewer.md",
                cli.home / ".copilot/agents/reviewer.agent.md",
                cli.home / ".codex/agents/reviewer.toml",
            )
            if path.exists()
        ]
        assert remaining == [], f"Expected agent copies deleted, got {remaining!r}"

    def test_unreadable_library_deletes_nothing(self, cli: Cli, library: Path) -> None:
        """A missing library fails the run and leaves every synced file in place."""
        Harnesses.install(cli.home)
        assert cli.run("init", "--source", str(library)).status == 0
        assert cli.run("sync").status == 0
        before = _files(cli.home / ".claude")
        shutil.rmtree(library)

        result = cli.run("sync")

        assert result.status == 1, f"Expected exit 1, got {result.status}: {result!r}"
        after = _files(cli.home / ".claude")
        assert after == before, f"Expected {before!r} untouched, got {after!r}"


class TestSafety:
    """Files SkillSinc does not own are never overwritten silently."""

    def test_unmanaged_file_blocks_until_adopted(self, cli: Cli, library: Path) -> None:
        """A user's file at a target path blocks ``sync``; ``--adopt`` takes it."""
        Harnesses.install(cli.home)
        assert cli.run("init", "--source", str(library)).status == 0
        mine = cli.home / ".claude/agents/reviewer.md"
        mine.parent.mkdir(parents=True)
        mine.write_text("my own agent\n", encoding="utf-8")

        blocked = cli.run("sync")
        kept = mine.read_text(encoding="utf-8")
        adopted = cli.run("sync", "--adopt")

        assert (blocked.status, kept) == (1, "my own agent\n"), (
            f"Expected exit 1 with the file kept, got {blocked.status}: {kept!r}"
        )
        assert adopted.status == 0, (
            f"Expected --adopt to succeed, got {adopted.status}: {adopted!r}"
        )
        assert "Review carefully." in mine.read_text(encoding="utf-8"), (
            "Expected the adopted file replaced by the library's agent"
        )

    def test_user_settings_survive_a_shared_config_edit(
        self, cli: Cli, library: Path
    ) -> None:
        """Keys a user wrote in ``~/.claude.json`` survive adding a server."""
        Harnesses.install(cli.home)
        assert cli.run("init", "--source", str(library)).status == 0
        shared = cli.home / ".claude.json"
        shared.write_text(
            json.dumps({"theme": "dark", "mcpServers": {"mine": {"command": "x"}}}),
            encoding="utf-8",
        )

        result = cli.run("sync")

        data = json.loads(shared.read_text(encoding="utf-8"))
        assert result.status == 0, f"Expected exit 0, got {result.status}: {result!r}"
        assert data["theme"] == "dark", f"Expected user key kept, got {data!r}"
        assert set(data["mcpServers"]) == {"mine", "github"}, (
            f"Expected both servers, got {data['mcpServers']!r}"
        )

    def test_held_lock_refuses_a_second_run(self, cli: Cli, library: Path) -> None:
        """A live lock file makes ``sync`` exit 1 without writing."""
        Harnesses.install(cli.home)
        assert cli.run("init", "--source", str(library)).status == 0
        cli.state_directory.mkdir(parents=True)
        (cli.state_directory / "sync.lock").write_text("1\n", encoding="utf-8")

        result = cli.run("sync")

        assert result.status == 1, f"Expected exit 1, got {result.status}: {result!r}"
        assert "in progress" in result.stderr, (
            f"Expected the lock named on stderr, got {result.stderr!r}"
        )
        assert _files(cli.home / ".claude") == [], "Expected no writes under a lock"


class TestScheduling:
    """What a scheduler runs, and what ``schedule`` would register."""

    def test_scheduled_sync_writes_only_the_log(self, cli: Cli, library: Path) -> None:
        """``sync --log-file`` prints nothing and appends the report to the log."""
        Harnesses.install(cli.home)
        cli.run("init", "--source", str(library))
        log = cli.state_directory / "sync.log"

        result = cli.run("sync", "--log-file", str(log))

        assert (result.status, result.stdout, result.stderr) == (0, "", ""), (
            f"Expected a silent run, got {result!r}"
        )
        text = log.read_text(encoding="utf-8")
        assert "skillsinc sync" in text, f"Expected a run header in {text}"
        assert "claude" in text, f"Expected the report in {text}"

    def test_dry_run_shows_the_launchd_agent(self, cli: Cli) -> None:
        """``schedule install --dry-run`` prints the agent and registers nothing."""
        result = cli.run(
            "schedule",
            "install",
            "--scheduler",
            "launchd",
            "--interval",
            "5m",
            "--dry-run",
        )

        assert result.status == 0, f"Expected exit 0, got {result!r}"
        assert "<integer>300</integer>" in result.stdout, (
            f"Expected a 300-second interval, got {result.stdout}"
        )
        agents = cli.home / "Library" / "LaunchAgents"
        assert not agents.exists(), f"Expected nothing written to {agents}"

    def test_invalid_interval_is_a_usage_error(self, cli: Cli) -> None:
        """An interval below one minute exits 2 before touching any scheduler."""
        result = cli.run("schedule", "install", "--interval", "10s")

        assert result.status == 2, f"Expected exit 2, got {result!r}"
        assert "from 1m to 31d" in result.stderr, f"Expected the range: {result!r}"
