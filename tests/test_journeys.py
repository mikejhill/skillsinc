"""Exercise user journeys through Application with isolated filesystem state."""

from __future__ import annotations

import json
import shutil
import tomllib

import pytest
from tests.conftest import Builder, Sandbox

from skillsinc.lock import StateLock
from skillsinc.manifest import ManifestStore


class TestApplication:
    """Complete init, reconciliation, adoption, and failure journeys."""

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
            "version=2",
            "version=1\nunknown=true",
            "version=1\n[[routes]]\nsources=['missing']\ndestinations=['missing']",
            (
                "version=1\n[destinations.a]\nprofile='generic'\n"
                "path='out'\n[destinations.b]\nprofile='generic'"
                "\npath='out/child'"
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
