"""Finding newer releases and planning how to install them."""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from skillsinc import selfupdate
from skillsinc.selfupdate import (
    PYPI_URL,
    RESULT_FILE,
    Installation,
    Release,
    ReleaseFeed,
    UpdateError,
    UpdateHelper,
    UpdateResult,
    base_interpreter,
    is_newer,
    release_key,
)


def _feed(body: bytes | Exception) -> ReleaseFeed:
    def fetch(url: str) -> bytes:
        assert url == PYPI_URL
        if isinstance(body, Exception):
            raise body
        return body

    return ReleaseFeed(fetch)


class TestVersions:
    """PyPI versions compare the way pip orders them."""

    @pytest.mark.parametrize(
        ("older", "newer"),
        [
            ("0.9.0", "0.10.0"),
            ("1.2.0rc1", "1.2.0"),
            ("1.2.0", "1.2.0.post1"),
            ("1.2.0.dev1", "1.2.0a1"),
            ("1.2.0a2", "1.2.0b1"),
            ("1.2.0b1", "1.2.0rc1"),
            ("1.2.0", "1.2.1.dev0"),
            ("1.2.0.post1.dev1", "1.2.0.post1"),
            ("1.2", "1.2.0.1"),
        ],
    )
    def test_order(self, older: str, newer: str) -> None:
        """Pre-releases, post-releases, and dev releases sort as PEP 440 says."""
        assert is_newer(newer, older)
        assert not is_newer(older, newer)

    @pytest.mark.parametrize(
        ("left", "right"), [("1.2", "1.2.0"), ("1.0-1", "1.0.post1"), ("v1.0", "1.0")]
    )
    def test_equal_spellings(self, left: str, right: str) -> None:
        """Different spellings of one version are equal."""
        assert release_key(left) == release_key(right)

    def test_unknown_text_is_not_a_version(self) -> None:
        """Anything else has no key; an unknown running version is always older."""
        assert release_key("unknown") is None
        assert is_newer("1.0", "unknown")
        assert not is_newer("unknown", "1.0")


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
            http.client.IncompleteRead(b""),
            b"not json",
            b"[]",
            b'{"info": {}}',
            b'{"info": {"version": "unknown"}}',
        ],
    )
    def test_unexpected_answers_are_update_errors(
        self, body: bytes | Exception
    ) -> None:
        """Network failures, broken HTTP, and odd answers say the check failed."""
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

    @pytest.fixture(autouse=True)
    def not_editable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Treat this test run's checkout as an ordinary install."""
        monkeypatch.setattr(Installation, "_editable", staticmethod(lambda: False))

    def test_uv_tool(self, tmp_path: Path) -> None:
        """A uv tool environment upgrades with uv, aimed at its tool folder."""
        prefix = tmp_path / "tools" / "skillsinc"
        prefix.mkdir(parents=True)
        (prefix / "uv-receipt.toml").write_text("", encoding="utf-8")

        found = Installation.detect(prefix, which=lambda name: f"/bin/{name}")

        assert found == Installation(
            "uv",
            ("/bin/uv", "tool", "upgrade", "skillsinc"),
            environment=(("UV_TOOL_DIR", str(tmp_path / "tools")),),
        )
        assert found.can_upgrade

    def test_pipx(self, tmp_path: Path) -> None:
        """A pipx environment upgrades with pipx, aimed at its home."""
        prefix = tmp_path / "pipx" / "venvs" / "skillsinc"
        prefix.mkdir(parents=True)
        (prefix / "pipx_metadata.json").write_text("{}", encoding="utf-8")

        found = Installation.detect(prefix, which=lambda name: f"/bin/{name}")

        assert found.command == ("/bin/pipx", "upgrade", "skillsinc")
        assert found.environment == (("PIPX_HOME", str(tmp_path / "pipx")),)

    @pytest.mark.parametrize(
        ("parts", "marker"),
        [
            (("tools", "other"), "uv-receipt.toml"),
            (("pipx", "elsewhere", "skillsinc"), "pipx_metadata.json"),
        ],
    )
    def test_unexpected_tool_layout_is_manual(
        self, tmp_path: Path, parts: tuple[str, ...], marker: str
    ) -> None:
        """An environment the tool would not find by name is left to the user."""
        prefix = tmp_path.joinpath(*parts)
        prefix.mkdir(parents=True)
        (prefix / marker).write_text("", encoding="utf-8")

        found = Installation.detect(prefix, which=lambda name: f"/bin/{name}")

        assert not found.can_upgrade
        assert "is not where" in found.reason

    def test_tool_missing_from_path(self, tmp_path: Path) -> None:
        """Without the tool on PATH the user is told what to run."""
        prefix = tmp_path / "skillsinc"
        prefix.mkdir()
        (prefix / "uv-receipt.toml").write_text("", encoding="utf-8")

        found = Installation.detect(prefix, which=lambda _name: None)

        assert not found.can_upgrade
        assert "uv tool upgrade skillsinc" in found.reason

    def test_virtual_environment_uses_pip(self, tmp_path: Path) -> None:
        """A plain virtual environment upgrades with its own pip."""
        python = tmp_path / "bin" / "python"

        found = Installation.detect(
            tmp_path, python, tmp_path / "base", has_pip=lambda: True
        )

        assert found.command == (
            str(python),
            "-m",
            "pip",
            "install",
            "--upgrade",
            "skillsinc[tray]",
        )

    def test_virtual_environment_without_pip_is_manual(self, tmp_path: Path) -> None:
        """A uv venv has no pip; the user is told how to upgrade instead."""
        found = Installation.detect(
            tmp_path, tmp_path / "python", tmp_path / "base", has_pip=lambda: False
        )

        assert not found.can_upgrade
        assert "uv pip install --upgrade" in found.reason

    def test_system_python_is_manual(self, tmp_path: Path) -> None:
        """A system Python is not upgraded by SkillSinc."""
        found = Installation.detect(tmp_path, tmp_path / "python", tmp_path)

        assert found.kind == "manual"
        assert "system Python" in found.reason

    def test_working_copy_wins_over_tool_markers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An editable install in a uv tool environment is still a working copy."""
        monkeypatch.setattr(Installation, "_editable", staticmethod(lambda: True))
        prefix = tmp_path / "skillsinc"
        prefix.mkdir()
        (prefix / "uv-receipt.toml").write_text("", encoding="utf-8")

        found = Installation.detect(prefix, which=lambda name: f"/bin/{name}")

        assert found.kind == "manual"
        assert "working copy" in found.reason

    def test_pip_probe_asks_the_import_system(self) -> None:
        """The default probe looks pip up without importing it."""
        expected = selfupdate.importlib.util.find_spec("pip") is not None

        assert selfupdate._has_pip() is expected

    def test_running_python_is_detected(self) -> None:
        """Detection works on this interpreter without arguments."""
        assert Installation.detect().kind in {"uv", "pipx", "pip", "manual"}


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
    monkeypatch: pytest.MonkeyPatch, text: str | None, *, editable: bool
) -> None:
    """Only a direct_url.json marking the install editable counts."""

    class Distribution:
        def read_text(self, name: str) -> str | None:
            assert name == "direct_url.json"
            return text

    monkeypatch.setattr(selfupdate, "distribution", lambda _name: Distribution())

    assert Installation._editable() is editable


class TestBaseInterpreter:
    """The helper runs outside the environment it upgrades."""

    def test_base_interpreter_when_it_exists(self, tmp_path: Path) -> None:
        """The base executable is used when present."""
        base = tmp_path / "python"
        base.write_text("", encoding="utf-8")

        assert base_interpreter("/venv/python", str(base)) == str(base)

    def test_falls_back_to_the_running_interpreter(self, tmp_path: Path) -> None:
        """Without a base executable, the running one is used."""
        missing = str(tmp_path / "gone")

        assert base_interpreter("/venv/python", missing) == "/venv/python"
        assert base_interpreter()


class TestHelper:
    """The helper waits for the tray, upgrades into a log, records, relaunches."""

    @pytest.fixture
    def dead_pid(self) -> int:
        """A process id that no longer runs."""
        process = subprocess.Popen([sys.executable, "-c", "pass"])
        process.wait()
        return process.pid

    @staticmethod
    def _run(
        folder: Path, upgrade: list[str], relaunch: list[str], pid: int
    ) -> subprocess.CompletedProcess[str]:
        installation = Installation("uv", tuple(upgrade), environment=(("MARK", "1"),))
        helper = UpdateHelper.plan(
            installation, relaunch, folder, "9.0.0", pid=pid, python=sys.executable
        )
        assert helper.program == sys.executable
        return subprocess.run(  # noqa: S603 - argv built by the test
            [helper.program, *helper.arguments],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    @staticmethod
    def _wait_for(path: Path) -> None:
        deadline = time.monotonic() + 30
        while not path.exists():
            assert time.monotonic() < deadline, f"{path} never appeared"
            time.sleep(0.1)

    def test_upgrade_is_logged_recorded_and_the_tray_restarts(
        self, tmp_path: Path, dead_pid: int
    ) -> None:
        """Arguments with shell and batch metacharacters pass through untouched."""
        marker = tmp_path / "relaunched & 100%.txt"
        upgrade = [
            sys.executable,
            "-c",
            "import os, sys; print('upgraded', sys.argv[1], os.environ['MARK'])",
            "a&b %PATH% 'q'",
        ]
        relaunch = [
            sys.executable,
            "-c",
            "import sys; open(sys.argv[1], 'w').close()",
            str(marker),
        ]

        done = self._run(tmp_path, upgrade, relaunch, dead_pid)

        assert done.returncode == 0, done.stderr
        log = (tmp_path / "update.log").read_text(encoding="utf-8")
        assert "upgraded a&b %PATH% 'q' 1" in log
        assert UpdateResult.read(tmp_path) == UpdateResult(
            "9.0.0", 0, "", reported=False
        )
        self._wait_for(marker)

    def test_failed_upgrade_is_recorded(self, tmp_path: Path, dead_pid: int) -> None:
        """A failing installer's exit code is kept for the tray to report."""
        upgrade = [sys.executable, "-c", "raise SystemExit(3)"]

        done = self._run(tmp_path, upgrade, [sys.executable, "-c", "pass"], dead_pid)

        assert done.returncode == 0, done.stderr
        result = UpdateResult.read(tmp_path)
        assert result is not None
        assert (result.returncode, result.succeeded) == (3, False)

    def test_missing_installer_is_recorded(self, tmp_path: Path, dead_pid: int) -> None:
        """An installer that cannot start is recorded with why."""
        upgrade = [str(tmp_path / "no-such-tool")]

        self._run(tmp_path, upgrade, [sys.executable, "-c", "pass"], dead_pid)

        result = UpdateResult.read(tmp_path)
        assert result is not None
        assert result.returncode is None
        assert result.note

    def test_a_tray_that_does_not_exit_stops_the_upgrade(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """While the tray still runs, nothing is upgraded or restarted."""
        monkeypatch.setattr(selfupdate, "EXIT_WAIT_SECONDS", 0.5)
        flag = tmp_path / "upgraded"
        upgrade = [sys.executable, "-c", f"open({str(flag)!r}, 'w').close()"]
        relaunch = [sys.executable, "-c", "pass"]

        done = self._run(tmp_path, upgrade, relaunch, os.getpid())

        assert done.returncode == 1
        assert not flag.exists()
        result = UpdateResult.read(tmp_path)
        assert result is not None
        assert result.note == "the tray did not exit"

    @pytest.mark.skipif(sys.platform == "win32", reason="links need privileges")
    def test_linked_log_is_not_followed(self, tmp_path: Path, dead_pid: int) -> None:
        """A link at update.log is left alone; the upgrade still runs."""
        target = tmp_path / "victim.txt"
        target.write_text("keep", encoding="utf-8")
        (tmp_path / "update.log").symlink_to(target)
        upgrade = [sys.executable, "-c", "print('upgraded')"]

        self._run(tmp_path, upgrade, [sys.executable, "-c", "pass"], dead_pid)

        assert target.read_text(encoding="utf-8") == "keep"
        assert UpdateResult.read(tmp_path) is not None

    @pytest.mark.skipif(sys.platform == "win32", reason="FIFOs are POSIX")
    def test_fifo_log_does_not_block(self, tmp_path: Path, dead_pid: int) -> None:
        """A FIFO at update.log is refused at once."""
        os.mkfifo(tmp_path / "update.log")
        upgrade = [sys.executable, "-c", "print('upgraded')"]

        done = self._run(tmp_path, upgrade, [sys.executable, "-c", "pass"], dead_pid)

        assert done.returncode == 0
        assert UpdateResult.read(tmp_path) is not None


class TestUpdateResult:
    """Reading, marking, and clearing the helper's record."""

    def test_mark_reported_then_clear(self, tmp_path: Path) -> None:
        """A reported failure stays recorded until cleared."""
        (tmp_path / RESULT_FILE).write_text(
            json.dumps({"version": "9.0.0", "returncode": 2, "note": ""}),
            encoding="utf-8",
        )

        UpdateResult.mark_reported(tmp_path)
        assert UpdateResult.read(tmp_path) == UpdateResult(
            "9.0.0", 2, "", reported=True
        )
        UpdateResult.clear(tmp_path)

        assert UpdateResult.read(tmp_path) is None
        UpdateResult.mark_reported(tmp_path)
        assert not (tmp_path / RESULT_FILE).exists()

    @pytest.mark.parametrize(
        "text",
        ["not json", "[]", '{"returncode": 0}', '{"version": "1", "returncode": true}'],
    )
    def test_odd_records(self, tmp_path: Path, text: str) -> None:
        """Unreadable records are ignored; a non-integer exit code reads as none."""
        (tmp_path / RESULT_FILE).write_text(text, encoding="utf-8")

        result = UpdateResult.read(tmp_path)

        assert result is None or result.returncode is None

    def test_missing_record(self, tmp_path: Path) -> None:
        """No record, no result."""
        assert UpdateResult.read(tmp_path) is None
