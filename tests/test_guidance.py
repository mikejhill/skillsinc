"""Verify optional bootstrap content, conflict preflight, and exclusive writes."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import Builder

from skillsinc.exceptions import ConfigurationError
from skillsinc.guidance import GUIDANCE_PATHS, GuidanceInstaller
from skillsinc.models import Kind, Support
from skillsinc.paths import Environment
from skillsinc.profiles.registry import ProfileRegistry
from skillsinc.sources import SourceReader
from skillsinc.starter import StarterConfig


class TestGuidance:
    """Guidance is ordinary portable library content, installed only on request."""

    def test_assets_load_and_render_without_losses(self, tmp_path: Path) -> None:
        """The bundled skill and global rule pass source and profile schemas."""
        installer = GuidanceInstaller()
        installer.install(installer.plan(tmp_path, tmp_path / "config.json"))
        items = SourceReader().read(Builder.source(tmp_path))

        assert {item.kind for item in items} == {Kind.SKILL, Kind.INSTRUCTION}, (
            f"Expected a skill and instruction, got {items!r}"
        )
        for item in items:
            for profile in ProfileRegistry().profiles:
                if profile.support[item.kind] is Support.UNSUPPORTED:
                    continue
                rendering = profile.render(item)
                assert not rendering.losses, (
                    f"Expected portable {item.kind} for {profile.name}: {rendering}"
                )

    def test_repeat_preserves_identical_files_and_unrelated_content(
        self, tmp_path: Path, environment: Environment
    ) -> None:
        """Forced config regeneration never rewrites matching guidance assets."""
        starter = StarterConfig(ProfileRegistry(), environment)
        config = tmp_path / "config.json"
        root = environment.default_library
        starter.write(config, None, force=False, with_guidance=True)
        unrelated = root / "notes.txt"
        unrelated.write_text("private local note", encoding="utf-8")
        before = {path: path.stat().st_mtime_ns for path in root.rglob("*.md")}

        starter.write(config, None, force=True, with_guidance=True)

        assert before == {path: path.stat().st_mtime_ns for path in before}, (
            "Expected identical guidance not to be rewritten"
        )
        assert unrelated.read_text(encoding="utf-8") == "private local note", (
            "Expected unrelated library content preserved"
        )
        assert not (root / ".git").exists(), "Expected no implicit Git initialization"

    @pytest.mark.parametrize("relative", GUIDANCE_PATHS)
    @pytest.mark.parametrize("directory", [False, True])
    def test_conflict_prevents_all_writes_even_with_force(
        self,
        tmp_path: Path,
        environment: Environment,
        relative: str,
        *,
        directory: bool,
    ) -> None:
        """Preflight both reserved names before changing config or library."""
        root = tmp_path / "library"
        target = root / relative
        target.parent.mkdir(parents=True)
        if directory:
            target.mkdir()
        else:
            target.write_text("Customized guidance", encoding="utf-8")
        config = tmp_path / "config.json"
        config.write_text("Original configuration", encoding="utf-8")

        with pytest.raises(ConfigurationError, match="conflicts"):
            StarterConfig(ProfileRegistry(), environment).write(
                config, root, force=True, with_guidance=True
            )

        assert config.read_text(encoding="utf-8") == "Original configuration", (
            "Expected configuration preserved on a guidance conflict"
        )
        other = next(name for name in GUIDANCE_PATHS if name != relative)
        assert not (root / other).exists(), "Expected no partial guidance install"
        assert (
            target.is_dir()
            if directory
            else target.read_text() == ("Customized guidance")
        ), "Expected conflicting content preserved"

    def test_parent_file_is_refused(self, tmp_path: Path) -> None:
        """A file in place of a source subdirectory blocks bootstrap."""
        (tmp_path / "skills").write_text("user file", encoding="utf-8")

        with pytest.raises(ConfigurationError, match="not a directory"):
            GuidanceInstaller().plan(tmp_path, tmp_path / "config.json")

        assert not (tmp_path / "instructions").exists(), "Expected no writes"

    @pytest.mark.parametrize("relative", GUIDANCE_PATHS)
    @pytest.mark.parametrize("aliased", ["none", "config", "source", "both"])
    def test_config_cannot_replace_guidance(
        self, tmp_path: Path, relative: str, aliased: str
    ) -> None:
        """Direct and aliased paths cannot configure over bootstrap targets."""
        root = tmp_path / "library"
        config = root / relative
        if aliased in {"config", "both"}:
            config = config.parent / ".." / config.parent.name / config.name
        if aliased in {"source", "both"}:
            root = root / ".." / root.name
        with pytest.raises(ConfigurationError, match="overlaps"):
            GuidanceInstaller().plan(root, config)

        assert list(tmp_path.iterdir()) == [], "Expected preflight to write nothing"

    def test_linked_configuration_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Do not normalize a configuration link into an apparently safe path."""
        config = tmp_path / "linked-config.json"
        monkeypatch.setattr(Path, "is_symlink", lambda path: path == config)

        with pytest.raises(ConfigurationError, match="passes through a link"):
            GuidanceInstaller().plan(tmp_path / "library", config)

        assert list(tmp_path.iterdir()) == [], "Expected no writes through config link"

    def test_junction_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reject a junction before reading or creating any starter file."""
        monkeypatch.setattr(Path, "is_junction", lambda path: path == tmp_path)

        with pytest.raises(ConfigurationError, match="passes through a link"):
            GuidanceInstaller().plan(tmp_path, tmp_path / "config.json")

        assert list(tmp_path.iterdir()) == [], "Expected no writes through junction"

    def test_link_is_refused(self, tmp_path: Path) -> None:
        """A linked library is not an implicit authorization to edit its target."""
        linked = tmp_path / "linked"
        try:
            linked.symlink_to(tmp_path, target_is_directory=True)
        except OSError as err:
            pytest.skip(f"Platform cannot create directory symlinks: {err}")

        with pytest.raises(ConfigurationError, match="passes through a link"):
            GuidanceInstaller().plan(linked, tmp_path / "config.json")

        assert not (tmp_path / "skills").exists(), "Expected link target untouched"

    def test_unreadable_existing_file_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unreadable target is a configuration error, with no installation."""
        installer = GuidanceInstaller()
        installer.install(installer.plan(tmp_path, tmp_path / "config.json"))
        target = tmp_path / GUIDANCE_PATHS[0]
        original = Path.read_bytes

        def read(path: Path) -> bytes:
            """Fail only the synthetic existing target read."""
            if path == target:
                raise PermissionError("synthetic read denial")
            return original(path)

        monkeypatch.setattr(Path, "read_bytes", read)
        with pytest.raises(ConfigurationError, match="cannot read guidance target"):
            installer.plan(tmp_path, tmp_path / "config.json")

    def test_concurrent_file_is_not_overwritten(self, tmp_path: Path) -> None:
        """Exclusive creation preserves a file that appeared after preflight."""
        installer = GuidanceInstaller()
        pending = installer.plan(tmp_path, tmp_path / "config.json")
        target = pending[0].path
        target.parent.mkdir(parents=True)
        target.write_bytes(b"Concurrent edit")

        with pytest.raises(ConfigurationError, match="cannot create guidance"):
            installer.install(pending)

        assert target.read_bytes() == b"Concurrent edit", (
            "Expected concurrent content preserved"
        )
        assert not pending[1].path.exists(), "Expected installation to stop on error"

    def test_link_appearing_after_plan_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Recheck path safety immediately before attempting file creation."""
        installer = GuidanceInstaller()
        pending = installer.plan(tmp_path, tmp_path / "config.json")
        monkeypatch.setattr(Path, "is_junction", lambda path: path == tmp_path)

        with pytest.raises(ConfigurationError, match="passes through a link"):
            installer.install(pending)

        assert list(tmp_path.iterdir()) == [], "Expected no writes after path changed"
