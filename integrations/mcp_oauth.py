"""Signing in to an MCP server on the internet - no app to register first.

Google needs HeyTora's own client id and a review (integrations/oauth.py).
MCP servers follow the MCP authorization spec instead, so one flow works
for every service that does - Linear, Notion, Sentry, Atlassian and more:

1. The server's protected-resource metadata names its sign-in server
   (/.well-known/oauth-protected-resource, or the URL its 401 points at).
2. That server's metadata names its endpoints
   (/.well-known/oauth-authorization-server, or OpenID's).
3. HeyTora registers itself there (dynamic client registration) with its
   127.0.0.1 return address - no developer account, no review.
4. The person signs in in their own browser: PKCE S256 and a random
   `state`, the `resource` naming exactly this server (RFC 8707), the
   answer caught on 127.0.0.1 (account/loopback.py).
5. The tokens go to Windows Credential Manager under "mcp:<name>"; an old
   access token is refreshed there, and never written anywhere else.

Transport injectable: tests never go online.
"""
from __future__ import annotations

import logging
import re
import secrets
import threading
import time
import webbrowser
from typing import Callable, Optional
from urllib.parse import urlencode, urlsplit

import httpx

from .oauth import Refused, pkce
from .vault import vault

log = logging.getLogger("tora.mcp")

PATH = "/heytora/mcp-connected"
WAIT_S = 300.0


def vault_ref(name: str) -> str:
    return f"mcp:{name}"


def _origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def _get_json(http: httpx.Client, url: str) -> Optional[dict]:
    try:
        r = http.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def discover(server_url: str, challenge: str = "", *, transport=None) -> dict:
    """Where to sign in for this server: its endpoints, the resource to
    name, the scopes it lists. Raises Refused when it has no sign-in."""
    path = urlsplit(server_url).path.rstrip("/")
    with httpx.Client(transport=transport, timeout=20, follow_redirects=True) as http:
        pointed = re.search(r'resource_metadata="([^"]+)"', challenge or "")
        resource = None
        for url in ([pointed.group(1)] if pointed else []) + [
                f"{_origin(server_url)}/.well-known/oauth-protected-resource{path}",
                f"{_origin(server_url)}/.well-known/oauth-protected-resource"]:
            resource = _get_json(http, url)
            if resource:
                break
        issuer = str(((resource or {}).get("authorization_servers") or [_origin(server_url)])[0]).rstrip("/")
        ip = urlsplit(issuer).path.rstrip("/")
        meta = None
        for url in (f"{_origin(issuer)}/.well-known/oauth-authorization-server{ip}",
                    f"{_origin(issuer)}/.well-known/openid-configuration{ip}",
                    f"{issuer}/.well-known/openid-configuration"):
            meta = _get_json(http, url)
            if meta and meta.get("authorization_endpoint") and meta.get("token_endpoint"):
                break
            meta = None
    if meta is None:                                  # the 2025-03-26 spec's default places
        base = _origin(issuer)
        meta = {"authorization_endpoint": base + "/authorize", "token_endpoint": base + "/token",
                "registration_endpoint": base + "/register"}
    if "S256" not in (meta.get("code_challenge_methods_supported") or ["S256"]):
        raise Refused("That service's sign-in does not support PKCE, so HeyTora will not use it.")
    return {"authorize": meta["authorization_endpoint"], "token": meta["token_endpoint"],
            "register": meta.get("registration_endpoint", ""),
            "resource": str((resource or {}).get("resource") or server_url),
            "scopes": [str(s) for s in (resource or {}).get("scopes_supported") or []]}


