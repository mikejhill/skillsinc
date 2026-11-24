"""Test Copilot and VS Code native paths and loss boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.conftest import Builder

from skillsinc.frontmatter import FrontmatterCodec
from skillsinc.models import EntryOutput, Frontmatter, JsonValue, Kind, Rendering
from skillsinc.paths import Environment
from skillsinc.profiles.copilot import CopilotProfile, VSCodeProfile


class TestCopilotProfile:
    """Copilot copies native documents and refuses unsupported prompts."""

    @pytest.mark.parametrize(
        ("kind", "path"),
        [
            (Kind.SKILL, "skills/sample/SKILL.md"),
            (Kind.INSTRUCTION, "instructions/sample.instructions.md"),
            (Kind.AGENT, "agents/sample.agent.md"),
            (Kind.RESOURCE, "resources/sample.md"),
        ],
    )
    def test_native_files_are_copied_verbatim(self, kind: Kind, path: str) -> None:
        """Retain authored bytes in Copilot's discovery layout."""
        item = Builder.item(kind, frontmatter={"applyTo": "**"})

        result = CopilotProfile().render(item)

        assert (result.files[0].path, result.files[0].content, result.losses) == (
            path,
            item.document.raw,
            (),
        ), f"Expected exact copy at {path}, got {result!r}"

    def test_instruction_without_scope_is_lossy(self) -> None:
        """Report Copilot CLI's need for applyTo metadata."""
        result = CopilotProfile().render(Builder.item(Kind.INSTRUCTION, frontmatter={}))

        assert result.losses == (
            "has no applyTo, which Copilot CLI needs to apply the file",
        ), f"Expected applyTo loss, got {result!r}"

    def test_prompt_is_refused_with_vscode_hint(self) -> None:
        """Direct unsupported prompts to the VS Code profile."""
        result = CopilotProfile().render(Builder.item(Kind.PROMPT))

        assert result.unsupported is not None, f"Expected vscode hint, got {result!r}"
        assert "vscode" in result.unsupported, f"Expected vscode hint, got {result!r}"

    def test_mcp_server_becomes_mcp_config_entry(self) -> None:
        """Place the server definition under ``mcpServers`` in ``mcp-config.json``."""
        server: dict[str, JsonValue] = {
            "type": "local",
            "command": "npx",
            "args": ["server"],
        }
        item = Builder.item(Kind.MCP, name="github", frontmatter=server)

        result = CopilotProfile().render(item)

        assert result == Rendering(
            entries=(EntryOutput("mcp-config.json", "mcpServers", "github", server),)
        ), f"Expected one mcpServers entry, got {result!r}"

    def test_hook_file_is_copied_into_hooks(self) -> None:
        """Copy the authored hook file to ``hooks/<name>.json``."""
        item = Builder.item(Kind.HOOK, name="guard", frontmatter={"version": 1})

        result = CopilotProfile().render(item)

        assert [(f.path, f.content) for f in result.files] == [
            ("hooks/guard.json", item.document.raw)
        ], f"Expected verbatim hook copy, got {result!r}"

    def test_default_path_uses_fake_home(self, environment: Environment) -> None:
        """Default Copilot roots stay within the injected home."""
        result = CopilotProfile().default_path(environment)

        assert result == environment.home / ".copilot", (
            f"Expected fake Copilot root, got {result}"
        )


