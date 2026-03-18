"""
g-trade-mcp: G-Trade MCP server backed by analytics API (same tool/resource names as local).
Auth: Bearer ANALYTICS_API_KEY or MCP_AUTH_TOKEN.
"""
from __future__ import annotations

import json
import os
import logging
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, status

logger = logging.getLogger(__name__)

ANALYTICS_API_URL = (os.environ.get("ANALYTICS_API_URL") or "").strip().rstrip("/")
ANALYTICS_API_KEY = (os.environ.get("ANALYTICS_API_KEY") or "").strip()
MCP_AUTH_TOKEN = (os.environ.get("MCP_AUTH_TOKEN") or "").strip()
MCP_SESSION_HEADER = "Mcp-Session-Id"


def _bearer_ok(auth: str | None) -> bool:
    if not auth or not auth.startswith("Bearer "):
        return False
    token = auth[7:].strip()
    # Accept either ANALYTICS_API_KEY (for analytics API) or MCP_AUTH_TOKEN (for Cursor)
    if ANALYTICS_API_KEY and token == ANALYTICS_API_KEY:
        return True
    if MCP_AUTH_TOKEN and token == MCP_AUTH_TOKEN:
        return True
    return False


def _api_get(path: str, params: dict | None = None) -> dict:
    if not ANALYTICS_API_URL or not ANALYTICS_API_KEY:
        return {}
    with httpx.Client(timeout=15.0) as client:
        r = client.get(
            f"{ANALYTICS_API_URL}{path}",
            headers={"Authorization": f"Bearer {ANALYTICS_API_KEY}"},
            params=params or {},
        )
        if r.status_code != 200:
            return {}
        return r.json()


def _jsonrpc_result(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _call_tool(name: str, arguments: dict) -> Any:
    if name == "list_runs":
        r = _api_get("/runs", {"limit": arguments.get("limit", 25), "search": arguments.get("search")})
        return {"runs": r.get("runs", [])}
    if name == "query_events":
        run_id = arguments.get("run_id")
        if run_id:
            r = _api_get(f"/runs/{run_id}/events", {"limit": arguments.get("limit", 100)})
            return {"events": r.get("events", [])}
        return {"events": []}
    if name == "get_performance_summary":
        run_id = arguments.get("run_id")
        r = _api_get("/analytics/summary")
        return r or {}
    if name == "get_runtime_summary":
        r = _api_get("/analytics/summary")
        return r or {}
    if name == "get_run_context":
        run_id = arguments.get("run_id")
        if not run_id:
            return {"error": "run_id required"}
        run = _api_get(f"/runs/{run_id}")
        events = _api_get(f"/runs/{run_id}/events", {"limit": arguments.get("event_limit", 50)})
        trades = _api_get(f"/runs/{run_id}/trades", {"limit": arguments.get("trade_limit", 50)})
        return {"run": run, "events": events.get("events", []), "trades": trades.get("trades", [])}
    return {"error": f"Unknown tool: {name}"}


app = FastAPI(title="g-trade-mcp")


@app.post("/mcp")
async def mcp_post(request: Request, authorization: str | None = Header(None)):
    if not _bearer_ok(authorization):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing Bearer token")
    body = await request.json()
    method = body.get("method")
    params = body.get("params") or {}
    req_id = body.get("id")
    try:
        if method == "initialize":
            return _jsonrpc_result(req_id, {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "es-hotzone-trader-mcp", "version": "0.1.0"},
                "capabilities": {"tools": {}, "resources": {}},
            })
        if method == "ping":
            return _jsonrpc_result(req_id, {})
        if method == "tools/list":
            return _jsonrpc_result(req_id, {"tools": [
                {"name": "list_runs", "description": "List recent runs.", "inputSchema": {"type": "object"}},
                {"name": "query_events", "description": "Query events.", "inputSchema": {"type": "object"}},
                {"name": "get_performance_summary", "description": "Performance summary.", "inputSchema": {"type": "object"}},
                {"name": "get_runtime_summary", "description": "Runtime summary.", "inputSchema": {"type": "object"}},
                {"name": "get_run_context", "description": "Run context.", "inputSchema": {"type": "object"}},
            ]})
        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            result = _call_tool(name, args)
            return _jsonrpc_result(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}],
                "structuredContent": result,
                "isError": False,
            })
        if method == "resources/list":
            return _jsonrpc_result(req_id, {"resources": []})
        if method == "resources/read":
            return _jsonrpc_result(req_id, {"contents": []})
        return _jsonrpc_error(req_id, -32601, f"Unsupported method: {method}")
    except Exception as e:
        logger.exception("MCP request failed")
        return _jsonrpc_error(req_id, -32000, str(e))


@app.get("/mcp")
async def mcp_metadata(authorization: str | None = Header(None)):
    if not _bearer_ok(authorization):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing Bearer token")
    return {
        "name": "es-hotzone-trader-mcp",
        "transport": "streamable-http",
        "endpoint": "/mcp",
        "session_header": MCP_SESSION_HEADER,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8002")))
