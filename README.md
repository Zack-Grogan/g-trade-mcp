# g-trade-mcp

MCP server on Railway. Same protocol and tool names as the previous local MCP; backend is Postgres/analytics API. Part of the G-Trade Railway project; see [Architecture overview](https://github.com/Zack-Grogan/G-Trade/blob/main/docs/Architecture-Overview.md) and [OPERATOR](https://github.com/Zack-Grogan/G-Trade/blob/main/docs/OPERATOR.md) for architecture and Cursor setup.

- **Env:** `ANALYTICS_API_URL`, `ANALYTICS_API_KEY` (for analytics API calls), `MCP_AUTH_TOKEN` (for Cursor/Bearer auth), optional `RLM_SERVICE_URL`, optional `RLM_AUTH_TOKEN`
- **Auth:** Bearer token in the `Authorization` header must match `MCP_AUTH_TOKEN`. Outbound calls to analytics use `ANALYTICS_API_KEY`; outbound calls to RLM use `RLM_AUTH_TOKEN` when configured.
- **Tools:** analytics queries, runtime-log queries, bridge-failure summaries, service health, and RLM trigger/read flows for report, hypothesis, conclusion, and chat analysis.
- Cursor and other MCP clients point at this service URL (e.g. in `.cursor/mcp.json`). Set `server.railway_mcp_url` in local config to this URL.