class TestVSCodeProfile:
    """VS Code uses flat suffixed Markdown files and platform-specific roots."""

    @pytest.mark.parametrize(
        ("kind", "suffix"),
        [
            (Kind.INSTRUCTION, "instructions"),
            (Kind.AGENT, "agent"),
            (Kind.PROMPT, "prompt"),
        ],
    )
    def test_native_documents_are_flat_verbatim_copies(
        self, kind: Kind, suffix: str
    ) -> None:
        """Copy every supported document without stripping authored metadata."""
        item = Builder.item(kind)

        result = VSCodeProfile().render(item)

        assert (result.files[0].path, result.files[0].content) == (
            f"sample.{suffix}.md",
            item.document.raw,
        ), f"Expected flat {suffix} file, got {result!r}"

    def test_skills_are_refused_with_shared_home_hint(self) -> None:
        """VS Code skills belong in Copilot or shared agents destinations."""
        result = VSCodeProfile().render(Builder.item())

        assert result.unsupported is not None, (
            f"Expected shared skill-home hint, got {result!r}"
        )
        assert "copilot or agents" in result.unsupported, (
            f"Expected shared skill-home hint, got {result!r}"
        )

    def test_mcp_server_becomes_user_mcp_json_entry(self) -> None:
        """Place servers under ``servers`` in ``User/mcp.json`` with a type."""
        server: dict[str, JsonValue] = {
            "type": "local",
            "command": "gh",
            "tools": ["*"],
        }
        item = Builder.item(Kind.MCP, name="github", frontmatter=server)

        result = VSCodeProfile().render(item)

        assert result == Rendering(
            entries=(
                EntryOutput(
                    "../mcp.json",
                    "servers",
                    "github",
                    {"type": "stdio", "command": "gh"},
                ),
            ),
            losses=("drops server fields: tools",),
        ), f"Expected one servers entry beside the root, got {result!r}"

    @pytest.mark.parametrize(
        ("platform", "prefix"),
        [
            ("win32", "AppData/Roaming"),
            ("darwin", "Library/Application Support"),
            ("linux", ".config"),
        ],
    )
    def test_platform_default_paths(
        self, tmp_path: Path, platform: str, prefix: str
    ) -> None:
        """Choose the native VS Code user prompts location on each platform."""
        environment = Environment(tmp_path, {}, platform)

        result = VSCodeProfile().default_path(environment)

        expected = tmp_path / prefix / "Code/User/prompts"
        assert result == expected, f"Expected {expected}, got {result}"

    def test_appdata_override_is_honored(self, tmp_path: Path) -> None:
        """Absolute APPDATA overrides the Windows roaming fallback."""
        environment = Environment(
            tmp_path, {"APPDATA": str(tmp_path / "roaming")}, "win32"
        )

        result = VSCodeProfile().default_path(environment)

        assert result == tmp_path / "roaming/Code/User/prompts", (
            f"Expected APPDATA root, got {result}"
        )


class TestPluginConversions:
    """Claude Code plugin agents and commands keep only fields both formats share."""

    @pytest.mark.parametrize(
        ("profile", "path"),
        [
            (CopilotProfile(), "agents/sample.agent.md"),
            (VSCodeProfile(), "sample.agent.md"),
        ],
    )
    def test_plugin_agent_keeps_name_and_description(
        self, profile: CopilotProfile | VSCodeProfile, path: str
    ) -> None:
        """Claude tool and model fields are reported, not copied."""
        header: Frontmatter = {"name": "sample", "description": "D", "tools": "Read"}
        item = Builder.item(Kind.AGENT, frontmatter=header, plugin="kit")

        result = profile.render(item)

        written = FrontmatterCodec().parse(result.files[0].content).frontmatter
        assert (result.files[0].path, written, result.losses) == (
            path,
            {"name": "sample", "description": "D"},
            ("drops frontmatter: tools",),
        ), f"Expected converted agent at {path}, got {result!r}"

    def test_plugin_command_becomes_prompt_with_losses(self) -> None:
        """Claude argument placeholders and tool permissions are reported."""
        header: Frontmatter = {
            "description": "D",
            "argument-hint": "[env]",
            "allowed-tools": "Bash",
        }
        item = Builder.item(
            Kind.PROMPT,
            frontmatter=header,
            body="Deploy $1 $ARGUMENTS after !`git status`.\n",
            plugin="kit",
        )

        result = VSCodeProfile().render(item)

        written = FrontmatterCodec().parse(result.files[0].content).frontmatter
        assert (result.files[0].path, written, result.losses) == (
            "sample.prompt.md",
            {"description": "D", "argument-hint": "[env]"},
            (
                "drops frontmatter: allowed-tools",
                "uses Claude Code argument placeholders: $1, $ARGUMENTS",
                "runs shell commands with !`...`, which only Claude Code executes",
            ),
        ), f"Expected converted prompt, got {result!r}"


