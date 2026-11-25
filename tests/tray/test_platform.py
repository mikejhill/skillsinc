"""Sign-in start, the single-instance socket, and the profile catalog."""

from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from tests.conftest import Sandbox
from tests.tray.conftest import add_profiles

from skillsinc.paths import Environment
from skillsinc.tray.catalog import CatalogReader
from skillsinc.tray.instance import SingleInstance
from skillsinc.tray.startup import VALUE_NAME, LoginItem, tray_command

COMMAND = ["C:\\Program Files\\Python\\pythonw.exe", "-P", "-m", "skillsinc", "tray"]


class FakeRunKey:
    """An in-memory Run key."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def read(self, name: str) -> str | None:
        """Return the named value."""
        return self.values.get(name)

    def write(self, name: str, value: str) -> None:
        """Set the named value."""
        self.values[name] = value

    def delete(self, name: str) -> None:
        """Remove the named value."""
        self.values.pop(name, None)


class TestLoginItem:
    """Start at sign-in: a Run value on Windows, an autostart entry on Linux."""

    def test_windows_run_value(self, environment: Environment) -> None:
        """Enabling writes the quoted command; disabling removes it."""
        key = FakeRunKey()
        item = LoginItem(replace(environment, platform="win32"), COMMAND, key)

        assert item.supported
        assert item.apply(enabled=True)
        assert key.values[VALUE_NAME] == (
            '"C:\\Program Files\\Python\\pythonw.exe" -P -m skillsinc tray'
        )
        assert item.enabled()
        item.apply(enabled=False)
        assert VALUE_NAME not in key.values
        assert not item.enabled()

    def test_windows_stale_command_is_not_enabled(
        self, environment: Environment
    ) -> None:
        """A Run value for another interpreter counts as not enabled."""
        key = FakeRunKey()
        key.values[VALUE_NAME] = "old.exe"
        item = LoginItem(replace(environment, platform="win32"), COMMAND, key)

        assert not item.enabled()

    def test_linux_autostart_entry(self, environment: Environment) -> None:
        """Enabling writes a desktop entry; disabling deletes it."""
        item = LoginItem(
            replace(environment, platform="linux"),
            ["/opt/my python/bin/python", "-m", "skillsinc", "tray"],
        )
        entry = environment.config_home / "autostart" / "skillsinc-tray.desktop"

        assert item.apply(enabled=True)
        text = entry.read_text(encoding="utf-8")
        assert 'Exec="/opt/my python/bin/python" -m skillsinc tray' in text
        assert item.enabled()
        entry.write_text(text + "# edited\n", encoding="utf-8")
        assert not item.enabled(), "Expected an edited entry to count as stale"
        item.apply(enabled=False)
        assert not entry.exists()

    def test_macos_is_unsupported(self, environment: Environment) -> None:
        """Nothing is written where sign-in start is not implemented."""
        item = LoginItem(replace(environment, platform="darwin"), COMMAND)

        assert not item.supported
        assert not item.apply(enabled=True)
        assert not item.enabled()

    def test_tray_command_uses_the_windowless_python(self, tmp_path: Path) -> None:
        """On Windows the command uses pythonw.exe beside python.exe."""
        (tmp_path / "python.exe").touch()
        (tmp_path / "pythonw.exe").touch()

        command = tray_command(tmp_path / "python.exe", "win32")

        assert command == [
            str(tmp_path / "pythonw.exe"),
            "-P",
            "-m",
            "skillsinc",
            "tray",
        ]


class TestSingleInstance:
    """A second launch forwards its request to the first."""

    def test_forward_reaches_the_listener(self, qtbot: QtBot) -> None:
        """Arguments arrive intact at the running instance."""
        name = f"skillsinc-test-{uuid.uuid4().hex}"
        first = SingleInstance(name)
        assert first.listen()
        try:
            with qtbot.waitSignal(first.message_received, timeout=5000) as blocker:
                assert SingleInstance(name).forward(["sync", "work"])
            assert blocker.args == [["sync", "work"]]
        finally:
            first.close()

    def test_nobody_listening(self, qtbot: QtBot) -> None:
        """Forwarding with no running instance reports False."""
        del qtbot
        assert not SingleInstance(f"skillsinc-test-{uuid.uuid4().hex}").forward([])

    def test_malformed_message_is_ignored(self, qtbot: QtBot) -> None:
        """Garbage on the socket emits nothing."""
        from PySide6.QtNetwork import QLocalSocket  # noqa: PLC0415

        name = f"skillsinc-test-{uuid.uuid4().hex}"
        first = SingleInstance(name)
        assert first.listen()
        received: list[object] = []
        first.message_received.connect(received.append)
        try:
            socket = QLocalSocket()
            socket.connectToServer(name)
            assert socket.waitForConnected(2000)
            socket.write(b"{not json")
            socket.flush()
            socket.waitForBytesWritten(2000)
            socket.disconnectFromServer()
            qtbot.wait(300)
            assert received == []
        finally:
            first.close()
            first.close()
        assert first.name == name

    def test_non_list_message_is_ignored(self, qtbot: QtBot) -> None:
        """Valid JSON that is not an argument list emits nothing."""
        from PySide6.QtNetwork import QLocalSocket  # noqa: PLC0415

        name = f"skillsinc-test-{uuid.uuid4().hex}"
        first = SingleInstance(name)
        assert first.listen()
        received: list[object] = []
        first.message_received.connect(received.append)
        try:
            socket = QLocalSocket()
            socket.connectToServer(name)
            assert socket.waitForConnected(2000)
            socket.write(b'{"sync": true}')
            socket.flush()
            socket.waitForBytesWritten(2000)
            socket.disconnectFromServer()
            qtbot.wait(300)
            assert received == []
        finally:
            first.close()

    def test_unusable_name_cannot_listen(self, qtbot: QtBot, tmp_path: Path) -> None:
        """A name the system refuses leaves the launch without a tray."""
        del qtbot
        instance = SingleInstance(str(tmp_path / "missing" / "socket"))

        assert not instance.listen()
        instance._on_connection()

    def test_name_is_per_user_and_state_folder(
        self, environment: Environment, tmp_path: Path
    ) -> None:
        """Two homes get two trays; one home always the same one."""
        other = replace(
            environment,
            variables={**environment.variables, "XDG_STATE_HOME": str(tmp_path / "x")},
        )

        assert SingleInstance.name_for(environment) == SingleInstance.name_for(
            environment
        )
        assert SingleInstance.name_for(environment) != SingleInstance.name_for(other)
        assert SingleInstance.name_for(environment).startswith("SkillSinc-tray-")


class TestCatalog:
    """The tray reads profiles in-process, without running a sync."""

    def test_profiles_and_active_state(self, configured: Sandbox) -> None:
        """Each profile is listed with whether it is active here."""
        add_profiles(
            configured.config_path,
            {"work": {"description": "Work"}, "home": {"default": False}},
        )

        catalog = CatalogReader(configured.environment).profiles(configured.config_path)

        assert catalog.defined
        assert [(p.name, p.active) for p in catalog.profiles] == [
            ("work", True),
            ("home", False),
        ]
        assert catalog.active == ("work",)

    def test_no_profiles(self, configured: Sandbox) -> None:
        """A configuration without profiles has an empty catalog."""
        catalog = CatalogReader(configured.environment).profiles(configured.config_path)

        assert (catalog.defined, catalog.profiles, catalog.error) == (False, (), "")

    def test_broken_configuration(self, sandbox: Sandbox) -> None:
        """A configuration error is reported, not raised."""
        catalog = CatalogReader(sandbox.environment).profiles(sandbox.config_path)

        assert "config" in catalog.error.lower() or catalog.error, catalog

    @pytest.mark.parametrize("limit", [1, 5])
    def test_history_is_limited(self, configured: Sandbox, limit: int) -> None:
        """History reads stop at the limit."""
        for _ in range(3):
            configured.run("sync")

        records = CatalogReader(configured.environment).history(
            configured.config_path, limit
        )

        assert len(records) == min(limit, 3)
