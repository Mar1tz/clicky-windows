"""The sign-in link in the email, caught on this PC.

The sign-in email always carries a link; it carries the code too only once
the project has its own email sender (Supabase will not let a free project
on its built-in sender change the email). And most people click a link
anyway. So while the app waits for a sign-in, it serves one tiny page on
127.0.0.1 and asks Supabase to send the link's browser there. The browser
arrives with a one-time `code` that only this app can turn into a session:
the sign-in was asked for with a PKCE challenge, and its secret half (the
verifier) never left the app.

Nothing listens on the network - 127.0.0.1 only, on a port Windows picks,
until the sign-in lands, the window closes, or LISTEN_S passes. Supabase
sends a browser only to addresses on the project's Redirect URLs list, so
REDIRECT_PATTERN must stay on it (docs/agents/ACCOUNTS.md).
"""
from __future__ import annotations

import html
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional
from urllib.parse import parse_qs, urlsplit

_log = logging.getLogger("tora.account")

PATH = "/heytora/signed-in"
REDIRECT_PATTERN = "http://127.0.0.1:*/heytora/signed-in"     # the project's Redirect URLs entry
LISTEN_S = 30 * 60                                              # longer than anyone reads an email

_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>HeyTora</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
         font: 16px/1.5 "Segoe UI", system-ui, sans-serif; background: #0f1115; color: #e8eaf0; }}
  .card {{ max-width: 420px; padding: 36px 32px; border-radius: 18px; background: #181b22;
          text-align: center; box-shadow: 0 10px 40px rgba(0,0,0,.35); }}
  .mark {{ font-size: 40px; color: {colour}; }}
  h1 {{ font-size: 21px; margin: 8px 0; }}
  p {{ margin: 0; color: #a9afbd; }}
</style></head><body><div class="card"><div class="mark">{mark}</div>
<h1>{title}</h1><p>{words}</p></div></body></html>"""


def page(ok: bool, words: str, done_title: str = "You're signed in to HeyTora") -> bytes:
    return _PAGE.format(
        colour="#3D8BFF" if ok else "#ff7a7a", mark="&#10003;" if ok else "&#9888;",
        title=html.escape(done_title) if ok else (
            "That didn't sign you in" if done_title == "You're signed in to HeyTora" else "That didn't work"),
        words=html.escape(words)).encode("utf-8")


class LinkCatcher:
    """Waits on 127.0.0.1 for the browser the email's link sends back.

    `on_code(code)` runs on the catcher's thread with the one-time code and
    returns (ok, words) for the page the browser shows; after a success the
    catcher stops by itself.
    """

    def __init__(self, on_code: Callable[[str], tuple[bool, str]], listen_s: float = LISTEN_S, *,
                 path: str = PATH, on_params: Optional[Callable[[dict], tuple[bool, str]]] = None,
                 done_title: str = "You're signed in to HeyTora"):
        """`path` and `on_params` (CI-07): a connector's OAuth comes back to
        its own path, and is handed every parameter (state, code, error) to
        check - not just the code - with no hop to the website."""
        self._on_code = on_code
        self._listen_s = listen_s
        self._path = path
        self._on_params = on_params
        self._done_title = done_title
        self._server: Optional[ThreadingHTTPServer] = None
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        with self._lock:
            if not self._server:
                return ""
            return f"http://127.0.0.1:{self._server.server_address[1]}{self._path}"

    def start(self) -> str:
        """Listen (if not already) and return the address for the link; "" if it can't."""
        with self._lock:
            if self._server is None:
                try:
                    self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
                except OSError as e:
                    _log.warning("sign-in link catcher could not listen: %s", e)
                    return ""
                self._server.daemon_threads = True
                threading.Thread(target=self._server.serve_forever, name="heytora-signin",
                                 daemon=True).start()
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self._listen_s, self.stop)
            self._timer.daemon = True
            self._timer.start()
        return self.url

    def stop(self) -> None:
        with self._lock:
            server, self._server = self._server, None
            if self._timer:
                self._timer.cancel()
                self._timer = None
        if server:
            # Never from serve_forever's own thread; a request thread is fine.
            threading.Thread(target=lambda: (server.shutdown(), server.server_close()),
                             daemon=True).start()

    def _handler(self):
        catcher = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server's name
                parts = urlsplit(self.path)
                if parts.path != catcher._path:
                    self.send_error(404)
                    return
                query = parse_qs(parts.query)
                if catcher._on_params is not None:
                    try:
                        ok, words = catcher._on_params({k: v[0] for k, v in query.items()})
                    except Exception as e:  # noqa: BLE001 - shown in the browser, in words
                        ok, words = False, str(e)
                    body = page(ok, words, catcher._done_title)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                    if ok:
                        catcher.stop()
                    return
                code = (query.get("code") or [""])[0]
                if code:
                    try:
                        ok, words = catcher._on_code(code)
                    except Exception as e:  # noqa: BLE001 - shown in the browser, in words
                        ok, words = False, str(e)
                else:
                    # Supabase sends ?error_description=... for a used or expired link.
                    ok, words = False, ((query.get("error_description") or [""])[0]
                                        or "The link had no sign-in in it.")
                    words += " Send a new one from HeyTora."
                if ok:
                    words = words or "You can close this tab and go back to HeyTora."
                from account import settings
                if settings.website_url():
                    # The website's own page says it, in HeyTora's look; the
                    # app's window says the same (and why, if it failed).
                    self.send_response(302)
                    self.send_header("Location", settings.website_url() + "/signed-in"
                                     + ("" if ok else "?error=1"))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    if ok:
                        catcher.stop()
                    return
                body = page(ok, words)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                if ok:
                    catcher.stop()

            def log_message(self, fmt, *args):  # the code is in the URL: never log it
                pass

        return Handler
