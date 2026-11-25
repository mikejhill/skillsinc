"""One tray per user: later launches forward their request to the running one."""

from __future__ import annotations

import getpass
import hashlib
import json
import logging
from collections.abc import Sequence
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
        """Send ``arguments`` to a running tray; True if one received them."""
        socket = QLocalSocket()
        socket.connectToServer(self._name)
        if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
            return False
        socket.write(json.dumps(list(arguments)).encode("utf-8"))
        socket.flush()
        socket.waitForBytesWritten(CONNECT_TIMEOUT_MS)
        socket.disconnectFromServer()
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
            # The sender writes one message and disconnects; read then, so
            # nothing that arrived before the signals connected is missed
            if (  # pragma: no cover - depends on how fast the sender closes
                socket.state() == QLocalSocket.LocalSocketState.UnconnectedState
            ):
                self._read(socket)
                continue
            socket.disconnected.connect(lambda s=socket: self._read(s))

    def _read(self, socket: QLocalSocket) -> None:
        payload = bytes(socket.readAll().data()).decode("utf-8", errors="replace")
        # Keep the socket alive until deferred deletion; Windows pipe
        # callbacks can still be queued after it disconnects
        socket.setParent(QCoreApplication.instance())
        socket.deleteLater()
        try:
            decoded: object = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("Ignoring a malformed message from another launch")
            return
        if isinstance(decoded, list):
            self.message_received.emit([str(item) for item in decoded])
