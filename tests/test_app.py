from __future__ import annotations

import asyncio
import sys
from pathlib import Path
import unittest

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as mcp_app  # noqa: E402


class McpTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._analytics_key = mcp_app.ANALYTICS_API_KEY
        self._mcp_auth_token = mcp_app.MCP_AUTH_TOKEN
        mcp_app.ANALYTICS_API_KEY = "test-analytics-key"
        mcp_app.MCP_AUTH_TOKEN = "test-mcp-token"
        self.client = TestClient(mcp_app.app)

    def tearDown(self) -> None:
        mcp_app.ANALYTICS_API_KEY = self._analytics_key
        mcp_app.MCP_AUTH_TOKEN = self._mcp_auth_token

    def test_initialize_echoes_requested_protocol_version(self) -> None:
        async def run() -> tuple[dict | None, int, dict[str, str]]:
            return await mcp_app._handle_jsonrpc(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26"},
                }
            )

        body, status, headers = asyncio.run(run())

        self.assertEqual(status, 200)
        self.assertIsNotNone(body)
        assert body is not None
        self.assertEqual(body["result"]["protocolVersion"], "2025-03-26")
        self.assertIn(mcp_app.MCP_SESSION_HEADER, headers)
        self.assertTrue(headers[mcp_app.MCP_SESSION_HEADER])

    def test_notifications_initialized_returns_202_without_body(self) -> None:
        async def run() -> tuple[dict | None, int, dict[str, str]]:
            return await mcp_app._handle_jsonrpc(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                }
            )

        body, status, headers = asyncio.run(run())

        self.assertEqual(status, 202)
        self.assertIsNone(body)
        self.assertEqual(headers, {})

    def test_mcp_endpoint_completes_initialize_and_notification_flow(self) -> None:
        init_response = self.client.post(
            "/mcp",
            headers={"Authorization": "Bearer test-mcp-token"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            },
        )

        self.assertEqual(init_response.status_code, 200)
        self.assertIn(mcp_app.MCP_SESSION_HEADER, init_response.headers)
        session_id = init_response.headers[mcp_app.MCP_SESSION_HEADER]
        self.assertEqual(init_response.json()["result"]["protocolVersion"], "2025-03-26")

        notification_response = self.client.post(
            "/mcp",
            headers={
                "Authorization": "Bearer test-mcp-token",
                mcp_app.MCP_SESSION_HEADER: session_id,
            },
            json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
        )

        self.assertEqual(notification_response.status_code, 202)
        self.assertEqual(notification_response.content, b"")

        tools_response = self.client.post(
            "/mcp",
            headers={
                "Authorization": "Bearer test-mcp-token",
                mcp_app.MCP_SESSION_HEADER: session_id,
            },
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            },
        )

        self.assertEqual(tools_response.status_code, 200)
        tools = tools_response.json()["result"]["tools"]
        tool_names = {tool["name"] for tool in tools}
        self.assertIn("tools", tools_response.json()["result"])
        self.assertIn("query_runtime_logs", tool_names)
        self.assertIn("get_bridge_failures", tool_names)
        self.assertIn("generate_rlm_report", tool_names)

    def test_mcp_endpoint_rejects_analytics_token(self) -> None:
        response = self.client.post(
            "/mcp",
            headers={"Authorization": "Bearer test-analytics-key"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            },
        )

        self.assertEqual(response.status_code, 401)

    def test_chat_with_rlm_tool_routes_to_rlm_service(self) -> None:
        async def fake_rlm_post(path: str, body: dict | None = None) -> dict:
            return {"path": path, "body": body, "message": "analysis"}

        self.addCleanup(setattr, mcp_app, "_rlm_post", mcp_app._rlm_post)
        mcp_app._rlm_post = fake_rlm_post

        result = asyncio.run(
            mcp_app._call_tool(
                "chat_with_rlm",
                {"prompt": "Investigate bridge auth failures", "lookback": 4},
            )
        )

        self.assertEqual(result["path"], "/chat/analyze")
        self.assertEqual(result["body"]["prompt"], "Investigate bridge auth failures")


if __name__ == "__main__":
    unittest.main()
