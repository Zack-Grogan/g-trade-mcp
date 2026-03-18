"""
g-trade-mcp: G-Trade MCP server backed by analytics API (same tool/resource names as local).
Auth: Bearer ANALYTICS_API_KEY or MCP_AUTH_TOKEN.
Transport: streamable-http (2025-03-26) with SSE support; returns Mcp-Session-Id and 202 for notifications.
"""
from __future__ import annotations

import json
import os
import logging
import secrets
from typing import Any, AsyncGenerator

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response, StreamingResponse

logger = logging.getLogger(__name__)

ANALYTICS_API_URL = (os.environ.get("ANALYTICS_API_URL") or "").strip().rstrip("/")
ANALYTICS_API_KEY = (os.environ.get("ANALYTICS_API_KEY") or "").strip()
MCP_AUTH_TOKEN = (os.environ.get("MCP_AUTH_TOKEN") or "").strip()
MCP_SESSION_HEADER = "Mcp-Session-Id"


def _bearer_ok(auth: str | None) -> bool:
    if not auth or not auth.startswith("Bearer "):
        return False
    token = auth[7:].strip()
    if ANALYTICS_API_KEY and token == ANALYTICS_API_KEY:
        return True
    if MCP_AUTH_TOKEN and token == MCP_AUTH_TOKEN:
        return True
    return False


async def _api_get(path: str, params: dict | None = None) -> dict:
    """Async analytics API GET. Returns {} on any error."""
    if not ANALYTICS_API_URL or not ANALYTICS_API_KEY:
        return {}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{ANALYTICS_API_URL}{path}",
                headers={"Authorization": f"Bearer {ANALYTICS_API_KEY}"},
                params=params or {},
            )
            if r.status_code != 200:
                return {}
            return r.json()
    except Exception:
        logger.exception("_api_get failed: %s", path)
        return {}


def _jsonrpc_result(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


async def _call_tool(name: str, arguments: dict) -> Any:
    if name == "list_runs":
        r = await _api_get("/runs", {"limit": arguments.get("limit", 25), "search": arguments.get("search")})
        return {"runs": r.get("runs", [])}
    if name == "query_events":
        run_id = arguments.get("run_id")
        if run_id:
            r = await _api_get(
                f"/runs/{run_id}/events",
                {
                    "limit": arguments.get("limit", 100),
                    "category": arguments.get("category"),
                    "event_type": arguments.get("event_type"),
                    "order_id": arguments.get("order_id"),
                    "search": arguments.get("search"),
                    "since_minutes": arguments.get("since_minutes"),
                    "start_time": arguments.get("start_time"),
                    "end_time": arguments.get("end_time"),
                },
            )
            return {"events": r.get("events", [])}
        return {"events": []}
    if name == "get_state_snapshots":
        run_id = arguments.get("run_id")
        if not run_id:
            return {"error": "run_id required"}
        r = await _api_get(f"/runs/{run_id}/state_snapshots", {"limit": arguments.get("limit", 100)})
        return {"stateSnapshots": r.get("stateSnapshots", [])}
    if name == "get_event_timeline":
        run_id = arguments.get("run_id")
        if not run_id:
            return {"error": "run_id required"}
        r = await _api_get(f"/runs/{run_id}/timeline", {"limit": arguments.get("limit", 400)})
        return r or {}
    if name == "get_order_event_story":
        run_id = arguments.get("run_id")
        order_id = arguments.get("order_id")
        if not run_id or not order_id:
            return {"error": "run_id and order_id required"}
        r = await _api_get(
            f"/runs/{run_id}/events",
            {
                "limit": arguments.get("limit", 200),
                "order_id": order_id,
                "search": arguments.get("search"),
            },
        )
        return {"run_id": run_id, "order_id": order_id, "events": r.get("events", [])}
    if name == "get_performance_summary":
        r = await _api_get("/analytics/summary")
        return r or {}
    if name == "get_runtime_summary":
        r = await _api_get("/analytics/summary")
        return r or {}
    if name == "get_run_context":
        run_id = arguments.get("run_id")
        if not run_id:
            return {"error": "run_id required"}
        run = await _api_get(f"/runs/{run_id}")
        events = await _api_get(f"/runs/{run_id}/events", {"limit": arguments.get("event_limit", 50)})
        trades = await _api_get(f"/runs/{run_id}/trades", {"limit": arguments.get("trade_limit", 50)})
        snapshots = await _api_get(f"/runs/{run_id}/state_snapshots", {"limit": arguments.get("snapshot_limit", 25)})
        timeline = await _api_get(f"/runs/{run_id}/timeline", {"limit": arguments.get("timeline_limit", 200)})
        return {
            "run": run,
            "events": events.get("events", []),
            "trades": trades.get("trades", []),
            "state_snapshots": snapshots.get("stateSnapshots", []),
            "timeline": timeline.get("timeline", []),
            "blockers": timeline.get("blockers", []),
            "counts": timeline.get("counts", {}),
        }
    if name == "summarize_execution_reconstruction":
        run_id = arguments.get("run_id")
        if not run_id:
            return {"error": "run_id required"}
        timeline = await _api_get(f"/runs/{run_id}/timeline", {"limit": arguments.get("limit", 400)})
        return {
            "run_id": run_id,
            "counts": timeline.get("counts", {}),
            "blockers": timeline.get("blockers", []),
            "timeline": timeline.get("timeline", []),
        }
    return {"error": f"Unknown tool: {name}"}


async def _handle_jsonrpc(body: dict) -> tuple[dict | None, int, dict[str, str]]:
    """Returns (response_body or None, status_code, headers). 202 + no body for notifications."""
    method = body.get("method")
    params = body.get("params") or {}
    req_id = body.get("id")

    if method == "initialize":
        session_id = secrets.token_urlsafe(32)
        result = _jsonrpc_result(req_id, {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "es-hotzone-trader-mcp", "version": "0.1.0"},
            "capabilities": {"tools": {}, "resources": {}},
        })
        return result, 200, {MCP_SESSION_HEADER: session_id}
    if method == "notifications/initialized":
        # Codex rmcp currently expects a JSON-RPC-style response here; be lenient.
        # Echo the id if present so both "request" and "notification" styles work.
        return _jsonrpc_result(req_id, {}), 200, {}
    if method == "ping":
        return _jsonrpc_result(req_id, {}), 200, {}
    if method == "tools/list":
        return _jsonrpc_result(req_id, {"tools": [
            {"name": "list_runs", "description": "List recent runs.", "inputSchema": {"type": "object"}},
            {"name": "query_events", "description": "Query events.", "inputSchema": {"type": "object"}},
            {"name": "get_state_snapshots", "description": "Fetch state snapshots for a run.", "inputSchema": {"type": "object"}},
            {"name": "get_event_timeline", "description": "Fetch the reconstructed run timeline.", "inputSchema": {"type": "object"}},
            {"name": "get_order_event_story", "description": "Fetch all events for a specific order.", "inputSchema": {"type": "object"}},
            {"name": "get_performance_summary", "description": "Performance summary.", "inputSchema": {"type": "object"}},
            {"name": "get_runtime_summary", "description": "Runtime summary.", "inputSchema": {"type": "object"}},
            {"name": "get_run_context", "description": "Run context.", "inputSchema": {"type": "object"}},
            {"name": "summarize_execution_reconstruction", "description": "Summarize a run reconstruction timeline.", "inputSchema": {"type": "object"}},
        ]}), 200, {}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        result = await _call_tool(name, args)
        return _jsonrpc_result(req_id, {
            "content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}],
            "structuredContent": result,
            "isError": False,
        }), 200, {}
    if method == "resources/list":
        return _jsonrpc_result(req_id, {"resources": []}), 200, {}
    if method == "resources/read":
        return _jsonrpc_result(req_id, {"contents": []}), 200, {}
    return _jsonrpc_error(req_id, -32601, f"Unsupported method: {method}"), 200, {}


