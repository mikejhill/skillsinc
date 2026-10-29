"""Exercise user journeys through Application with isolated filesystem state."""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.conftest import Builder, Sandbox

from skillsinc.lock import StateLock
from skillsinc.manifest import ManifestStore


class TestSourceScanFailure:
    """A failed traversal preserves previously delivered files and ownership."""

    @pytest.mark.parametrize("command", ["plan", "sync"])
    def test_unreadable_assets_preserve_every_destination(
        self, sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch, command: str
    ) -> None:
        """Refuse partial libraries instead of deleting assets omitted by os.walk."""
        other = sandbox.environment.home / "other"
        Builder.write_config(
            sandbox.config_path,
            sandbox.library,
            (("target", "generic", sandbox.root), ("other", "generic", other)),
        )
        assert sandbox.run("sync")[0] == 0, "Expected initial sync to succeed"
        before = {
            path: path.read_bytes()
            for root in (sandbox.root, other, sandbox.environment.state_directory)
            for path in root.rglob("*")
            if path.is_file()
        }
        unreadable = sandbox.library / "skills/sample/references"
        original = os.scandir

        def fail(path: str) -> Iterator[os.DirEntry[str]]:
            """Refuse exactly one source subtree during the next run."""
            if Path(path) == unreadable:
                raise PermissionError("synthetic directory scan failure")
            return original(path)

        monkeypatch.setattr(os, "scandir", fail)

        status, output = sandbox.run(command)

        assert status == 1, f"Expected failed source exit 1, got {status}: {output}"
        assert "synthetic directory scan failure" in output, (
            f"Expected source traversal error, got {output}"
        )
        assert all(path.read_bytes() == content for path, content in before.items()), (
            "Expected every destination file and ownership manifest unchanged"
        )


