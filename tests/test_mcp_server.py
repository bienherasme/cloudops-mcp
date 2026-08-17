"""Real MCP client/server smoke test.

Crosses the actual protocol boundary via the SDK's in-process Client, the
smallest official way to do that without a stdio subprocess. This protects
the project's distinctive contract (typed tools -> structuredContent), not
any single tool's full schema, so assertions stay deliberately shallow.
"""

from __future__ import annotations

from mcp.client.client import Client

from cloudops_mcp.server import build_server

_EXPECTED_TOOLS = {
    "get_services",
    "get_service_health",
    "get_recent_deployments",
    "get_logs",
    "get_metrics",
    "get_operational_snapshot",
}


async def test_mcp_tools_are_discoverable_and_return_structured_content() -> None:
    server = build_server("healthy")

    async with Client(server) as client:
        tools = await client.list_tools()
        tool_names = {tool.name for tool in tools.tools}
        assert tool_names == _EXPECTED_TOOLS

        snapshot_tool = next(t for t in tools.tools if t.name == "get_operational_snapshot")
        assert snapshot_tool.output_schema is not None

        result = await client.call_tool(
            "get_operational_snapshot",
            {"service": "checkout-api", "environment": "production"},
        )

        assert result.is_error is False
        assert result.structured_content is not None
        assert set(result.structured_content) == {
            "service",
            "environment",
            "deployments",
            "metrics",
            "logs",
            "health",
        }
