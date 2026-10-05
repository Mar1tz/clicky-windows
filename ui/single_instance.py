"""One HeyTora at a time.

Clicking HeyTora on the Start menu or the desktop while it is already
running used to start a second copy - two Toras listening to the same
microphone, two tray icons, two flow bars (seen 4-5 Oct 2026). Now the
first copy listens on a local socket (Qt's QLocalServer: a named pipe on
Windows, nothing on the network); a second copy tells it "open" and quits,
and the running HeyTora opens its app window - or, still at the sign-in,
brings that forward.

A copy that is about to restart itself (signing out) lets go first
(release), so the new copy it starts becomes the one.
"""
from __future__ import annotations

import getpass
import logging
import sys
from typing import Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

_log = logging.getLogger("tora")

OPEN = b"open\n"


def server_name() -> str:
    """Per Windows user: two people signed in to one PC each get their own."""
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "user"
    return "HeyTora-" + "".join(ch for ch in user if ch.isalnum() or ch in "-_")


class SingleInstance(QObject):
    """claim() once, early: True - this is the HeyTora; False - another one
    is running and has been asked to open its window, so quit."""

    activated = pyqtSignal()            # a second HeyTora was started: show yourself

    def __init__(self, name: str = ""):
        super().__init__()
        self.name = name or server_name()
        self._server: Optional[QLocalServer] = None

    def claim(self, timeout_ms: int = 1500) -> bool:
        if self._tell_the_running_one(timeout_ms):
            _log.info("HeyTora is already running - asked it to open its window")
            return False
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(self.name):
            # A pipe left over by a copy that died (other systems keep a
            # file): clear it and try once more.
            QLocalServer.removeServer(self.name)
            if not server.listen(self.name):
                _log.warning("single instance: could not listen (%s) - carrying on",
                             server.errorString())
                return True
        server.newConnection.connect(self._on_connection)
        self._server = server
        return True

    def release(self) -> None:
        """Stop being the HeyTora (before starting a fresh copy)."""
        server, self._server = self._server, None
        if server is not None:
            server.close()

    def _tell_the_running_one(self, timeout_ms: int) -> bool:
        sock = QLocalSocket()
        sock.connectToServer(self.name)
        if not sock.waitForConnected(timeout_ms):
            return False
        if sys.platform == "win32":
            # This copy was just clicked, so it may bring a window forward;
            # let the running one do it instead (ASFW_ANY).
            try:
                import ctypes
                ctypes.windll.user32.AllowSetForegroundWindow(-1)
            except Exception:  # noqa: BLE001
                pass
        sock.write(OPEN)
        sock.flush()
        sock.waitForBytesWritten(timeout_ms)
        sock.disconnectFromServer()
        return True

    def _on_connection(self) -> None:
        """Another HeyTora connected: that IS the request to open (only
        HeyTora connects here). Not waiting for its "open" - on Windows a pipe
        can lose the last bytes of a sender that quits straight away."""
        server = self._server
        if server is None:
            return
        came = False
        while server.hasPendingConnections():
            sock = server.nextPendingConnection()
            if sock is None:
                break
            came = True
            sock.disconnected.connect(sock.deleteLater)
            sock.disconnectFromServer()
        if came:
            self.activated.emit()


def bring_forward(windows: Callable[[], list]) -> None:
    """Show, raise and focus the visible windows `windows()` gives (the
    sign-in window, before the app is up)."""
    for w in windows():
        try:
            if w.isVisible():
                w.showNormal() if w.isMinimized() else None
                w.raise_()
                w.activateWindow()
        except RuntimeError:              # a window already deleted
            continue
