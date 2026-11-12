"""Project configurations: discovery, project folders, and per-project state."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from tests.conftest import Builder, Sandbox

from skillsinc.config import ConfigLoader
from skillsinc.exceptions import ConfigurationError
from skillsinc.manifest import Manifest, ManifestStore
from skillsinc.models import Kind
from skillsinc.paths import PROJECT_STATE, Environment
from skillsinc.profiles.claude import ClaudeProfile
from skillsinc.profiles.codex import CodexProfile
from skillsinc.profiles.copilot import CopilotProfile, VSCodeProfile
from skillsinc.profiles.registry import ProfileRegistry
from skillsinc.profiles.shared import AgentsProfile, GenericProfile

pytestmark = pytest.mark.integration

SERVER = '{"command": "npx", "args": ["server"]}\n'


def write_project_config(
    project: Path,
    destinations: dict[str, dict[str, str]],
    *,
    source: str,
    extra: dict[str, object] | None = None,
) -> Path:
    """Write ``.config/skillsinc/config.json`` routing ``source`` everywhere."""
    data: dict[str, object] = {
        "version": 1,
        "sources": {"library": {"path": source}},
        "destinations": destinations,
        "routes": [
            {
                "sources": ["library"],
                "destinations": list(destinations),
                "allow_lossy": True,
            }
        ],
        **(extra or {}),
    }
    path = project / ".config" / "skillsinc" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("// project\n" + json.dumps(data, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def project(environment: Environment) -> Path:
    """Provide an empty project directory below the fake home."""
    root = environment.home / "src" / "app"
    (root / "pkg").mkdir(parents=True)
    return root


def inside(sandbox: Sandbox, directory: Path) -> Sandbox:
    """Return the sandbox driven from ``directory``."""
    return Sandbox(replace(sandbox.environment, cwd=directory), sandbox.library)


class TestDiscovery:
    """Plan and sync find the nearest project configuration."""

    def test_finds_the_nearest_configuration_above_the_working_directory(
        self, environment: Environment, project: Path
    ) -> None:
        """A subdirectory of the project uses the project's file."""
        config = write_project_config(project, {}, source="lib")
        nested = replace(environment, cwd=project / "pkg")

        result = nested.config_file()

        assert result == config, f"Expected {config}, got {result}"

    def test_reads_an_early_jsonc_name(
        self, environment: Environment, project: Path
    ) -> None:
        """``config.jsonc`` is found when ``config.json`` is absent."""
        path = project / ".config" / "skillsinc" / "config.jsonc"
        path.parent.mkdir(parents=True)
        path.write_text("{}", encoding="utf-8")

        result = replace(environment, cwd=project).config_file()

        assert result == path, f"Expected {path}, got {result}"

    def test_falls_back_to_the_user_configuration(
        self, environment: Environment, project: Path
    ) -> None:
        """Outside any project, the user configuration is used."""
        result = replace(environment, cwd=project).config_file()

        assert result == environment.default_config_file, f"Got {result}"

    def test_user_flag_skips_the_project(
        self, environment: Environment, project: Path
    ) -> None:
        """``user=True`` always returns the user configuration."""
        write_project_config(project, {}, source="lib")

        result = replace(environment, cwd=project).config_file(user=True)

        assert result == environment.default_config_file, f"Got {result}"

    def test_home_is_not_a_project(self, environment: Environment) -> None:
        """``~/.config/skillsinc`` is the user's folder even with XDG moved."""
        home_config = environment.home / ".config" / "skillsinc" / "config.json"
        home_config.parent.mkdir(parents=True)
        home_config.write_text("{}", encoding="utf-8")
        nested = environment.home / "src"
        nested.mkdir()

        result = replace(environment, cwd=nested).project_config()

        assert result is None, f"Expected no project above the home, got {result}"

    def test_xdg_config_home_inside_a_project_is_still_the_user_file(
        self, tmp_path: Path
    ) -> None:
        """The user configuration is never mistaken for a project's."""
        project = tmp_path / "work"
        user = project / ".config" / "skillsinc" / "config.json"
        user.parent.mkdir(parents=True)
        user.write_text("{}", encoding="utf-8")
        environment = Environment(
            tmp_path / "home",
            {"XDG_CONFIG_HOME": str(project / ".config")},
            "linux",
            project,
        )

        assert environment.project_config() is None, "Expected the user file"
        assert environment.is_user_config(user), "Expected the user file detected"