def register(where: dict, redirect: str, *, transport=None) -> tuple[str, str]:
    """(client_id, client_secret) - HeyTora registered with this sign-in server."""
    if not where.get("register"):
        raise Refused("That service wants an app registered by hand before sign-in - HeyTora can't connect "
                      "it this way yet.")
    with httpx.Client(transport=transport, timeout=20) as http:
        r = http.post(where["register"], json={
            "client_name": "HeyTora", "redirect_uris": [redirect], "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})
    if r.status_code not in (200, 201):
        raise Refused(f"That service did not let HeyTora register ({r.status_code}).")
    data = r.json()
    return str(data["client_id"]), str(data.get("client_secret") or "")


def connect(name: str, server_url: str, challenge: str = "", *,
            open_browser: Callable[[str], object] = webbrowser.open, transport=None,
            wait_s: float = WAIT_S, catcher_factory=None) -> dict:
    """Sign in to the server and keep its tokens. What was kept, without the tokens."""
    from account.loopback import LinkCatcher
    where = discover(server_url, challenge, transport=transport)
    got: dict = {}
    done = threading.Event()
    state = secrets.token_urlsafe(24)

    def on_params(params: dict) -> tuple[bool, str]:
        if params.get("state") != state:
            got["error"] = "state"
        elif params.get("error"):
            got["error"] = params["error"]
        else:
            got["code"] = params.get("code", "")
        done.set()
        return ("error" not in got,
                "You can close this tab and go back to HeyTora." if "error" not in got else "Nothing was connected.")

    make = catcher_factory or (lambda handler: LinkCatcher(lambda c: (False, ""), wait_s, path=PATH,
                                                           on_params=handler, done_title="Connected to HeyTora"))
    catcher = make(on_params)
    redirect = catcher.start()
    if not redirect:
        raise Refused("HeyTora could not listen for the sign-in's answer on this PC.")
    try:
        client_id, client_secret = register(where, redirect, transport=transport)
        verifier, code_challenge = pkce()
        q = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect, "state": state,
             "code_challenge": code_challenge, "code_challenge_method": "S256", "resource": where["resource"]}
        if where["scopes"]:
            q["scope"] = " ".join(where["scopes"])
        open_browser(f"{where['authorize']}{'&' if '?' in where['authorize'] else '?'}{urlencode(q)}")
        if not done.wait(wait_s):
            raise Refused("Nothing came back from the sign-in in time.")
    finally:
        catcher.stop()
    if got.get("error"):
        raise Refused("You chose not to connect it." if got["error"] == "access_denied"
                      else "That answer did not come from HeyTora's request." if got["error"] == "state"
                      else f"The sign-in said: {got['error']}", denied=got["error"] == "access_denied")
    form = {"grant_type": "authorization_code", "code": got["code"], "redirect_uri": redirect,
            "client_id": client_id, "code_verifier": verifier, "resource": where["resource"]}
    if client_secret:
        form["client_secret"] = client_secret
    with httpx.Client(transport=transport, timeout=20) as http:
        r = http.post(where["token"], data=form, headers={"Accept": "application/json"})
    if r.status_code != 200:
        raise Refused(f"The sign-in was not accepted ({r.status_code}).")
    held = {"client_id": client_id, "client_secret": client_secret, "token_endpoint": where["token"],
            "resource": where["resource"], **_tokens(r.json())}
    vault().put(vault_ref(name), held)
    log.info("mcp %s: signed in (%s)", name, _origin(server_url))
    return {"name": name, "resource": where["resource"]}


def _tokens(data: dict, previous_refresh: str = "") -> dict:
    return {"access_token": str(data["access_token"]),
            "refresh_token": str(data.get("refresh_token") or previous_refresh),
            "expires_at": time.time() + float(data.get("expires_in") or 3600) - 60}


def token(name: str, *, force: bool = False, transport=None) -> Optional[str]:
    """The access token for server `name` - refreshed when old or `force`.
    None: never signed in, or the sign-in is over (connect again)."""
    held = vault().get(vault_ref(name))
    if not held or not held.get("access_token"):
        return None
    if not force and float(held.get("expires_at") or 0) > time.time():
        return str(held["access_token"])
    if not held.get("refresh_token"):
        return None if force else str(held["access_token"])
    form = {"grant_type": "refresh_token", "refresh_token": held["refresh_token"],
            "client_id": held.get("client_id", ""), "resource": held.get("resource", "")}
    if held.get("client_secret"):
        form["client_secret"] = held["client_secret"]
    try:
        with httpx.Client(transport=transport, timeout=20) as http:
            r = http.post(held["token_endpoint"], data=form, headers={"Accept": "application/json"})
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        log.info("mcp %s: refresh refused (%d) - needs connecting again", name, r.status_code)
        return None
    held.update(_tokens(r.json(), previous_refresh=held["refresh_token"]))
    vault().put(vault_ref(name), held)
    return str(held["access_token"])


def forget(name: str) -> bool:
    return vault().delete(vault_ref(name))
