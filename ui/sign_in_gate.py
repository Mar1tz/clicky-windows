"""No HeyTora account, no HeyTora.

Every copy of the app belongs to a signed-in HeyTora account - also when it
thinks with the user's own Gemini key. So before anything starts (the
overlay, the hotkey, the microphone, the agents, the tray's features), the
app checks for a sign-in; without one it shows the first launch - or, if
that already ran, its sign-in page - and nothing else. Closing that window
closes the app.

A sign-in can also end while the app runs: "Sign out" in Settings, or the
server no longer accepting it (the account was deleted, or signed out
everywhere). Then the app restarts into the same window, rather than
carrying on unlocked until the next launch.

A build that does not know its HeyTora service (account/settings.py empty -
a fork, or the tests) has no accounts to sign in to, and no gate.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Optional

from PyQt6.QtCore import QEventLoop, QObject, QProcess, pyqtSignal

_log = logging.getLogger("tora.account")


def required() -> bool:
    """Must the user sign in before HeyTora may do anything?"""
    from account import settings
    from account.service import account
    return settings.configured() and not account().signed_in()


def gate(wizard_factory=None) -> str:
    """Before anything else starts. Returns
         "ok"         already signed in (or a build without accounts)
         "signed_in"  signed in just now, through the window shown here
         "closed"     the window was closed without signing in: quit
    """
    if not required():
        return "ok"
    from ui.setup_wizard import SetupWizard, setup_already_ran
    make = wizard_factory or SetupWizard
    # After a sign-out the questions were already answered: straight to
    # signing in. A fresh install (or a reinstall) gets the whole first launch.
    wiz = make(start="connect" if setup_already_ran() else "welcome")
    loop = QEventLoop()
    wiz.completed.connect(loop.quit)
    wiz.finished.connect(loop.quit)
    # The first half is done (signed in, mic tested): the app starts, and
    # the same window carries on once main.py attach()es it (open_wizard()).
    ready = getattr(wiz, "ready", None)
    if ready is not None:
        ready.connect(loop.quit)
    wiz.show()
    wiz.raise_()
    wiz.activateWindow()
    loop.exec()
    _keep.append(wiz)
    if required():
        _log.info("closed without signing in - HeyTora stays shut")
        return "closed"
    return "signed_in"


_keep: list = []        # the gate's wizard outlives its loop until the app is up


def open_wizard():
    """The gate's first-launch window, if it is still open and waiting for
    the running app (main.py attach()es it); else None."""
    for wiz in reversed(_keep):
        try:
            if wiz.isVisible():
                return wiz
        except RuntimeError:             # deleted on the C++ side
            continue
    return None


class _Relay(QObject):
    """Carries "the sign-in is gone" from any thread to the main one."""
    lost = pyqtSignal()


_relay: Optional[_Relay] = None


def relay() -> _Relay:
    """Create on the main thread (main.py does, before anything can call lost())."""
    global _relay
    if _relay is None:
        _relay = _Relay()
    return _relay


def lost() -> None:
    """The sign-in ended (signed out, or refused by the server). Any thread."""
    if _relay is not None:
        _relay.lost.emit()


def check_in_background() -> None:
    """Ask the server whether this sign-in still stands; if not, lost().

    Offline, or the server having a bad moment, is not a lost sign-in - only
    the server refusing it is (account.service forgets it then).
    """
    if not required_service():
        return

    def check() -> None:
        from account.service import SignInNeeded, account
        try:
            account().summary(force=True)
        except SignInNeeded:
            _log.info("the server no longer accepts this sign-in")
            lost()
        except Exception:  # noqa: BLE001 - offline etc.: carry on
            _log.debug("sign-in check could not reach HeyTora", exc_info=True)

    threading.Thread(target=check, name="heytora-signin-check", daemon=True).start()


def required_service() -> bool:
    """Is there a HeyTora service and a sign-in to check?"""
    from account import settings
    from account.service import account
    return settings.configured() and account().signed_in()


def restart() -> bool:
    """Start a fresh copy of the app (which opens at the gate); the caller quits."""
    if getattr(sys, "frozen", False):
        program, args = sys.executable, sys.argv[1:]
    else:
        program, args = sys.executable, [os.path.abspath(sys.argv[0])] + sys.argv[1:]
    ok, _pid = QProcess.startDetached(program, args, os.getcwd())
    if not ok:
        _log.warning("could not restart HeyTora for sign-in")
    return bool(ok)