class TestProjectScope:
    """A project configuration resolves paths and defaults inside the project."""

    def test_destinations_default_to_project_folders(
        self, environment: Environment, project: Path
    ) -> None:
        """Every profile with a project folder defaults to it."""
        names = ("claude", "copilot", "vscode", "codex", "agents")
        path = write_project_config(
            project, {name: {} for name in names}, source="~/lib"
        )

        config = ConfigLoader(ProfileRegistry(), environment).load(path)

        folders = {name: config.destinations[name].path for name in names}
        assert folders == {
            "claude": project / ".claude",
            "copilot": project / ".github",
            "vscode": project / ".vscode",
            "codex": project / ".codex",
            "agents": project / ".agents",
        }, f"Expected project folders, got {folders}"
        assert config.project == project, f"Expected project root, got {config}"

    def test_relative_paths_resolve_against_the_project_root(
        self, environment: Environment, project: Path
    ) -> None:
        """``path`` values are relative to the project, not the config folder."""
        path = write_project_config(
            project,
            {"out": {"profile": "generic", "path": "build/agents"}},
            source="agent-library",
        )

        config = ConfigLoader(ProfileRegistry(), environment).load(path)

        assert config.sources["library"].path == project / "agent-library"
        assert config.destinations["out"].path == project / "build/agents"

    def test_state_is_kept_in_the_project(
        self, environment: Environment, project: Path
    ) -> None:
        """Manifests and the lock live under ``.config/skillsinc/state``."""
        path = write_project_config(project, {"claude": {}}, source="lib")

        config = ConfigLoader(ProfileRegistry(), environment).load(path)

        assert config.state_directory(environment) == project / PROJECT_STATE

    def test_user_configuration_is_not_a_project(
        self, environment: Environment
    ) -> None:
        """The user file keeps user defaults and the XDG state directory."""
        path = environment.default_config_file
        path.parent.mkdir(parents=True)
        path.write_text(
            '{"version": 1, "sources": {}, "destinations": {"claude": {}}}',
            encoding="utf-8",
        )

        config = ConfigLoader(ProfileRegistry(), environment).load(path)

        assert config.project is None, "Expected user scope"
        assert config.destinations["claude"].path == environment.home / ".claude"
        assert config.state_directory(environment) == environment.state_directory

    def test_explicit_project_key_names_the_root(
        self, environment: Environment, project: Path
    ) -> None:
        """``project`` makes any file a project configuration."""
        path = project / "tools" / "skillsinc.json"
        path.parent.mkdir()
        path.write_text(
            '{"version": 1, "project": "..", "sources": {},'
            ' "destinations": {"claude": {}}}',
            encoding="utf-8",
        )

        config = ConfigLoader(ProfileRegistry(), environment).load(path)

        assert config.project == project, f"Got {config.project}"
        assert config.destinations["claude"].path == project / ".claude"

    @pytest.mark.parametrize("value", ["", "missing"])
    def test_project_key_must_name_a_directory(
        self, environment: Environment, tmp_path: Path, value: str
    ) -> None:
        """An empty or missing project root is a configuration error."""
        path = tmp_path / "config.json"
        path.write_text(
            json.dumps({"version": 1, "project": value, "sources": {}}),
            encoding="utf-8",
        )

        with pytest.raises(ConfigurationError, match="project"):
            ConfigLoader(ProfileRegistry(), environment).load(path)

    def test_generic_destination_needs_a_path_in_a_project(
        self, environment: Environment, project: Path
    ) -> None:
        """Profiles without a project folder say so."""
        path = write_project_config(
            project, {"out": {"profile": "generic"}}, source="lib"
        )

        with pytest.raises(ConfigurationError, match="in a project"):
            ConfigLoader(ProfileRegistry(), environment).load(path)


