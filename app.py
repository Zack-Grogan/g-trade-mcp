"""
g-trade-mcp: G-Trade MCP server backed by analytics API (same tool/resource names as local).
Auth: Bearer MCP_AUTH_TOKEN.
Transport: streamable-http with SSE support; returns Mcp-Session-Id and 202 for notifications.
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
RLM_SERVICE_URL = (os.environ.get("RLM_SERVICE_URL") or "").strip().rstrip("/")
RLM_AUTH_TOKEN = (os.environ.get("RLM_AUTH_TOKEN") or "").strip()
MCP_SESSION_HEADER = "Mcp-Session-Id"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-03-26", "2024-11-05")


def _bearer_ok(auth: str | None) -> bool:
    if not auth or not auth.startswith("Bearer "):
        return False
    token = auth[7:].strip()
    return bool(MCP_AUTH_TOKEN and token == MCP_AUTH_TOKEN)


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


async def _api_post(path: str, body: dict | None = None) -> dict:
    """Async analytics API POST. Returns {} on any error."""
    if not ANALYTICS_API_URL or not ANALYTICS_API_KEY:
        return {}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(
                f"{ANALYTICS_API_URL}{path}",
                headers={"Authorization": f"Bearer {ANALYTICS_API_KEY}"},
                json=body or {},
            )
            if r.status_code != 200:
                return {"error": f"HTTP {r.status_code}", "detail": r.text}
            return r.json()
    except Exception:
        logger.exception("_api_post failed: %s", path)
        return {}


def _rlm_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    if RLM_AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {RLM_AUTH_TOKEN}"
    return headers


async def _rlm_get(path: str, params: dict | None = None) -> dict:
    if not RLM_SERVICE_URL:
        return {}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                f"{RLM_SERVICE_URL}{path}",
                headers=_rlm_headers(),
                params=params or {},
            )
            if r.status_code != 200:
                return {"error": f"HTTP {r.status_code}", "detail": r.text}
            return r.json()
    except Exception:
        logger.exception("_rlm_get failed: %s", path)
        return {}


async def _rlm_post(path: str, body: dict | None = None) -> dict:
    if not RLM_SERVICE_URL:
        return {}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{RLM_SERVICE_URL}{path}",
                headers=_rlm_headers(),
                json=body or {},
            )
            if r.status_code != 200:
                return {"error": f"HTTP {r.status_code}", "detail": r.text}
            return r.json()
    except Exception:
        logger.exception("_rlm_post failed: %s", path)
        return {}


def _jsonrpc_result(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _negotiate_protocol_version(requested_version: Any) -> str:
    version = str(requested_version or "").strip()
    if version in SUPPORTED_PROTOCOL_VERSIONS:
        return version
    return SUPPORTED_PROTOCOL_VERSIONS[0]


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
    if name == "get_market_tape":
        run_id = arguments.get("run_id")
        if not run_id:
            return {"error": "run_id required"}
        r = await _api_get(f"/runs/{run_id}/market_tape", {"limit": arguments.get("limit", 500)})
        return {"marketTape": r.get("marketTape", [])}
    if name == "get_decision_snapshots":
        run_id = arguments.get("run_id")
        if not run_id:
            return {"error": "run_id required"}
        r = await _api_get(f"/runs/{run_id}/decision_snapshots", {"limit": arguments.get("limit", 200)})
        return {"decisionSnapshots": r.get("decisionSnapshots", [])}
    if name == "get_order_lifecycle":
        run_id = arguments.get("run_id")
        if not run_id:
            return {"error": "run_id required"}
        r = await _api_get(f"/runs/{run_id}/order_lifecycle", {"limit": arguments.get("limit", 500)})
        return {"orderLifecycle": r.get("orderLifecycle", [])}
    if name == "get_bridge_health":
        run_id = arguments.get("run_id")
        if not run_id:
            return {"error": "run_id required"}
        r = await _api_get(f"/runs/{run_id}/bridge_health", {"limit": arguments.get("limit", 100)})
        return {"bridgeHealth": r.get("bridgeHealth", [])}
    if name == "get_run_manifest":
        run_id = arguments.get("run_id")
        if not run_id:
            return {"error": "run_id required"}
        r = await _api_get(f"/runs/{run_id}/manifest")
        return r or {}
    if name == "get_event_timeline":
        run_id = arguments.get("run_id")
        if not run_id:
            return {"error": "run_id required"}
        r = await _api_get(f"/runs/{run_id}/timeline", {"limit": arguments.get("limit", 400)})
        return r or {}
    if name == "get_non_entry_explanations":
        run_id = arguments.get("run_id")
        if not run_id:
            return {"error": "run_id required"}
        r = await _api_get(f"/runs/{run_id}/non_entry_explanations", {"limit": arguments.get("limit", 200)})
        return r or {}
    if name == "compare_runs":
        left_run_id = arguments.get("left_run_id")
        right_run_id = arguments.get("right_run_id")
        if not left_run_id or not right_run_id:
            return {"error": "left_run_id and right_run_id required"}
        r = await _api_get("/runs/compare", {"left_run_id": left_run_id, "right_run_id": right_run_id})
        return r or {}
    if name == "search_runs":
        q = arguments.get("q")
        if not q:
            return {"error": "q required"}
        r = await _api_get("/search/runs", {"q": q, "limit": arguments.get("limit", 25)})
        return {"runs": r.get("runs", [])}
    if name == "search_events":
        q = arguments.get("q")
        if not q:
            return {"error": "q required"}
        r = await _api_get("/search/events", {"q": q, "limit": arguments.get("limit", 100)})
        return {"events": r.get("events", [])}
    if name == "query_runtime_logs":
        run_id = arguments.get("run_id")
        if not run_id:
            return {"error": "run_id required"}
        r = await _api_get(
            f"/runs/{run_id}/runtime_logs",
            {
                "limit": arguments.get("limit", 200),
                "level": arguments.get("level"),
                "search": arguments.get("search"),
                "start_time": arguments.get("start_time"),
                "end_time": arguments.get("end_time"),
            },
        )
        return {"runtimeLogs": r.get("runtimeLogs", [])}
    if name == "search_runtime_logs":
        q = arguments.get("q")
        if not q:
            return {"error": "q required"}
        r = await _api_get(
            "/search/runtime_logs",
            {
                "q": q,
                "run_id": arguments.get("run_id"),
                "level": arguments.get("level"),
                "limit": arguments.get("limit", 200),
            },
        )
        return {"runtimeLogs": r.get("runtimeLogs", [])}
    if name == "get_bridge_failures":
        r = await _api_get(
            "/bridge/failures",
            {"run_id": arguments.get("run_id"), "limit": arguments.get("limit", 100)},
        )
        return {"failures": r.get("failures", [])}
    if name == "get_service_health":
        analytics_health = await _api_get("/service-health")
        rlm_health = await _rlm_get("/health")
        return {"analytics": analytics_health, "rlm": rlm_health}
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
        market_tape = await _api_get(f"/runs/{run_id}/market_tape", {"limit": arguments.get("market_limit", 50)})
        decisions = await _api_get(f"/runs/{run_id}/decision_snapshots", {"limit": arguments.get("decision_limit", 25)})
        lifecycle = await _api_get(f"/runs/{run_id}/order_lifecycle", {"limit": arguments.get("order_limit", 50)})
        bridge_health = await _api_get(f"/runs/{run_id}/bridge_health", {"limit": arguments.get("bridge_limit", 25)})
        manifest = await _api_get(f"/runs/{run_id}/manifest")
        timeline = await _api_get(f"/runs/{run_id}/timeline", {"limit": arguments.get("timeline_limit", 200)})
        explanations = await _api_get(f"/runs/{run_id}/non_entry_explanations", {"limit": arguments.get("explanation_limit", 100)})
        return {
            "run": run,
            "events": events.get("events", []),
            "trades": trades.get("trades", []),
            "state_snapshots": snapshots.get("stateSnapshots", []),
            "market_tape": market_tape.get("marketTape", []),
            "decision_snapshots": decisions.get("decisionSnapshots", []),
            "order_lifecycle": lifecycle.get("orderLifecycle", []),
            "bridge_health": bridge_health.get("bridgeHealth", []),
            "manifest": manifest,
            "timeline": timeline.get("timeline", []),
            "blockers": timeline.get("blockers", []),
            "explanations": explanations.get("explanations", []),
            "counts": timeline.get("counts", {}),
        }
    if name == "summarize_execution_reconstruction":
        run_id = arguments.get("run_id")
        if not run_id:
            return {"error": "run_id required"}
        timeline = await _api_get(f"/runs/{run_id}/timeline", {"limit": arguments.get("limit", 400)})
        explanations = await _api_get(f"/runs/{run_id}/non_entry_explanations", {"limit": arguments.get("explanation_limit", 100)})
        return {
            "run_id": run_id,
            "counts": timeline.get("counts", {}),
            "blockers": timeline.get("blockers", []),
            "explanations": explanations.get("explanations", []),
            "timeline": timeline.get("timeline", []),
        }
    if name == "generate_rlm_report":
        return await _rlm_post(
            "/reports/generate",
            {
                "regime_context": arguments.get("regime_context", ""),
                "generation": arguments.get("generation", 1),
                "report_type": arguments.get("report_type", "on_demand"),
                "lookback": arguments.get("lookback", 8),
            },
        )
    if name == "generate_rlm_hypothesis":
        return await _rlm_post(
            "/hypotheses/generate",
            {
                "regime_context": arguments.get("regime_context", ""),
                "prior_conclusions_summary": arguments.get("prior_conclusions_summary", ""),
                "generation": arguments.get("generation", 1),
                "parent_hypothesis_id": arguments.get("parent_hypothesis_id"),
                "run_id": arguments.get("run_id"),
            },
        )
    if name == "submit_rlm_conclusion":
        result_id = arguments.get("result_id")
        verdict = arguments.get("verdict")
        if result_id is None or verdict is None:
            return {"error": "result_id and verdict required"}
        return await _rlm_post(
            "/conclusions/submit",
            {
                "result_id": result_id,
                "verdict": verdict,
                "confidence_score": arguments.get("confidence_score"),
                "mutation_directive": arguments.get("mutation_directive"),
                "regime_tags": arguments.get("regime_tags"),
            },
        )
    if name == "chat_with_rlm":
        prompt = arguments.get("prompt")
        if not prompt:
            return {"error": "prompt required"}
        return await _rlm_post(
            "/chat/analyze",
            {
                "prompt": prompt,
                "regime_context": arguments.get("regime_context", ""),
                "lookback": arguments.get("lookback", 8),
                "generation": arguments.get("generation", 1),
            },
        )
    return {"error": f"Unknown tool: {name}"}


def _tool_definitions() -> list[dict[str, Any]]:
    return [
        {"name": "list_runs", "description": "List recent runs.", "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"}, "search": {"type": "string"}}}},
        {"name": "query_events", "description": "Query events.", "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}, "limit": {"type": "integer"}, "category": {"type": "string"}, "event_type": {"type": "string"}, "order_id": {"type": "string"}, "search": {"type": "string"}, "since_minutes": {"type": "integer"}, "start_time": {"type": "string"}, "end_time": {"type": "string"}}, "required": ["run_id"]}},
        {"name": "query_runtime_logs", "description": "Query runtime logs for a run.", "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}, "limit": {"type": "integer"}, "level": {"type": "string"}, "search": {"type": "string"}, "start_time": {"type": "string"}, "end_time": {"type": "string"}}, "required": ["run_id"]}},
        {"name": "search_runtime_logs", "description": "Search runtime logs across runs.", "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}, "run_id": {"type": "string"}, "level": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["q"]}},
        {"name": "get_state_snapshots", "description": "Fetch state snapshots for a run.", "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["run_id"]}},
        {"name": "get_market_tape", "description": "Fetch market tape for a run.", "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["run_id"]}},
        {"name": "get_decision_snapshots", "description": "Fetch decision snapshots for a run.", "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["run_id"]}},
        {"name": "get_order_lifecycle", "description": "Fetch order lifecycle records for a run.", "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["run_id"]}},
        {"name": "get_bridge_health", "description": "Fetch bridge health records for a run.", "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["run_id"]}},
        {"name": "get_bridge_failures", "description": "Fetch recent bridge failures.", "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}, "limit": {"type": "integer"}}}},
        {"name": "get_service_health", "description": "Fetch analytics and RLM service health views.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "get_run_manifest", "description": "Fetch the manifest for a run.", "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]}},
        {"name": "get_event_timeline", "description": "Fetch the reconstructed run timeline.", "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["run_id"]}},
        {"name": "get_order_event_story", "description": "Fetch all events for a specific order.", "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}, "order_id": {"type": "string"}, "limit": {"type": "integer"}, "search": {"type": "string"}}, "required": ["run_id", "order_id"]}},
        {"name": "get_non_entry_explanations", "description": "Fetch decision snapshots that did not submit an order.", "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["run_id"]}},
        {"name": "compare_runs", "description": "Compare two runs by summary metrics.", "inputSchema": {"type": "object", "properties": {"left_run_id": {"type": "string"}, "right_run_id": {"type": "string"}}, "required": ["left_run_id", "right_run_id"]}},
        {"name": "search_runs", "description": "Search runs by query string.", "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["q"]}},
        {"name": "search_events", "description": "Search events across runs.", "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["q"]}},
        {"name": "get_performance_summary", "description": "Performance summary.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "get_runtime_summary", "description": "Runtime summary.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "get_run_context", "description": "Run context.", "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}, "event_limit": {"type": "integer"}, "trade_limit": {"type": "integer"}, "snapshot_limit": {"type": "integer"}, "market_limit": {"type": "integer"}, "decision_limit": {"type": "integer"}, "order_limit": {"type": "integer"}, "bridge_limit": {"type": "integer"}, "timeline_limit": {"type": "integer"}, "explanation_limit": {"type": "integer"}}, "required": ["run_id"]}},
        {"name": "summarize_execution_reconstruction", "description": "Summarize a run reconstruction timeline.", "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}, "limit": {"type": "integer"}, "explanation_limit": {"type": "integer"}}, "required": ["run_id"]}},
        {"name": "generate_rlm_report", "description": "Trigger RLM report generation.", "inputSchema": {"type": "object", "properties": {"regime_context": {"type": "string"}, "generation": {"type": "integer"}, "report_type": {"type": "string"}, "lookback": {"type": "integer"}}}},
        {"name": "generate_rlm_hypothesis", "description": "Trigger RLM hypothesis generation.", "inputSchema": {"type": "object", "properties": {"regime_context": {"type": "string"}, "prior_conclusions_summary": {"type": "string"}, "generation": {"type": "integer"}, "parent_hypothesis_id": {"type": "string"}, "run_id": {"type": "string"}}}},
        {"name": "submit_rlm_conclusion", "description": "Persist an RLM conclusion entry.", "inputSchema": {"type": "object", "properties": {"result_id": {"type": "integer"}, "verdict": {"type": "string"}, "confidence_score": {"type": "number"}, "mutation_directive": {"type": "string"}, "regime_tags": {"type": "object"}}, "required": ["result_id", "verdict"]}},
        {"name": "chat_with_rlm", "description": "Run an advisory operator chat request through RLM.", "inputSchema": {"type": "object", "properties": {"prompt": {"type": "string"}, "regime_context": {"type": "string"}, "lookback": {"type": "integer"}, "generation": {"type": "integer"}}, "required": ["prompt"]}},
    ]


async def _handle_jsonrpc(body: dict) -> tuple[dict | None, int, dict[str, str]]:
    """Returns (response_body or None, status_code, headers). 202 + no body for notifications."""
    method = body.get("method")
    params = body.get("params") or {}
    req_id = body.get("id")

    if method == "initialize":
        protocol_version = _negotiate_protocol_version(params.get("protocolVersion"))
        session_id = secrets.token_urlsafe(32)
        result = _jsonrpc_result(req_id, {
            "protocolVersion": protocol_version,
            "serverInfo": {"name": "es-hotzone-trader-mcp", "version": "0.1.0"},
            "capabilities": {"tools": {}, "resources": {}},
        })
        return result, 200, {MCP_SESSION_HEADER: session_id}
    if method == "notifications/initialized":
        return None, 202, {}
    if str(method or "").startswith("notifications/"):
        return None, 202, {}
    if method == "ping":
        return _jsonrpc_result(req_id, {}), 200, {}
    if method == "tools/list":
        return _jsonrpc_result(req_id, {"tools": _tool_definitions()}), 200, {}
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
    return StreamingResponse(_sse_generator(str(request.url)), media_type="text/event-stream", headers=_SSE_HEADERS)


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
    return StreamingResponse(_sse_generator(str(request.url)), media_type="text/event-stream", headers=_SSE_HEADERS)


@app.get("/sse")
async def sse_endpoint(request: Request, authorization: str | None = Header(None)):
    """Legacy SSE endpoint fallback for older clients."""
    if not _bearer_ok(authorization):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing Bearer token")
    return StreamingResponse(_sse_generator(str(request.url)), media_type="text/event-stream", headers=_SSE_HEADERS)


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8002")))
