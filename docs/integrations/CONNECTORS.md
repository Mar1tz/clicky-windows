# Connectors — how HeyTora reaches the person's other apps

Four ways, from no setup to the most capable. All of them go through the
same registry and approvals as everything else an agent does.

| Way | What it gives | Setup | Code |
|---|---|---|---|
| Compose window | Email, today, for everyone: the message opens filled in, in their own Gmail/Outlook; they press Send | none | `integrations/compose.py` |
| Browser companion | Work in their own signed-in tabs (any site): read, click, type — each action approved — without moving their mouse | "set up the browser extension" (developer-mode load until it is on the Chrome Web Store) | `connectors/browser`, `integrations/browser_bridge.py`, `agents/tools/browser_tab_tools.py` |
| Google connectors | Gmail send in the background; Calendar read | Google OAuth Desktop client in `.env` (`TORA_GOOGLE_CLIENT_ID/SECRET`); public use needs verification — `GOOGLE_VERIFICATION.md` | `integrations/oauth.py`, `connections.py`, `gmail.py`, `gcal.py`, `vault.py` |
| MCP servers | Any of the thousands of MCP servers (GitHub, Notion, Slack, databases, files...) | `mcp_servers.json` in the data folder, Claude Desktop's format | `integrations/mcp.py`, `agents/tools/mcp_tools.py` |

## Taking over the PC

"Take over and do it": a computer job with `take_over`. One approval for the
whole job ("Let Tora use your mouse and keyboard to … for up to 15 minutes"),
then the mouse/keyboard/desktop/tab tools run without asking per click until
the time is up. **Esc stops it at once** (the kill switch). Sending, paying
and deleting still ask; auto-approve never gives control.

## mcp_servers.json

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "<token>"}
    },
    "files": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:\\Users\\me\\Documents"]
    }
  }
}
```

A job that names the server ("open an issue on github", "in my files") gets
its tools. A tool the server marks read-only runs on the job's policy; any
other asks every time. HeyTora restarts its servers when it starts.

## Safety, everywhere

* Tokens only in Windows Credential Manager (`integrations/vault.py`).
* Exact-host service detection; lookalike hosts refused (`providers.py`).
* Approvals bound to exactly what was shown (an email's digest; a click's
  target); any change asks again.
* Password, one-time-code and card fields are never filled.
* What any connector returns reaches the model as data, never instructions.