class TestProjectProfiles:
    """Profiles write the files each harness reads in a repository."""

    def test_claude_servers_go_to_the_project_mcp_file(self, tmp_path: Path) -> None:
        """Claude Code reads project servers from ``.mcp.json``."""
        profile = ClaudeProfile(tmp_path)
        environment = Environment(tmp_path, {}, "linux")

        result = profile.locate(".claude.json", tmp_path / ".claude", environment)

        assert result == "../.mcp.json", f"Got {result}"
        assert result in profile.outside, "Expected the file allowed beside the root"

    def test_codex_instructions_go_to_the_root_agents_file(
        self, tmp_path: Path
    ) -> None:
        """Codex reads a project's ``AGENTS.md`` at its root."""
        item = Builder.item(Kind.INSTRUCTION, name="rules", frontmatter={})

        rendering = CodexProfile(tmp_path).render(item)

        paths = [block.path for block in rendering.blocks]
        assert paths == ["../AGENTS.md"], f"Got {paths}"
        assert "../AGENTS.md" in CodexProfile.outside, "Expected it allowed"

    def test_codex_user_instructions_stay_in_codex_home(self) -> None:
        """At the user level the block stays in ``~/.codex/AGENTS.md``."""
        item = Builder.item(Kind.INSTRUCTION, name="rules", frontmatter={})

        rendering = CodexProfile().render(item)

        assert [block.path for block in rendering.blocks] == ["AGENTS.md"]

    def test_copilot_writes_prompts_and_repository_servers(
        self, tmp_path: Path
    ) -> None:
        """``.github/prompts`` and ``.github/mcp.json`` hold project content."""
        profile = CopilotProfile(tmp_path)
        prompt = Builder.item(Kind.PROMPT, name="ask")
        server = Builder.item(
            Kind.MCP, name="srv", frontmatter={"command": "npx"}, body=""
        )

        prompts = [file.path for file in profile.render(prompt).files]
        servers = [entry.path for entry in profile.render(server).entries]

        assert prompts == ["prompts/ask.prompt.md"], f"Got {prompts}"
        assert servers == ["mcp.json"], f"Got {servers}"

    def test_copilot_user_level_still_refuses_prompts(self) -> None:
        """Copilot CLI has no user prompts folder."""
        rendering = CopilotProfile().render(Builder.item(Kind.PROMPT, name="ask"))

        assert rendering.unsupported is not None, "Expected a refusal"

    def test_vscode_project_holds_only_servers(self, tmp_path: Path) -> None:
        """``.vscode/mcp.json`` gets servers; the rest is read from ``.github``."""
        profile = VSCodeProfile(tmp_path)
        server = Builder.item(
            Kind.MCP, name="srv", frontmatter={"command": "npx"}, body=""
        )

        entries = profile.render(server).entries
        refused = profile.render(Builder.item(Kind.INSTRUCTION, name="rules"))

        assert [(entry.path, entry.container) for entry in entries] == [
            ("mcp.json", "servers")
        ]
        assert refused.unsupported is not None
        assert ".github" in refused.unsupported, f"Got {refused.unsupported}"

    def test_scoped_profiles_switch_coverage(self, tmp_path: Path) -> None:
        """Copilot defers skills to ``.claude`` only inside a project."""
        user = CopilotProfile()
        scoped = user.scoped(tmp_path)

        assert "claude" not in user.covering(), "Expected user coverage unchanged"
        assert "claude" in scoped.covering(), "Expected project coverage"
        assert "copilot" in VSCodeProfile(tmp_path).reads()
        assert VSCodeProfile(tmp_path).covering() == {}

    def test_profiles_without_project_folders(self, tmp_path: Path) -> None:
        """A generic profile has no project default; agents uses ``.agents``."""
        environment = Environment(tmp_path, {}, "linux")

        assert GenericProfile(tmp_path).default_path(environment) is None
        assert AgentsProfile(tmp_path).default_path(environment) == (
            tmp_path / ".agents"
        )


