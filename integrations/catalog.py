"""Services HeyTora can connect by name - "connect Linear", "connect Zapier".

Each is an MCP server (integrations/mcp.py, mcp_http.py) and one of four
ways in:

* sign_in - the service's own sign-in in their browser, nothing to copy
  (integrations/mcp_oauth.py: no app registered by hand, no review).
* key     - they make a key on the service's page and copy it; HeyTora
  reads it off the clipboard once and keeps it in Credential Manager.
* link    - the service gives them a private URL (Zapier, Composio) that
  reaches thousands of apps; copied the same way.
* local   - a program on their PC (needs Node.js), no account.

Anything not listed: any MCP server URL they paste ("custom").
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Service:
    id: str
    name: str
    how: str                         # sign_in | key | link | local
    url: str = ""                    # the MCP server (sign_in, key)
    setup: str = ""                  # the page where they get a key or a link
    looks_like: str = ""             # what a pasted key or link must match
    header: str = "Authorization"    # key: the header it goes in, as "Bearer <key>"
    command: str = ""                # local
    args: tuple = ()
    aliases: tuple = ()
    gives: str = ""                  # what jobs can do with it, said once connected


SERVICES: dict[str, Service] = {s.id: s for s in (
    Service("linear", "Linear", "sign_in", "https://mcp.linear.app/mcp",
            gives="find, create and update issues and projects"),
    Service("notion", "Notion", "sign_in", "https://mcp.notion.com/mcp",
            gives="search, read and write pages and databases"),
    Service("sentry", "Sentry", "sign_in", "https://mcp.sentry.dev/mcp",
            gives="look up errors and issues"),
    Service("atlassian", "Jira and Confluence", "sign_in", "https://mcp.atlassian.com/v1/sse",
            aliases=("jira", "confluence"), gives="Jira issues and Confluence pages"),
    Service("github", "GitHub", "key", "https://api.githubcopilot.com/mcp/",
            setup="https://github.com/settings/personal-access-tokens/new",
            looks_like=r"^(ghp_|github_pat_)[A-Za-z0-9_]{20,}$",
            gives="repositories, issues, pull requests"),
    Service("stripe", "Stripe", "key", "https://mcp.stripe.com",
            setup="https://dashboard.stripe.com/apikeys",
            looks_like=r"^(rk|sk)_(live|test)_[A-Za-z0-9]{10,}$",
            gives="customers, payments, invoices (use a restricted key)"),
    Service("zapier", "Zapier", "link", setup="https://mcp.zapier.com",
            looks_like=r"^https://mcp\.zapier\.com/\S+$",
            gives="the apps they switched on in Zapier - Slack, Outlook, Sheets, Trello and thousands more"),
    Service("composio", "Composio", "link", setup="https://platform.composio.dev",
            looks_like=r"^https://[a-z0-9.-]*composio\.dev/\S+$",
            gives="the apps they set up in Composio"),
    Service("files", "Files", "local", command="npx", aliases=("documents",),
            args=("-y", "@modelcontextprotocol/server-filesystem", "{documents}"),
            gives="read and write files in their Documents folder"),
)}


def find(words: str) -> Optional[Service]:
    """The service they named ("connect jira" -> Atlassian)."""
    w = re.sub(r"[^a-z0-9]+", " ", (words or "").lower()).strip()
    for s in SERVICES.values():
        if w in (s.id, s.name.lower()) or w in s.aliases:
            return s
    for s in SERVICES.values():
        if s.id in w.split() or any(a in w.split() for a in s.aliases):
            return s
    return None


def accepts(service: Service, pasted: str) -> bool:
    """Is what they copied the key or link this service gives? Never echoed."""
    return bool(service.looks_like and re.match(service.looks_like, (pasted or "").strip()))


def custom_url_ok(url: str) -> bool:
    """A pasted MCP server URL: https, a real host, no user@host trick."""
    m = re.match(r"^https://([^/@\s]+)(/\S*)?$", (url or "").strip())
    return bool(m and "." in m.group(1))


def names_for_jobs() -> dict[str, tuple]:
    """Server name -> words a job's goal may use for it ("jira" -> atlassian)."""
    return {s.id: (s.id, *s.aliases, *s.name.lower().split()) for s in SERVICES.values()}
