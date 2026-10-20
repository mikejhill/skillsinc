"""Exercise user journeys through Application with isolated filesystem state."""

from __future__ import annotations

import json
import shutil

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