class TestRelocatableState:
    """Project manifests record paths relative to the project."""

    def test_round_trip_records_relative_paths(self, tmp_path: Path) -> None:
        """Saved manifests are relative; loaded ones absolute again."""
        project = tmp_path / "app"
        config = project / ".config" / "skillsinc" / "config.json"
        store = ManifestStore(project / PROJECT_STATE, project)
        manifest = Manifest(
            "claude",
            str(project / ".claude"),
            files={"skills/a/SKILL.md": "digest"},
            config=str(config.resolve()),
        )

        store.save(manifest)
        saved = json.loads(store.path("claude").read_text(encoding="utf-8"))
        loaded = store.load("claude", project / ".claude")

        assert (saved["root"], saved["config"]) == (
            ".claude",
            ".config/skillsinc/config.json",
        ), f"Got {saved}"
        assert loaded.root == str(project / ".claude"), f"Got {loaded.root}"
        assert loaded.config == str(config.resolve()), f"Got {loaded.config}"

    def test_journal_header_is_relative(self, tmp_path: Path) -> None:
        """An interrupted run's journal replays against the moved project."""
        old, new = tmp_path / "old", tmp_path / "new"
        store = ManifestStore(old / PROJECT_STATE, old)
        manifest = Manifest("claude", str(old / ".claude"), config="")
        with store.journal(manifest):
            pass
        shutil.move(old, new)

        loaded = ManifestStore(new / PROJECT_STATE, new).load("claude", new / ".claude")

        assert loaded.root == str(new / ".claude"), f"Got {loaded.root}"

    def test_paths_outside_the_project_stay_absolute(self, tmp_path: Path) -> None:
        """A destination outside the project is recorded as configured."""
        store = ManifestStore(tmp_path / "state", tmp_path / "app")
        outside = tmp_path / "elsewhere"

        store.save(Manifest("out", str(outside)))
        saved = json.loads(store.path("out").read_text(encoding="utf-8"))

        assert saved["root"] == str(outside), f"Got {saved}"


