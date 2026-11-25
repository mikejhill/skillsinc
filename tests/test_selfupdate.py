"""Finding newer releases and planning how to install them."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from skillsinc import selfupdate
from skillsinc.selfupdate import (
    PYPI_URL,
    Installation,
    Release,
    ReleaseFeed,
    UpdateError,
    UpdateScript,
)


def _feed(body: bytes | Exception) -> ReleaseFeed:
    def fetch(url: str) -> bytes:
        assert url == PYPI_URL
        if isinstance(body, Exception):
            raise body
        return body

    return ReleaseFeed(fetch)


class TestReleaseFeed:
    """PyPI's newest version, compared with the running one."""

    @pytest.mark.parametrize(
        ("latest", "current", "newer"),
        [
            ("0.10.0", "0.9.0", True),
            ("0.9.0", "0.9.0", False),
            ("0.8.1", "0.9.0", False),
        ],
    )
    def test_newer_compares_release_numbers(
        self, latest: str, current: str, *, newer: bool
    ) -> None:
        """0.10.0 is newer than 0.9.0; equal and older are not."""
        body = json.dumps({"info": {"version": latest}}).encode()

        release = _feed(body).latest(current)

        assert release == Release(latest, current)
        assert release.newer is newer

    @pytest.mark.parametrize(
        "body",
        [
            OSError("offline"),
            b"not json",
            b"[]",
            b'{"info": {}}',
            b'{"info": {"version": "unknown"}}',
        ],
    )
    def test_unexpected_answers_are_update_errors(
        self, body: bytes | Exception
    ) -> None:
        """Network failures and odd answers say the check failed."""
        with pytest.raises(UpdateError, match="could not check for updates"):
            _feed(body).latest("0.9.0")

    def test_fetch_url_reads_a_bounded_body(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The default fetch sends a plain GET and reads the body."""

        class Response:
            def __enter__(self) -> Response:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self, limit: int) -> bytes:
                assert limit == selfupdate.MAXIMUM_RESPONSE_BYTES
                return b"{}"

        seen: list[object] = []

        def urlopen(request: object, timeout: float) -> Response:
            seen.append((request, timeout))
            return Response()

        monkeypatch.setattr(selfupdate.urllib.request, "urlopen", urlopen)

        assert selfupdate.fetch_url(PYPI_URL) == b"{}"
        assert seen[0][1] == selfupdate.TIMEOUT_SECONDS  # ty: ignore[not-subscriptable]


class TestInstallation:
    """How SkillSinc was installed decides the upgrade command."""

    def test_uv_tool(self, tmp_path: Path) -> None:
        """A uv tool environment upgrades with uv, keeping its extras."""
        (tmp_path / "uv-receipt.toml").write_text("", encoding="utf-8")

        found = Installation.detect(tmp_path, which=lambda name: f"/bin/{name}")

        assert found == Installation("uv", ("/bin/uv", "tool", "upgrade", "skillsinc"))
        assert found.can_upgrade

    def test_pipx(self, tmp_path: Path) -> None:
        """A pipx environment upgrades with pipx."""
        (tmp_path / "pipx_metadata.json").write_text("{}", encoding="utf-8")

        found = Installation.detect(tmp_path, which=lambda name: f"/bin/{name}")

        assert found.command == ("/bin/pipx", "upgrade", "skillsinc")

    def test_tool_missing_from_path(self, tmp_path: Path) -> None:
        """Without the tool on PATH the user is told what to run."""
        (tmp_path / "uv-receipt.toml").write_text("", encoding="utf-8")

        found = Installation.detect(tmp_path, which=lambda _name: None)

        assert not found.can_upgrade
        assert "uv tool upgrade" in found.reason

    def test_virtual_environment_uses_pip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A plain virtual environment upgrades with its own pip."""
        monkeypatch.setattr(Installation, "_editable", staticmethod(lambda: False))
        python = tmp_path / "bin" / "python"

        found = Installation.detect(tmp_path, python, tmp_path / "base")

        assert found.command == (
            str(python),
            "-m",
            "pip",
            "install",
            "--upgrade",
            "skillsinc[tray]",
        )

    def test_system_python_and_working_copy_are_manual(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A system Python or an editable checkout is not upgraded by SkillSinc."""
        monkeypatch.setattr(Installation, "_editable", staticmethod(lambda: False))
        system = Installation.detect(tmp_path, tmp_path / "python", tmp_path)
        monkeypatch.setattr(Installation, "_editable", staticmethod(lambda: True))
        checkout = Installation.detect(tmp_path, tmp_path / "python", tmp_path / "b")

        assert (system.kind, checkout.kind) == ("manual", "manual")
        assert "system Python" in system.reason
        assert "working copy" in checkout.reason

    @pytest.mark.parametrize(
        ("text", "editable"),
        [
            (None, False),
            ("", False),
            ("not json", False),
            ('{"url": "file:///x"}', False),
            ('{"dir_info": {"editable": true}}', True),
        ],
    )
    def test_editable_reads_direct_url(
        self, monkeypatch: pytest.MonkeyPatch, text: str | None, *, editable: bool
    ) -> None:
        """Only a direct_url.json marking the install editable counts."""

        class Distribution:
            def read_text(self, name: str) -> str | None:
                assert name == "direct_url.json"
                return text

        monkeypatch.setattr(selfupdate, "distribution", lambda _name: Distribution())

        assert Installation._editable() is editable


class TestUpdateScript:
    """The script waits, upgrades into a log, and restarts the tray."""

    def test_posix_script(self, tmp_path: Path) -> None:
        """A shell script that sleeps, upgrades, then starts the tray."""
        script = UpdateScript.write(
            tmp_path / "state",
            ["/bin/uv", "tool", "upgrade", "skillsinc"],
            ["/py/python", "-m", "skillsinc", "tray"],
            "linux",
        )

        assert script.program == "/bin/sh"
        assert script.arguments == (str(script.path),)
        assert "sleep 3" in script.text
        assert "/bin/uv tool upgrade skillsinc >>" in script.text
        assert "nohup /py/python -m skillsinc tray" in script.text
        assert script.path.read_text(encoding="utf-8") == script.text

    def test_windows_script(self, tmp_path: Path) -> None:
        """A batch file that waits, upgrades, then starts the tray."""
        script = UpdateScript.write(
            tmp_path,
            ["C:\\uv\\uv.exe", "tool", "upgrade", "skillsinc"],
            ["C:\\Program Files\\pythonw.exe", "-m", "skillsinc", "tray"],
            "win32",
        )

        assert script.program == "cmd.exe"
        assert script.arguments == ("/c", str(script.path))
        assert script.path.suffix == ".cmd"
        assert 'start "" "C:\\Program Files\\pythonw.exe" -m skillsinc tray' in (
            script.text
        )


def test_running_python_is_detected() -> None:
    """Detection works on this interpreter without arguments."""
    found = Installation.detect()

    assert found.kind in {"uv", "pipx", "pip", "manual"}
    assert sys.executable
