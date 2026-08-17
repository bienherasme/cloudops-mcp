"""CloudOps MCP server entrypoint.

Local-first: stdio transport only, no remote deployment in this version.

Two composition paths, selected by config.mode(): fake scenarios (default)
or a real AWS CloudWatch config. AWS provider modules import boto3, so they
are only imported here inside _build_aws_context, lazily - the fake-mode
path never touches boto3, and boto3 need not even be installed for it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from mcp.server.mcpserver import MCPServer

from cloudops_mcp import config
from cloudops_mcp.scenarios import ScenarioContext, load_scenario
from cloudops_mcp.tools.register import register_tools

INSTRUCTIONS = (
    "Read-only operational context for infrastructure services: catalog, health, "
    "deployments, logs, and metrics. Every telemetry query is bounded. Tools report "
    "normalized facts and explicit data-availability status, never severity judgments "
    "or causal conclusions."
)


def build_server(scenario_name: str | None = None) -> MCPServer:
    ctx = _build_context(scenario_name)
    server = MCPServer("cloudops-mcp", instructions=INSTRUCTIONS)
    register_tools(server, ctx)
    return server


def _build_context(scenario_name: str | None) -> ScenarioContext:
    if config.mode() == "aws":
        return _build_aws_context()
    return load_scenario(scenario_name or config.scenario_name())


def _build_aws_context() -> ScenarioContext:
    # Lazy on purpose: these import boto3, fake mode must work without it installed.
    from cloudops_mcp.providers.aws.config import build_registry, load_aws_config
    from cloudops_mcp.providers.aws.logs import AwsCloudWatchLogsProvider
    from cloudops_mcp.providers.aws.metrics import AwsCloudWatchMetricsProvider
    from cloudops_mcp.providers.fake.deployments import FakeDeploymentsProvider
    from cloudops_mcp.providers.fake.health import FakeHealthProvider

    aws_config = load_aws_config(config.aws_config_path())
    registry, log_group_by_ref, metrics_config_by_ref = build_registry(aws_config)

    return ScenarioContext(
        name="aws",
        registry=registry,
        logs_provider=AwsCloudWatchLogsProvider(log_group_by_ref),
        metrics_provider=AwsCloudWatchMetricsProvider(metrics_config_by_ref),
        # deployments/health AWS adapters aren't implemented yet: no registry
        # entry ever binds these capabilities in AWS mode, so they stay
        # NOT_CONFIGURED and these placeholders are never actually called.
        deployments_provider=FakeDeploymentsProvider({}),
        health_provider=FakeHealthProvider({}),
        now=datetime.now(UTC),
    )


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
