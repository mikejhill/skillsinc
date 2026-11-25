"""Fixtures for the tray: an offscreen Qt platform and a controller harness."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

# Before Qt loads: no display is needed, on any CI runner
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from pytestqt.qtbot import QtBot
from tests.conftest import Builder, Sandbox

from skillsinc.history import SyncHistory, SyncRecord
from skillsinc.jsonc import Jsonc
from skillsinc.paths import Environment
from skillsinc.tray.controller import Notice, TrayController
from skillsinc.tray.runner import SyncRunner
from skillsinc.tray.startup import LoginItem
from skillsinc.usersettings import ScheduleOptions, SettingsStore, UserSettings

SYNC_TIMEOUT_MS = 30_000


def child_environment(environment: Environment) -> dict[str, str]:
    """Return variables that point a child ``skillsinc`` at ``environment``."""
    variables = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("XDG_", "SKILLSINC_"))
    }
    variables.update(environment.variables)
    variables["HOME"] = str(environment.home)
    variables["USERPROFILE"] = str(environment.home)
    return variables


SENDER = """
import sys, time
from PySide6.QtNetwork import QLocalSocket
name, delay, pieces = sys.argv[1], float(sys.argv[2]), sys.argv[3:]
time.sleep(delay)
socket = QLocalSocket()
socket.connectToServer(name)
if not socket.waitForConnected(5000):
    sys.exit(1)
for piece in pieces:
    socket.write(piece.encode("utf-8"))
    socket.flush()
    socket.waitForBytesWritten(2000)
    time.sleep(0.1)
socket.waitForDisconnected(5000)
"""
FORWARDER = """
import sys, time
from skillsinc.tray.instance import SingleInstance
time.sleep(float(sys.argv[2]))
sys.exit(0 if SingleInstance(sys.argv[1]).forward(sys.argv[3:]) else 1)
"""

LISTENER = """
import json, sys
from PySide6.QtCore import QCoreApplication
from skillsinc.tray.instance import SingleInstance
application = QCoreApplication([])
instance = SingleInstance(sys.argv[1])
if not instance.listen():
    sys.exit(1)
def received(arguments):
    print(json.dumps(arguments), flush=True)
    application.quit()
instance.message_received.connect(received)
print("ready", flush=True)
application.exec()
instance.close()
"""


def listen_in_process(name: str) -> subprocess.Popen[str]:
    """Start a listener on ``name`` in another process; return once it listens.

    It prints the first message it receives as JSON, then exits.
    """
    listener = subprocess.Popen(  # noqa: S603 - argv is built by the test
        [sys.executable, "-c", LISTENER, name],
        stdout=subprocess.PIPE,
        encoding="utf-8",
    )
    assert listener.stdout is not None
    assert listener.stdout.readline() == "ready\n"
    return listener


def send_from_process(
    name: str, pieces: Sequence[str], *, delay: float = 0.0, raw: bool = False
) -> subprocess.Popen[bytes]:
    """Send to the tray socket ``name`` from another process, as a launch does.

    ``raw`` writes ``pieces`` as they are; otherwise they are the arguments a
    launch forwards. Another process, because a sender blocks in Qt calls that
    hold the interpreter lock, which would stall a listener on this process.
    """
    script = SENDER if raw else FORWARDER
    return subprocess.Popen(  # noqa: S603 - argv is built by the test
        [sys.executable, "-c", script, name, str(delay), *pieces]
    )


def add_profiles(config: Path, profiles: dict[str, dict[str, object]]) -> None:
    """Give ``config`` these profiles and tag its first route with the first one."""
    text = config.read_text(encoding="utf-8")
    data = json.loads(Jsonc.blank_trailing_commas(Jsonc.blank_comments(text)))
    data["profiles"] = profiles
    data["routes"][0]["profiles"] = [next(iter(profiles))]
    config.write_text(json.dumps(data, indent=2), encoding="utf-8")


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


@dataclass
class Tray:
    """A started controller over a sandbox, with what it opened and notified."""

    controller: TrayController
    sandbox: Sandbox
    qtbot: QtBot
    opened: list[Path] = field(default_factory=list)
    notices: list[Notice] = field(default_factory=list)
    quit_calls: list[bool] = field(default_factory=list)

    def history(self) -> list[SyncRecord]:
        """Return the sandbox's recorded syncs, newest first."""
        return SyncHistory(
            self.sandbox.environment.state_directory, keep=1000
        ).records()

    def wait_for_sync(self, action: object = None) -> SyncRecord | None:
        """Wait for the next sync to end; return its record."""
        with self.qtbot.waitSignal(
            self.controller.synced, timeout=SYNC_TIMEOUT_MS
        ) as blocker:
            if callable(action):
                action()
        record = blocker.args[0] if blocker.args else None
        return record if isinstance(record, SyncRecord) else None


def make_tray(  # noqa: PLR0913 - every collaborator a test may replace
    qtbot: QtBot,
    sandbox: Sandbox,
    settings: UserSettings | None = None,
    *,
    start: bool = True,
    runner: SyncRunner | None = None,
    login_item: LoginItem | None = None,
    opens: bool = True,
    save: bool = True,
) -> Tray:
    """Return a tray over ``sandbox`` with ``settings`` saved first.

    ``opens`` is what the desktop answers when asked to open a file; ``save``
    False leaves ``settings.json`` as it is on disk.
    """
    store = SettingsStore.default(sandbox.environment)
    if save:
        store.save(
            settings
            or UserSettings(schedule=ScheduleOptions(enabled=False, run_at_start=False))
        )
    runner = runner or SyncRunner(
        sandbox.config_path,
        program=[sys.executable, "-P", "-m", "skillsinc"],
        environment=child_environment(sandbox.environment),
    )
    opened: list[Path] = []
    quits: list[bool] = []

    def opener(path: Path) -> bool:
        opened.append(path)
        return opens

    controller = TrayController(
        sandbox.environment,
        store=store,
        runner=runner,
        opener=opener,
        login_item=login_item
        or LoginItem(sandbox.environment, ["skillsinc-tray"], FakeRunKey()),
        quit_application=lambda: quits.append(True),
    )
    tray = Tray(controller, sandbox, qtbot, opened, [], quits)
    controller.notified.connect(tray.notices.append)
    if start:
        controller.start()
    return tray


@pytest.fixture
def configured(sandbox: Sandbox) -> Sandbox:
    """A sandbox with a single generic destination configured."""
    Builder.write_config(
        sandbox.config_path, sandbox.library, (("target", "generic", sandbox.root),)
    )
    return sandbox


@pytest.fixture
def tray(qtbot: QtBot, configured: Sandbox) -> Iterator[Tray]:
    """A started tray with scheduling off."""
    harness = make_tray(qtbot, configured)
    yield harness
    harness.controller.stop()
    harness.controller.popup.close()
    harness.controller.history_window.close()