def _sse_generator(endpoint_data: str) -> AsyncGenerator[str, None]:
    """Return an SSE async generator that sends the endpoint event then periodic pings."""
    async def _gen() -> AsyncGenerator[str, None]:
        import asyncio
        yield f"event: endpoint\ndata: {endpoint_data}\n\n"
        while True:
            await asyncio.sleep(30)
            yield "event: ping\ndata: {}\n\n"
    return _gen()


_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

app = FastAPI(title="g-trade-mcp")


# Root path handlers — Cursor/Codex post to root or /mcp
@app.post("/")
async def root_post(request: Request, authorization: str | None = Header(None)):
    if not _bearer_ok(authorization):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing Bearer token")
    body = await request.json()
    try:
        result, status_code, headers = await _handle_jsonrpc(body)
        if status_code == 202:
            return Response(status_code=202, headers=headers)
        return JSONResponse(content=result, status_code=status_code, headers=headers)
    except Exception as e:
        logger.exception("MCP request failed")
        return JSONResponse(
            content=_jsonrpc_error(body.get("id"), -32000, str(e)),
            status_code=200,
        )


@app.get("/")
async def root_get(request: Request, authorization: str | None = Header(None)):
    """SSE endpoint for streamable HTTP transport at root."""
    if not _bearer_ok(authorization):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing Bearer token")
    return StreamingResponse(_sse_generator("/"), media_type="text/event-stream", headers=_SSE_HEADERS)


@app.post("/mcp")
async def mcp_post(request: Request, authorization: str | None = Header(None)):
    if not _bearer_ok(authorization):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing Bearer token")
    body = await request.json()
    try:
        result, status_code, headers = await _handle_jsonrpc(body)
        if status_code == 202:
            return Response(status_code=202, headers=headers)
        return JSONResponse(content=result, status_code=status_code, headers=headers)
    except Exception as e:
        logger.exception("MCP request failed")
        return JSONResponse(
            content=_jsonrpc_error(body.get("id"), -32000, str(e)),
            status_code=200,
        )


@app.get("/mcp")
async def mcp_get(request: Request, authorization: str | None = Header(None)):
    """SSE endpoint for streamable HTTP transport."""
    if not _bearer_ok(authorization):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing Bearer token")
    return StreamingResponse(_sse_generator("/mcp"), media_type="text/event-stream", headers=_SSE_HEADERS)


@app.get("/sse")
async def sse_endpoint(request: Request, authorization: str | None = Header(None)):
    """Legacy SSE endpoint fallback for older clients."""
    if not _bearer_ok(authorization):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing Bearer token")
    return StreamingResponse(_sse_generator("/mcp"), media_type="text/event-stream", headers=_SSE_HEADERS)


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8002")))
