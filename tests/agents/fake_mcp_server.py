"""A tiny MCP server for the tests: newline-delimited JSON-RPC on stdio.
Two tools - one that says it only reads, one that does not."""
import json
import sys

TOOLS = [
    {"name": "search_issues", "description": "Search the issues.", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}},
    {"name": "create_issue", "description": "Open a new issue.",
     "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}},
]

print("a stray line a server prints on start", flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    method, params = msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion"), "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": TOOLS[:1], "nextCursor": "p2"} if not params.get("cursor") else {"tools": TOOLS[1:]}
    elif method == "tools/call":
        args = params.get("arguments") or {}
        if params["name"] == "search_issues":
            result = {"content": [{"type": "text", "text": f"2 issues match {args.get('q')!r}"}]}
        else:
            result = {"content": [{"type": "text", "text": f"opened #12 {args.get('title')}"}]}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "no such method"}}),
              flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