class TestApplication:
    """Complete init, reconciliation, adoption, and failure journeys."""

    def test_guidance_uses_normal_sync_and_source_edits(self, sandbox: Sandbox) -> None:
        """Bootstrap stays local until sync and subsequent source edits converge."""
        for name in (".claude", ".agents", ".codex", ".copilot"):
            (sandbox.environment.home / name).mkdir()
        status, _ = sandbox.run("init", "--with-guidance")
        root = sandbox.environment.default_library
        instruction = root / "instructions/skillsinc.instructions.md"

        assert status == 0, "Expected optional bootstrap to succeed"
        assert not sandbox.environment.state_directory.exists(), (
            "Expected init not to sync or create ownership state"
        )
        for name in (".claude", ".agents", ".codex", ".copilot"):
            assert list((sandbox.environment.home / name).iterdir()) == [], (
                f"Expected {name} left empty before explicit sync"
            )
        assert sandbox.run("sync")[0] == 0, "Expected portable starter sync"
        instruction.write_text(
            instruction.read_text(encoding="utf-8") + "\nLocal customization.\n",
            encoding="utf-8",
        )
        assert sandbox.run("sync")[0] == 0, "Expected canonical edit distributed"
        codex = sandbox.environment.home / ".codex/AGENTS.md"
        assert "Local customization." in codex.read_text(encoding="utf-8"), (
            "Expected source customization in the managed Codex block"
        )
        status, report = sandbox.run("plan", "--json")
        data = json.loads(report)
        assert status == 0, f"Expected successful convergence check, got {report}"
        assert all(
            not destination["changes"] for destination in data["destinations"]
        ), f"Expected convergence, got {report}"

    def test_destination_root_linked_into_library_is_refused(
        self, sandbox: Sandbox
    ) -> None:
        """Refuse a destination whose root is a link resolving inside the library."""
        sandbox.configure()
        inside = sandbox.library / "linked-target"
        inside.mkdir()
        try:
            sandbox.root.symlink_to(inside, target_is_directory=True)
        except OSError as err:
            pytest.skip(f"Platform cannot create directory symlinks: {err}")

        status, output = sandbox.run("sync")

        assert status == 1, f"Expected overlap refusal exit 1, got {status}: {output}"
        assert list(inside.iterdir()) == [], (
            f"Expected no writes into the library, got {list(inside.iterdir())!r}"
        )

    def test_init_plan_sync_then_up_to_date(self, sandbox: Sandbox) -> None:
        """Detect installed harnesses and converge after the first sync."""
        for name in (".claude", ".agents"):
            (sandbox.environment.home / name).mkdir()

        status, _ = sandbox.run("init", "--source", str(sandbox.library))
        config = sandbox.load()
        preview_status, preview = sandbox.run("plan")
        sync_status, _ = sandbox.run("sync")
        final_status, final = sandbox.run("plan")

        assert (status, preview_status, sync_status, final_status) == (0, 0, 0, 0), (
            f"Expected (0, 0, 0, 0), got "
            f"{status, preview_status, sync_status, final_status}"
        )
        assert set(config.destinations) == {"claude", "agents"}, (
            f"Expected only installed harnesses enabled, got {config.destinations!r}"
        )
        assert "create" in preview, (
            f"Expected creates followed by convergence, got {preview!r}, {final!r}"
        )
        assert "up to date" in final, (
            f"Expected creates followed by convergence, got {preview!r}, {final!r}"
        )
        assert "create" not in final, (
            f"Expected creates followed by convergence, got {preview!r}, {final!r}"
        )

    def test_removed_skill_deletes_files_and_prunes_empty_directories(
        self, sandbox: Sandbox
    ) -> None:
        """Delete removed unchanged skills and prune their empty directories."""
        sandbox.configure()
        assert sandbox.run("sync")[0] == 0, "Expected initial sync to succeed"
        shutil.rmtree(sandbox.library / "skills/sample")

        status, output = sandbox.run("sync")

        assert status == 0, (
            f"Expected skill files and directories deleted, got {status}: {output}"
        )
        assert not (sandbox.root / "skills").exists(), (
            f"Expected skill files and directories deleted, got {status}: {output}"
        )

    def test_removed_edited_file_is_released_and_preserved(
        self, sandbox: Sandbox
    ) -> None:
        """Release edited files when their authored source is removed."""
        sandbox.configure()
        assert sandbox.run("sync")[0] == 0, "Expected initial sync to succeed"
        edited = sandbox.root / "skills/sample/references/guide.md"
        edited.write_bytes(b"User edits")
        shutil.rmtree(sandbox.library / "skills/sample")

        status, output = sandbox.run("sync")

        manifest = ManifestStore(sandbox.environment.state_directory).load(
            "target", sandbox.root
        )
        assert status == 0, f"Expected preserved released file, got {status}: {output}"
        assert "forget" in output, (
            f"Expected preserved released file, got {status}: {output}"
        )
        assert edited.read_bytes() == b"User edits", (
            f"Expected preserved released file, got {status}: {output}"
        )
        assert "skills/sample/references/guide.md" not in manifest.files, (
            f"Expected ownership released, got {manifest.files!r}"
        )

    def test_drift_is_repaired_with_local_edit_warning(self, sandbox: Sandbox) -> None:
        """Sync restores a managed file to source bytes and explains the overwrite."""
        sandbox.configure()
        assert sandbox.run("sync")[0] == 0, "Expected initial sync to succeed"
        target = sandbox.root / "skills/sample/SKILL.md"
        target.write_bytes(b"Edited")

        status, output = sandbox.run("sync")

        assert status == 0, (
            f"Expected drift warning and success, got {status}: {output}"
        )
        assert "overwrites local edits" in output, (
            f"Expected drift warning and success, got {status}: {output}"
        )
        assert (
            target.read_bytes()
            == (sandbox.library / "skills/sample/SKILL.md").read_bytes()
        ), f"Expected authored skill restored, got {target.read_bytes()!r}"

    def test_unmanaged_conflict_blocks_one_destination_then_adoption_succeeds(
        self, sandbox: Sandbox
    ) -> None:
        """Isolate destination conflicts and resolve them with adoption."""
        other = sandbox.environment.home / "other"
        Builder.write_config(
            sandbox.config_path,
            sandbox.library,
            (("target", "generic", sandbox.root), ("other", "generic", other)),
        )
        existing = sandbox.root / "skills/sample/SKILL.md"
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"User file")

        status, output = sandbox.run("sync")

        assert status == 1, (
            f"Expected blocked user file unchanged, got {status}: {output}"
        )
        assert existing.read_bytes() == b"User file", (
            f"Expected blocked user file unchanged, got {status}: {output}"
        )
        assert sorted(
            path.relative_to(sandbox.root).as_posix()
            for path in sandbox.root.rglob("*")
            if path.is_file()
        ) == ["skills/sample/SKILL.md"], (
            "Expected no additional writes in blocked destination"
        )
        assert (other / "skills/sample/SKILL.md").is_file(), (
            "Expected independent destination synced"
        )

        adopted_status, adopted_output = sandbox.run("sync", "--adopt")

        assert adopted_status == 0, (
            f"Expected adoption success, got {adopted_status}: {adopted_output}"
        )
        assert "adopt" in adopted_output, (
            f"Expected adoption success, got {adopted_status}: {adopted_output}"
        )
        assert (
            "skills/sample/SKILL.md"
            in ManifestStore(sandbox.environment.state_directory)
            .load("target", sandbox.root)
            .files
        ), "Expected adopted file owned"

    def test_missing_source_preserves_every_routed_destination(
        self, sandbox: Sandbox
    ) -> None:
        """Preserve every routed destination when a source cannot be read."""
        other = sandbox.environment.home / "other"
        Builder.write_config(
            sandbox.config_path,
            sandbox.library,
            (("target", "generic", sandbox.root), ("other", "generic", other)),
        )
        assert sandbox.run("sync")[0] == 0, "Expected initial sync to succeed"
        before = {
            path: path.read_bytes()
            for root in (sandbox.root, other)
            for path in root.rglob("*")
            if path.is_file()
        }
        sandbox.library.rename(sandbox.library.with_name("moved-library"))

        status, output = sandbox.run("sync")

        after = {
            path: path.read_bytes()
            for root in (sandbox.root, other)
            for path in root.rglob("*")
            if path.is_file()
        }
        assert status == 1, (
            f"Expected source failure preserving all files, got {status}: {output}"
        )
        assert after == before, (
            f"Expected source failure preserving all files, got {status}: {output}"
        )

    @pytest.mark.parametrize("user_content", [None, "User instructions.\n"])
    def test_codex_block_lifecycle_preserves_user_content(
        self, sandbox: Sandbox, user_content: str | None
    ) -> None:
        """Append, update, and remove a Codex block while respecting file ownership."""
        sandbox.configure("codex", allow_lossy=True)
        shared = sandbox.root / "AGENTS.md"
        if user_content is not None:
            shared.parent.mkdir(parents=True)
            shared.write_text(user_content, encoding="utf-8")
        assert sandbox.run("sync")[0] == 0, "Expected initial Codex sync"
        initial = shared.read_text(encoding="utf-8")
        (sandbox.library / "instructions/global.md").write_text(
            "Updated guidance.\n", encoding="utf-8"
        )
        assert sandbox.run("sync")[0] == 0, "Expected Codex block update"
        updated = shared.read_text(encoding="utf-8")
        shutil.rmtree(sandbox.library / "instructions")

        status, output = sandbox.run("sync")

        assert "Global guidance." in initial, (
            f"Expected one block updated in place, got {updated!r}"
        )
        assert "Updated guidance." in updated, (
            f"Expected one block updated in place, got {updated!r}"
        )
        assert updated.count("<!-- skillsinc:begin -->") == 1, (
            f"Expected one block updated in place, got {updated!r}"
        )
        expected = user_content is not None
        assert status == 0, (
            f"Expected file existence {expected}, got {shared.exists()}: {output}"
        )
        assert shared.exists() is expected, (
            f"Expected file existence {expected}, got {shared.exists()}: {output}"
        )
        if user_content is not None:
            assert (
                shared.read_text(encoding="utf-8") == user_content
            ), f"Expected user content preserved, got {
                shared.read_text(encoding=('utf-8'))!r
            }"

    def test_codex_agent_toml_lifecycle(self, sandbox: Sandbox) -> None:
        """Create, update, and delete a Codex custom agent file."""
        sandbox.configure("codex", allow_lossy=True)
        agent = sandbox.library / "agents/reviewer.agent.md"
        target = sandbox.root / "agents/reviewer.toml"
        assert sandbox.run("sync")[0] == 0, "Expected initial Codex sync"
        initial = tomllib.loads(target.read_text(encoding="utf-8"))
        agent.write_text(
            "---\ndescription: Review code\n---\nReview twice.\n", encoding="utf-8"
        )
        assert sandbox.run("sync")[0] == 0, "Expected Codex agent update"
        updated = tomllib.loads(target.read_text(encoding="utf-8"))
        agent.unlink()

        status, output = sandbox.run("sync")

        assert (initial, updated["developer_instructions"]) == (
            {
                "name": "reviewer",
                "description": "Review code",
                "developer_instructions": "Review.",
            },
            "Review twice.",
        ), f"Expected agent written then updated, got {initial!r}, {updated!r}"
        assert status == 0, f"Expected agent removal sync, got {status}: {output}"
        assert not target.parent.exists(), (
            f"Expected agent file and empty folder removed: {output}"
        )

    @pytest.mark.parametrize("allow_lossy", [False, True])
    def test_lossy_conversion_requires_route_permission(
        self, sandbox: Sandbox, *, allow_lossy: bool
    ) -> None:
        """Skip lossy conversions unless the route explicitly accepts them."""
        sandbox.configure("claude", allow_lossy=allow_lossy)

        status, output = sandbox.run("sync")

        actual = (sandbox.root / "agents/reviewer.md").exists()
        assert status == 0, (
            f"Expected lossy agent existence {allow_lossy}, got {actual}: {output}"
        )
        assert actual is allow_lossy, (
            f"Expected lossy agent existence {allow_lossy}, got {actual}: {output}"
        )

    def test_symlink_component_is_refused(self, sandbox: Sandbox) -> None:
        """Never write desired files through a destination symlink component."""
        sandbox.configure()
        sandbox.root.mkdir()
        outside = sandbox.environment.home / "outside"
        outside.mkdir()
        try:
            (sandbox.root / "skills").symlink_to(outside, target_is_directory=True)
        except OSError as err:
            pytest.skip(f"Platform cannot create directory symlinks: {err}")

        status, output = sandbox.run("sync")

        assert status == 1, (
            f"Expected symlink refusal without external writes, got {status}: {output}"
        )
        assert "link or junction" in output, (
            f"Expected symlink refusal without external writes, got {status}: {output}"
        )
        assert list(outside.iterdir()) == [], (
            f"Expected symlink refusal without external writes, got {status}: {output}"
        )

    def test_held_lock_fails_sync(self, sandbox: Sandbox) -> None:
        """An overlapping sync returns failure without writing destination files."""
        sandbox.configure()

        with StateLock(sandbox.environment.state_directory / "sync.lock"):
            status, output = sandbox.run("sync")

        assert status == 1, (
            f"Expected concurrent sync failure without writes, got {status}: {output}"
        )
        assert not sandbox.root.exists(), (
            f"Expected concurrent sync failure without writes, got {status}: {output}"
        )

    @pytest.mark.parametrize(
        "text",
        [
            None,
            '{"version": 2}',
            '{"version": 1, "unknown": true}',
            '{"version": 1, "routes": [{"sources": ["a"], "destinations": ["b"]}]}',
            (
                '{"version": 1, "destinations": {'
                '"a": {"profile": "generic", "path": "out"},'
                ' "b": {"profile": "generic", "path": "out/child"}}}'
            ),
        ],
    )
    def test_config_errors_exit_two(self, sandbox: Sandbox, text: str | None) -> None:
        """Missing, invalid, unknown, and overlapping configuration are usage errors."""
        if text is not None:
            sandbox.config_path.parent.mkdir(parents=True)
            sandbox.config_path.write_text(text, encoding="utf-8")

        status, output = sandbox.run("sync")

        assert status == 2, (
            f"Expected config error exit 2 without writes, got {status}: {output}"
        )
        assert not sandbox.root.exists(), (
            f"Expected config error exit 2 without writes, got {status}: {output}"
        )

    def test_json_sync_reports_applied_counts(self, sandbox: Sandbox) -> None:
        """Machine-readable sync output reports success and actual applied counts."""
        sandbox.configure()

        status, output = sandbox.run("sync", "--json")

        report = json.loads(output)
        assert (
            status,
            report["ok"],
            report["applied"],
            report["destinations"][0]["applied"],
        ) == (0, True, True, 7), f"Expected seven applied files, got {output}"

    def test_copilot_mcp_server_lifecycle_preserves_user_servers(
        self, sandbox: Sandbox
    ) -> None:
        """Add, update, and remove one server while the user's own keys stay intact."""
        sandbox.configure("copilot", allow_lossy=True)
        config_file = sandbox.root / "mcp-config.json"
        config_file.parent.mkdir(parents=True)
        user = {"mcpServers": {"mine": {"command": "mine"}}, "other": True}
        config_file.write_text(json.dumps(user), encoding="utf-8")
        server = sandbox.library / "mcp/github.json"
        server.parent.mkdir()
        server.write_text('{"type": "local", "command": "gh"}', encoding="utf-8")

        first_status, first = sandbox.run("sync")
        added = json.loads(config_file.read_text(encoding="utf-8"))
        server.write_text('{"type": "local", "command": "gh2"}', encoding="utf-8")
        update_status, _ = sandbox.run("sync")
        updated = json.loads(config_file.read_text(encoding="utf-8"))
        server.unlink()
        remove_status, removed_output = sandbox.run("sync")
        final_status, final = sandbox.run("plan")

        assert (first_status, update_status, remove_status, final_status) == (
            0,
            0,
            0,
            0,
        ), f"Expected four successful runs, got {first!r}, {removed_output!r}"
        assert "+ create mcp-config.json [mcpServers/github]" in first, (
            f"Expected entry creation in output, got {first!r}"
        )
        assert added["mcpServers"] == {
            "mine": {"command": "mine"},
            "github": {"type": "local", "command": "gh"},
        }, f"Expected both servers after first sync, got {added!r}"
        assert updated["mcpServers"]["github"]["command"] == "gh2", (
            f"Expected server updated in place, got {updated!r}"
        )
        assert json.loads(config_file.read_text(encoding="utf-8")) == user, (
            f"Expected user file restored exactly, got {config_file.read_text()!r}"
        )
        assert "up to date" in final, f"Expected convergence, got {final!r}"

    def test_codex_mcp_server_lifecycle_preserves_user_toml(
        self, sandbox: Sandbox
    ) -> None:
        """Add, update, and remove a Codex server; the user's TOML text survives."""
        sandbox.configure("codex", allow_lossy=True)
        config_file = sandbox.root / "config.toml"
        config_file.parent.mkdir(parents=True)
        user = '# mine\nmodel = "o3"  # pinned\n\n[mcp_servers.mine]\ncommand = "m"\n'
        config_file.write_text(user, encoding="utf-8")
        server = sandbox.library / "mcp/github.json"
        server.parent.mkdir()
        server.write_text(
            '{"type": "local", "command": "gh", "env": {"K": "v"}}', encoding="utf-8"
        )

        first_status, first = sandbox.run("sync")
        added = config_file.read_text(encoding="utf-8")
        server.write_text('{"type": "local", "command": "gh2"}', encoding="utf-8")
        update_status, _ = sandbox.run("sync")
        updated = tomllib.loads(config_file.read_text(encoding="utf-8"))
        server.unlink()
        remove_status, removed_output = sandbox.run("sync")
        final_status, final = sandbox.run("plan")

        assert (first_status, update_status, remove_status, final_status) == (
            0,
            0,
            0,
            0,
        ), f"Expected four successful runs, got {first!r}, {removed_output!r}"
        assert "+ create config.toml [mcp_servers/github]" in first, (
            f"Expected entry creation in output, got {first!r}"
        )
        assert added.startswith(user), f"Expected user text kept, got {added!r}"
        assert tomllib.loads(added)["mcp_servers"]["github"] == {
            "command": "gh",
            "env": {"K": "v"},
        }, f"Expected the server table, got {added!r}"
        assert updated["mcp_servers"]["github"] == {"command": "gh2"}, (
            f"Expected server updated in place, got {updated!r}"
        )
        assert config_file.read_text(encoding="utf-8") == user, (
            f"Expected user file restored exactly, got {config_file.read_text()!r}"
        )
        assert "up to date" in final, f"Expected convergence, got {final!r}"

    def test_unmanaged_mcp_server_blocks_until_adopted(self, sandbox: Sandbox) -> None:
        """A same-named server the user wrote is never overwritten without --adopt."""
        sandbox.configure("copilot")
        config_file = sandbox.root / "mcp-config.json"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(
            '{"mcpServers": {"github": {"command": "user"}}}', encoding="utf-8"
        )
        server = sandbox.library / "mcp/github.json"
        server.parent.mkdir()
        server.write_text('{"command": "gh"}', encoding="utf-8")

        blocked_status, blocked = sandbox.run("sync")
        kept = json.loads(config_file.read_text(encoding="utf-8"))
        adopt_status, _ = sandbox.run("sync", "--adopt")
        adopted = json.loads(config_file.read_text(encoding="utf-8"))

        assert blocked_status == 1, f"Expected block, got {blocked_status}: {blocked}"
        assert kept["mcpServers"]["github"] == {"command": "user"}, (
            f"Expected user server untouched, got {kept!r}"
        )
        assert adopt_status == 0, f"Expected adoption to succeed, got {adopt_status}"
        assert adopted["mcpServers"]["github"] == {"command": "gh"}, (
            f"Expected adopted server replaced, got {adopted!r}"
        )

    def test_claude_resource_imports_lifecycle_preserves_user_memory(
        self, sandbox: Sandbox
    ) -> None:
        """Copy resources, import them from CLAUDE.md, then remove both cleanly."""
        sandbox.configure("claude", allow_lossy=True)
        memory = sandbox.root / "CLAUDE.md"
        memory.parent.mkdir(parents=True)
        memory.write_text("My own memory.\n", encoding="utf-8")
        resources = sandbox.library / "resources"
        resources.mkdir()
        (resources / "style.md").write_text("Style guide.\n", encoding="utf-8")
        (resources / "api.md").write_text("API notes.\n", encoding="utf-8")

        first_status, first = sandbox.run("sync")
        imported = memory.read_text(encoding="utf-8")
        copied = (sandbox.root / "resources/style.md").read_text(encoding="utf-8")
        shutil.rmtree(resources)
        remove_status, removed = sandbox.run("sync")
        final_status, final = sandbox.run("plan")

        assert (first_status, remove_status, final_status) == (0, 0, 0), (
            f"Expected three successful runs, got {first!r}, {removed!r}"
        )
        assert imported.startswith("My own memory.\n"), (
            f"Expected user memory kept above the block, got {imported!r}"
        )
        assert imported.index("@resources/api.md") < imported.index(
            "@resources/style.md"
        ), f"Expected imports in name order, got {imported!r}"
        assert copied == "Style guide.\n", f"Expected verbatim copy, got {copied!r}"
        assert memory.read_text(encoding="utf-8") == "My own memory.\n", (
            f"Expected user memory restored exactly, got {memory.read_text()!r}"
        )
        assert not (sandbox.root / "resources").exists(), (
            f"Expected resources folder pruned, got {removed!r}"
        )
        assert "up to date" in final, f"Expected convergence, got {final!r}"

    def test_plugin_expands_into_claude_and_copilot(self, sandbox: Sandbox) -> None:
        """A plugin syncs its parts per harness, with unsynced hooks as a loss."""
        root = sandbox.library / "plugins/kit"
        files = {
            ".claude-plugin/plugin.json": '{"name": "kit"}',
            "agents/checker.md": "---\ndescription: Check\ntools: Read\n---\nCheck.\n",
            "commands/ship.md": "---\ndescription: Ship\n---\nShip $ARGUMENTS.\n",
            ".mcp.json": '{"mcpServers": {"api": {"command": "api"}}}',
            "commands/fmt.md": "Run ${CLAUDE_PLUGIN_ROOT}/fmt.sh.\n",
            "hooks/hooks.json": "{}",
        }
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        claude = sandbox.environment.home / "claude"
        copilot = sandbox.environment.home / "copilot"
        Builder.write_config(
            sandbox.config_path,
            sandbox.library,
            (("claude", "claude", claude), ("copilot", "copilot", copilot)),
            include=("plugins/kit/*",),
        )

        _, strict = sandbox.run("plan", "--verbose")
        Builder.write_config(
            sandbox.config_path,
            sandbox.library,
            (("claude", "claude", claude), ("copilot", "copilot", copilot)),
            include=("plugins/kit/*",),
            allow_lossy=True,
        )
        status, output = sandbox.run("sync")

        assert "hooks/, which is not synced" in strict, (
            f"Expected hooks named as a loss, got {strict!r}"
        )
        assert status == 0, f"Expected a successful sync, got {status}: {output}"
        assert (claude / "agents/checker.md").read_bytes() == (
            root / "agents/checker.md"
        ).read_bytes(), "Expected the plugin agent copied exactly into Claude"
        assert (claude / "commands/ship.md").is_file(), (
            f"Expected the plugin command in Claude, got {output}"
        )
        assert json.loads((copilot / "mcp-config.json").read_text())["mcpServers"] == {
            "api": {"command": "api"}
        }, f"Expected the plugin server, got {output}"
        assert (copilot / "agents/checker.agent.md").is_file(), (
            f"Expected the converted agent in Copilot, got {output}"
        )
        assert not (claude / "commands/fmt.md").exists(), (
            f"Expected the command needing the plugin root refused, got {output}"
        )
        assert "only an installed plugin resolves" in strict, (
            f"Expected the refusal reason reported, got {strict!r}"
        )
        assert not (claude / "skills").exists(), (
            f"Expected library items outside the plugin left unrouted, got {output}"
        )

    def test_claude_hooks_lifecycle_preserves_user_settings(
        self, sandbox: Sandbox
    ) -> None:
        """Hooks enter settings.json beside the user's keys and leave it as found."""
        sandbox.configure("claude", allow_lossy=True)
        settings = sandbox.root / "settings.json"
        settings.parent.mkdir(parents=True)
        user = {
            "model": "x",
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "me"}]}]},
        }
        settings.write_text(json.dumps(user), encoding="utf-8")
        hook = sandbox.library / "hooks/guard.json"
        hook.parent.mkdir()
        hook.write_text(
            '{"version": 1, "hooks": {"preToolUse": [{"bash": "guard.sh"}]}}',
            encoding="utf-8",
        )

        first_status, first = sandbox.run("sync")
        added = json.loads(settings.read_text(encoding="utf-8"))
        hook.unlink()
        remove_status, _ = sandbox.run("sync")
        final_status, final = sandbox.run("plan")

        assert (first_status, remove_status, final_status) == (0, 0, 0), (
            f"Expected three successful runs, got {first!r}"
        )
        assert "settings.json [hooks/PreToolUse]" in first, (
            f"Expected hook entry creation, got {first!r}"
        )
        assert added["hooks"]["PreToolUse"] == [
            {"hooks": [{"type": "command", "command": "guard.sh", "timeout": 30}]}
        ], f"Expected converted hook, got {added!r}"
        assert added["hooks"]["Stop"] == user["hooks"]["Stop"], (
            f"Expected user hook kept, got {added!r}"
        )
        assert json.loads(settings.read_text(encoding="utf-8")) == user, (
            f"Expected user settings restored, got {settings.read_text()!r}"
        )
        assert "up to date" in final, f"Expected convergence, got {final!r}"

    def test_user_hooks_for_the_same_event_block_until_adopted(
        self, sandbox: Sandbox
    ) -> None:
        """An event the user already hooks is never replaced without --adopt."""
        sandbox.configure("claude")
        settings = sandbox.root / "settings.json"
        settings.parent.mkdir(parents=True)
        user = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "me"}]}]}}
        settings.write_text(json.dumps(user), encoding="utf-8")
        hook = sandbox.library / "hooks/stop.json"
        hook.parent.mkdir()
        hook.write_text(
            '{"version": 1, "hooks": {"Stop": [{"bash": "lib.sh"}]}}',
            encoding="utf-8",
        )

        blocked_status, blocked = sandbox.run("sync")
        kept = json.loads(settings.read_text(encoding="utf-8"))

        assert blocked_status == 1, f"Expected block, got {blocked_status}: {blocked}"
        assert kept == user, f"Expected user settings untouched, got {kept!r}"

    def test_copilot_hook_file_is_copied(self, sandbox: Sandbox) -> None:
        """Copilot CLI receives the authored hook file unchanged."""
        sandbox.configure("copilot", allow_lossy=True)
        text = '{"version": 1, "hooks": {"sessionStart": [{"bash": "a.sh"}]}}'
        hook = sandbox.library / "hooks/start.json"
        hook.parent.mkdir()
        hook.write_text(text, encoding="utf-8")

        status, output = sandbox.run("sync")

        copied = (sandbox.root / "hooks/start.json").read_text(encoding="utf-8")
        assert (status, copied) == (0, text), f"Expected exact copy, got {output!r}"

    def test_claude_mcp_servers_go_into_home_claude_json(
        self, sandbox: Sandbox
    ) -> None:
        """Servers land in ``~/.claude.json`` beside the root and keep its mode."""
        sandbox.root = sandbox.environment.home / ".claude"
        sandbox.configure("claude")
        claude_json = sandbox.environment.home / ".claude.json"
        user = {"numStartups": 3, "mcpServers": {"mine": {"command": "mine"}}}
        claude_json.write_text(json.dumps(user), encoding="utf-8")
        claude_json.chmod(0o600)
        server = sandbox.library / "mcp/github.json"
        server.parent.mkdir()
        server.write_text('{"command": "gh"}', encoding="utf-8")

        first_status, first = sandbox.run("sync")
        added = json.loads(claude_json.read_text(encoding="utf-8"))
        mode = stat.S_IMODE(claude_json.stat().st_mode)
        server.unlink()
        remove_status, _ = sandbox.run("sync")

        assert first_status == 0, f"Expected sync to succeed, got {first!r}"
        assert "+ create ../.claude.json [mcpServers/github]" in first, (
            f"Expected entry creation beside the root, got {first!r}"
        )
        assert added == {
            "numStartups": 3,
            "mcpServers": {
                "mine": {"command": "mine"},
                "github": {"type": "stdio", "command": "gh"},
            },
        }, f"Expected both servers and user keys, got {added!r}"
        if sys.platform != "win32":
            assert mode == 0o600, f"Expected private mode kept, got {oct(mode)}"
        assert remove_status == 0, f"Expected removal to succeed, got {remove_status}"
        assert json.loads(claude_json.read_text(encoding="utf-8")) == user, (
            f"Expected user file restored, got {claude_json.read_text()!r}"
        )
        assert not (sandbox.root / ".claude.json").exists(), (
            "Expected nothing written inside ~/.claude"
        )

    def test_created_claude_json_is_removed_without_pruning_home(
        self, sandbox: Sandbox
    ) -> None:
        """A created sibling file goes with its last entry; its folder stays."""
        sandbox.root = sandbox.environment.home / ".claude"
        sandbox.configure("claude")
        claude_json = sandbox.environment.home / ".claude.json"
        server = sandbox.library / "mcp/github.json"
        server.parent.mkdir()
        server.write_text('{"command": "gh"}', encoding="utf-8")

        create_status, _ = sandbox.run("sync")
        created = claude_json.is_file()
        server.unlink()
        remove_status, _ = sandbox.run("sync")

        assert (create_status, remove_status, created) == (0, 0, True), (
            "Expected the file created then removed"
        )
        assert not claude_json.exists(), "Expected the created file deleted"
        assert sandbox.environment.home.is_dir(), "Expected home left in place"

    def test_linked_claude_json_blocks_the_destination(self, sandbox: Sandbox) -> None:
        """A ``~/.claude.json`` link is never written through."""
        sandbox.root = sandbox.environment.home / ".claude"
        sandbox.configure("claude")
        real = sandbox.environment.home / "dotfiles.json"
        real.write_text("{}", encoding="utf-8")
        try:
            (sandbox.environment.home / ".claude.json").symlink_to(real)
        except OSError as err:
            pytest.skip(f"Platform cannot create file symlinks: {err}")
        server = sandbox.library / "mcp/github.json"
        server.parent.mkdir()
        server.write_text('{"command": "gh"}', encoding="utf-8")

        status, output = sandbox.run("sync")

        assert status == 1, f"Expected a blocked destination, got {output!r}"
        assert "refusing to write through a link" in output, (
            f"Expected link refusal, got {output!r}"
        )
        assert real.read_text(encoding="utf-8") == "{}", "Expected link target intact"

    def test_linked_claude_root_keeps_claude_json_beside_it(
        self, sandbox: Sandbox, tmp_path: Path
    ) -> None:
        """A linked ``~/.claude`` still puts servers in ``~/.claude.json``."""
        target = tmp_path / "dotfiles" / "claude"
        target.mkdir(parents=True)
        sandbox.root = sandbox.environment.home / ".claude"
        try:
            sandbox.root.symlink_to(target, target_is_directory=True)
        except OSError as err:
            pytest.skip(f"Platform cannot create directory symlinks: {err}")
        sandbox.configure("claude")
        server = sandbox.library / "mcp/github.json"
        server.parent.mkdir()
        server.write_text('{"command": "gh"}', encoding="utf-8")

        status, output = sandbox.run("sync")

        assert status == 0, f"Expected sync to succeed, got {output!r}"
        assert (sandbox.environment.home / ".claude.json").is_file(), (
            "Expected ~/.claude.json written beside the link"
        )
        assert not (target.parent / ".claude.json").exists(), (
            "Expected nothing beside the link target"
        )

    def test_vscode_mcp_servers_go_into_user_mcp_json(self, sandbox: Sandbox) -> None:
        """Servers land in ``User/mcp.json`` beside the prompts folder."""
        user_dir = sandbox.environment.home / "Code" / "User"
        sandbox.root = user_dir / "prompts"
        sandbox.configure("vscode")
        mcp_json = user_dir / "mcp.json"
        user_dir.mkdir(parents=True)
        user = {"servers": {"mine": {"type": "stdio", "command": "x"}}, "inputs": []}
        mcp_json.write_text(json.dumps(user), encoding="utf-8")
        server = sandbox.library / "mcp/github.json"
        server.parent.mkdir()
        server.write_text('{"url": "https://x", "type": "http"}', encoding="utf-8")

        first_status, first = sandbox.run("sync")
        added = json.loads(mcp_json.read_text(encoding="utf-8"))
        server.unlink()
        remove_status, _ = sandbox.run("sync")

        assert first_status == 0, f"Expected sync to succeed, got {first!r}"
        assert added["servers"]["github"] == {"type": "http", "url": "https://x"}, (
            f"Expected server under servers, got {added!r}"
        )
        assert added["inputs"] == [], f"Expected inputs kept, got {added!r}"
        assert remove_status == 0, f"Expected removal to succeed, got {remove_status}"
        assert json.loads(mcp_json.read_text(encoding="utf-8")) == user, (
            f"Expected user file restored, got {mcp_json.read_text()!r}"
        )
        assert user_dir.is_dir(), "Expected User folder left in place"

    def test_vscode_mcp_json_keeps_comments(self, sandbox: Sandbox) -> None:
        """A commented ``mcp.json`` is edited in place and restored exactly."""
        user_dir = sandbox.environment.home / "Code" / "User"
        sandbox.root = user_dir / "prompts"
        sandbox.configure("vscode")
        mcp_json = user_dir / "mcp.json"
        user_dir.mkdir(parents=True)
        text = (
            '{\n  // mine\n  "servers": {\n'
            '    "x": {"command": "x"}, // note\n  },\n}\n'
        )
        mcp_json.write_text(text, encoding="utf-8", newline="")
        server = sandbox.library / "mcp/github.json"
        server.parent.mkdir()
        server.write_text('{"command": "gh"}', encoding="utf-8")

        add_status, output = sandbox.run("sync")
        added = mcp_json.read_text(encoding="utf-8")
        server.unlink()
        remove_status, _ = sandbox.run("sync")

        assert add_status == 0, f"Expected sync to succeed, got {output!r}"
        assert "// mine" in added, f"Expected comments kept, got {added!r}"
        assert '"github": {' in added, f"Expected the server added, got {added!r}"
        assert remove_status == 0, f"Expected removal to succeed, got {remove_status}"
        assert mcp_json.read_text(encoding="utf-8") == text, "Expected file restored"
