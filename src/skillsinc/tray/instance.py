"""One tray per user: later launches forward their request to the running one."""

from __future__ import annotations

import getpass
import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from PySide6.QtCore import (
    QCoreApplication,
    QDeadlineTimer,
    QLockFile,
    QObject,
    QThread,
    Signal,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from skillsinc.paths import Environment

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_MS: Final = 500
# How long a sender waits for the running tray to confirm it read a message
RECEIPT_TIMEOUT_MS: Final = 2000
TERMINATOR: Final = b"\n"
RETRY_MS: Final = 100


class SingleInstance(QObject):
    """A local socket named for the user; its owner is the running tray."""

    message_received = Signal(list)  # list[str]: the forwarded arguments

    def __init__(self, name: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._name = name or f"SkillSinc-tray-{getpass.getuser()}"
        self._server: QLocalServer | None = None
        self._lock: QLockFile | None = None

    @staticmethod
    def name_for(environment: Environment) -> str:
        """Return the socket name for ``environment``'s user and state folder.

        One tray runs per state folder, so a test home never reaches the
        user's own tray.
        """
        folder = str(environment.state_directory.resolve()).encode("utf-8")
        digest = hashlib.sha256(folder).hexdigest()[:12]
        return f"SkillSinc-tray-{getpass.getuser()}-{digest}"

    @property
    def name(self) -> str:
        """Return the local socket name."""
        return self._name

    def forward(self, arguments: Sequence[str]) -> bool:
        """Send ``arguments`` to a running tray; True if one received them.

        The message ends with a newline, and the sender waits for the tray to
        hang up once it has read it: on Windows, data still in the pipe when
        the sender closes first can be lost.
        """
        socket = QLocalSocket()
        socket.connectToServer(self._name)
        if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
            return False
        socket.write(json.dumps(list(arguments)).encode("utf-8") + TERMINATOR)
        socket.flush()
        socket.waitForBytesWritten(CONNECT_TIMEOUT_MS)
        unconnected = QLocalSocket.LocalSocketState.UnconnectedState
        if socket.state() != unconnected:  # pragma: no branch - unless it hung up
            socket.waitForDisconnected(RECEIPT_TIMEOUT_MS)
        socket.abort()
        return True

    def forward_when_ready(self, arguments: Sequence[str], wait_ms: int) -> bool:
        """Forward ``arguments`` once a starting tray listens, within ``wait_ms``."""
        deadline = QDeadlineTimer(wait_ms)
        while True:
            if self.forward(arguments):
                return True
            if deadline.hasExpired():
                return False
            QThread.msleep(RETRY_MS)

    def listen(self, lock: Path | None = None) -> bool:
        """Become the running tray; False if another holds ``lock`` or the name.

        The lock file serializes launches, so a stale socket is removed only
        by the launch that will listen on it.
        """
        if lock is not None:
            claim = QLockFile(str(lock))
            claim.setStaleLockTime(0)  # stale only when its process is gone
            if not claim.tryLock(0):
                return False
            self._lock = claim
        QLocalServer.removeServer(self._name)
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(self._name):
            logger.warning(
                "Could not listen on %s: %s", self._name, server.errorString()
            )
            self.close()
            return False
        server.newConnection.connect(self._on_connection)
        self._server = server
        return True

    def close(self) -> None:
        """Stop listening."""
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._lock is not None:
            self._lock.unlock()
            self._lock = None

    def _on_connection(self) -> None:
        server = self._server
        if server is None:
            return
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            message = _Message()
            socket.readyRead.connect(lambda s=socket, m=message: self._receive(s, m))
            socket.disconnected.connect(lambda s=socket, m=message: self._finish(s, m))
            # Anything that arrived before the signals connected
            self._receive(socket, message)

    def _receive(self, socket: QLocalSocket, message: _Message) -> None:
        """Read what arrived; deliver and hang up once the message is complete."""
        message.data += bytes(socket.readAll().data())
        if message.delivered or TERMINATOR not in message.data:
            return
        message.delivered = True
        self._deliver(bytes(message.data.split(TERMINATOR, 1)[0]))
        socket.disconnectFromServer()

    def _finish(self, socket: QLocalSocket, message: _Message) -> None:
        """Deliver an unterminated message, then let the socket go."""
        message.data += bytes(socket.readAll().data())
        if not message.delivered:
            message.delivered = True
            self._deliver(bytes(message.data))
        # Keep the socket alive until deferred deletion; Windows pipe
        # callbacks can still be queued after it disconnects
        socket.setParent(QCoreApplication.instance())
        socket.deleteLater()

    def _deliver(self, data: bytes) -> None:
        try:
            decoded: object = json.loads(data.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            logger.warning("Ignoring a malformed message from another launch")
            return
        if isinstance(decoded, list):
            self.message_received.emit([str(item) for item in decoded])


@dataclass
class _Message:
    """One launch's message, as it arrives."""

    data: bytearray = field(default_factory=bytearray)
    delivered: bool = False
