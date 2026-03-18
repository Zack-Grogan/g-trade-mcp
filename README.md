# g-trade-mcp

MCP server on Railway. Same protocol and tool names as the previous local MCP; backend is Postgres/analytics API. Part of the G-Trade Railway project; see [Architecture overview](https://github.com/Zack-Grogan/G-Trade/blob/main/docs/Architecture-Overview.md) and [OPERATOR](https://github.com/Zack-Grogan/G-Trade/blob/main/docs/OPERATOR.md) for architecture and Cursor setup.

Deployed service names may still be `grogan-trade-mcp` until renamed in Railway.

- **Env:** `ANALYTICS_API_URL`, `ANALYTICS_API_KEY` (single-operator auth)
- Cursor and other MCP clients point at this service URL (e.g. in `.cursor/mcp.json`). Set `server.railway_mcp_url` in local config to this URL.