class TestCopilotServerOptions:
    """SkillSinc's own server options never reach Copilot CLI's file."""

    def test_options_are_stripped_and_overrides_applied(self) -> None:
        """Copied fields lose SkillSinc options and gain ``profiles.copilot``."""
        server: dict[str, JsonValue] = {
            "type": "local",
            "command": "a",
            "enabled": True,
            "startupTimeoutSec": 5,
            "profiles": {"copilot": {"tools": ["*"]}},
        }
        item = Builder.item(Kind.MCP, name="api", frontmatter=server)

        result = CopilotProfile().render(item)

        assert result.entries == (
            EntryOutput(
                "mcp-config.json",
                "mcpServers",
                "api",
                {"type": "local", "command": "a", "tools": ["*"]},
            ),
        ), f"Expected stripped server, got {result!r}"
        assert result.losses == ("drops per-server timeouts: startupTimeoutSec",)

    def test_disabled_server_is_left_out(self) -> None:
        """Copilot CLI has no documented switch, so a disabled server is refused."""
        item = Builder.item(Kind.MCP, frontmatter={"command": "a", "enabled": False})

        result = CopilotProfile().render(item)

        assert result.unsupported is not None, f"Expected refusal, got {result!r}"


class TestVSCodeSkillLocations:
    """VS Code's own settings decide which other skill folders it reads."""

    @staticmethod
    def write(folder: Path, text: str) -> None:
        """Write ``folder/settings.json``."""
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "settings.json").write_text(text, encoding="utf-8")

    @staticmethod
    def environment(tmp_path: Path, platform: str = "linux") -> Environment:
        """Return an environment whose VS Code settings live under ``tmp_path``."""
        return Environment(
            tmp_path / "home", {"XDG_CONFIG_HOME": str(tmp_path / "config")}, platform
        )

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "not json",
            "[]",
            '{"chat.agentSkillsLocations": []}',
            '{"chat.agentSkillsLocations": {"~/.claude/skills": "no"}}',
            '{"chat.agentSkillsLocations": {"~/.claude/skills": true}}',
        ],
    )
    def test_default_reads_stay_without_a_false_entry(
        self, tmp_path: Path, text: str
    ) -> None:
        """Missing, unreadable, or enabling settings keep VS Code's defaults."""
        environment = self.environment(tmp_path)
        if text:
            self.write(tmp_path / "config/Code/User", text)

        reads = VSCodeProfile().reads_with(environment)

        assert reads == VSCodeProfile.also_reads, f"Expected defaults, got {reads!r}"

    @pytest.mark.parametrize(
        "key", ["~\\\\.agents\\\\skills", "~/.Agents/skills", ".agents/skills"]
    )
    def test_keys_match_literally_as_in_vs_code(self, tmp_path: Path, key: str) -> None:
        """VS Code compares keys literally, so variants keep the default."""
        environment = self.environment(tmp_path)
        self.write(
            tmp_path / "config/Code/User",
            json.dumps({"chat.agentSkillsLocations": {key: False}}),
        )

        reads = VSCodeProfile().reads_with(environment)

        assert reads == VSCodeProfile.also_reads, f"Expected defaults, got {reads!r}"

    def test_workspace_settings_override_user_settings(self, tmp_path: Path) -> None:
        """In a project, ``.vscode/settings.json`` wins for its own keys."""
        environment = self.environment(tmp_path)
        project = tmp_path / "repo"
        self.write(
            tmp_path / "config/Code/User",
            '{"chat.agentSkillsLocations": {".claude/skills": false}}',
        )
        self.write(
            project / ".vscode",
            '{"chat.agentSkillsLocations": {".claude/skills": true,'
            ' ".agents/skills": false, "~/.github/skills": false}}',
        )

        reads = VSCodeProfile(project).reads_with(environment)

        assert Kind.SKILL in reads["claude"], f"Expected claude kept, got {reads!r}"
        assert Kind.SKILL not in reads["agents"], f"Expected agents off: {reads!r}"
        assert Kind.SKILL in reads["copilot"], f"Expected home key ignored: {reads!r}"

    def test_project_copilot_key_is_github_skills(self, tmp_path: Path) -> None:
        """In a workspace the Copilot folder is ``.github/skills``."""
        project = tmp_path / "repo"
        self.write(
            project / ".vscode",
            '{"chat.agentSkillsLocations": {".github/skills": false}}',
        )

        reads = VSCodeProfile(project).reads_with(self.environment(tmp_path))

        assert Kind.SKILL not in reads["copilot"], f"Expected copilot off: {reads!r}"