class TestProjectJourney:
    """``init --project``, then plan and sync from anywhere in the project."""

    def setup_project(self, sandbox: Sandbox, project: Path) -> Sandbox:
        """Write a project configuration routing the library to Claude Code."""
        (sandbox.library / "mcp").mkdir()
        (sandbox.library / "mcp" / "srv.json").write_text(SERVER, encoding="utf-8")
        write_project_config(
            project,
            {"claude": {}, "agents": {}},
            source=sandbox.library.as_posix(),
        )
        return inside(sandbox, project / "pkg")

    def test_sync_writes_into_the_project_with_isolated_state(
        self, sandbox: Sandbox, project: Path
    ) -> None:
        """Files land in the project; manifests stay out of the user state."""
        driver = self.setup_project(sandbox, project)

        status, output = driver.run("sync")

        assert status == 0, f"Expected sync to succeed, got {status}: {output}"
        assert output.startswith(f"Project: {project}\n"), f"Got {output}"
        assert (project / ".claude/skills/sample/SKILL.md").is_file()
        assert (project / ".agents/skills/sample/SKILL.md").is_file()
        servers = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))
        assert "srv" in servers["mcpServers"], f"Got {servers}"
        state = project / PROJECT_STATE
        assert (state / "manifests" / "claude.json").is_file()
        assert (state / ".gitignore").read_text(encoding="utf-8").endswith("*\n")
        assert not (sandbox.environment.state_directory / "manifests").exists(), (
            "Expected the user state untouched"
        )
        assert not (sandbox.environment.home / ".claude").exists(), (
            "Expected nothing written to the user's Claude folder"
        )

    def test_project_and_user_destinations_share_names_safely(
        self, sandbox: Sandbox, project: Path
    ) -> None:
        """A user ``claude`` and a project ``claude`` keep separate manifests."""
        user_claude = sandbox.environment.home / ".claude"
        Builder.write_config(
            sandbox.config_path, sandbox.library, (("claude", "claude", user_claude),)
        )
        driver = self.setup_project(sandbox, project)

        user_status, user_output = driver.run("sync", "--user")
        project_status, project_output = driver.run("sync")
        replan_status, replan = driver.run("plan", "--user")

        assert user_status == 0, f"Expected user sync to succeed: {user_output}"
        assert project_status == 0, f"Expected project sync: {project_output}"
        assert replan_status == 0, f"Expected clean user plan: {replan}"
        assert "create" not in replan, f"Got {replan}"
        assert "delete" not in replan, f"Got {replan}"
        assert (user_claude / "skills/sample/SKILL.md").is_file()
        assert (project / ".claude/skills/sample/SKILL.md").is_file()

    def test_moved_project_keeps_its_state(
        self, sandbox: Sandbox, project: Path
    ) -> None:
        """Renaming the project folder leaves nothing to redo."""
        assert self.setup_project(sandbox, project).run("sync")[0] == 0
        moved = project.with_name("renamed")
        shutil.move(project, moved)

        status, output = inside(sandbox, moved).run("plan", "--json")

        report = json.loads(output)
        changes = [d["changes"] for d in report["destinations"]]
        assert status == 0, f"Expected a clean plan, got {status}: {output}"
        assert report["project"] == str(moved), f"Got {report['project']}"
        assert all(not change for change in changes), f"Got {changes}"

    def test_removed_project_destination_is_destroyed_in_the_project_only(
        self, sandbox: Sandbox, project: Path
    ) -> None:
        """Retiring a project destination never looks at user manifests."""
        driver = self.setup_project(sandbox, project)
        assert driver.run("sync")[0] == 0
        write_project_config(project, {"claude": {}}, source=sandbox.library.as_posix())

        status, output = driver.run("sync")

        assert status == 0, f"Expected sync to succeed, got {status}: {output}"
        assert "agents (removed from configuration)" in output, f"Got {output}"
        assert not (project / ".agents/skills/sample").exists()

    def test_config_and_user_flags_conflict(self, sandbox: Sandbox) -> None:
        """``--config`` and ``--user`` cannot both choose the file."""
        with pytest.raises(SystemExit):
            sandbox.run("plan", "--user", "--config", "x.json")

    def test_init_project_writes_the_conventional_file(
        self, sandbox: Sandbox, project: Path
    ) -> None:
        """``init --project`` targets the working directory and loads as a project."""
        (sandbox.environment.home / ".claude").mkdir()
        driver = inside(sandbox, project)

        status, output = driver.run(
            "init", "--project", "--source", str(sandbox.library)
        )

        path = project / ".config" / "skillsinc" / "config.json"
        assert status == 0, f"Expected init to succeed, got {status}: {output}"
        assert path.is_file(), f"Expected {path}"
        assert "--user" in output, f"Expected the user hint, got {output}"
        text = path.read_text(encoding="utf-8")
        assert '"claude": { "profile": "claude" },  // .claude/' in text, text
        assert '"generic"' not in text, "Expected no generic destination"
        config = ConfigLoader(ProfileRegistry(), driver.environment).load(path)
        assert config.destinations["claude"].path == project / ".claude"
        plan_status, plan = driver.run("plan")
        assert plan_status == 0, f"Expected the starter to plan, got {plan}"
        assert f"Project: {project}" in plan, f"Got {plan}"

    def test_init_project_with_custom_path_names_the_root(
        self, sandbox: Sandbox, project: Path
    ) -> None:
        """A starter outside ``.config/skillsinc`` records ``project``."""
        driver = inside(sandbox, project)
        path = project / "tools" / "skillsinc.json"

        status, output = driver.run(
            "init", "--project", "--config", str(path), "--source", str(sandbox.library)
        )

        assert status == 0, f"Expected init to succeed, got {status}: {output}"
        assert '"project": "..",' in path.read_text(encoding="utf-8")
        config = ConfigLoader(ProfileRegistry(), driver.environment).load(path)
        assert config.project == project, f"Expected project scope, got {config}"

    def test_init_project_routes_vscode_users_to_github(
        self, sandbox: Sandbox, project: Path
    ) -> None:
        """VS Code reads project instructions from ``.github``, so copilot is on."""
        vscode = VSCodeProfile().user_path(sandbox.environment)
        vscode.mkdir(parents=True)
        driver = inside(sandbox, project)

        status, output = driver.run(
            "init", "--project", "--source", str(sandbox.library)
        )

        text = (project / ".config/skillsinc/config.json").read_text(encoding="utf-8")
        assert status == 0, f"Expected init to succeed, got {status}: {output}"
        assert '    "copilot": { "profile": "copilot" },' in text, text
        assert '"copilot"]' in text, f"Expected copilot routed, got {text}"
