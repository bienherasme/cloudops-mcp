"""MCP tool registration.

Each tool is a thin wrapper: call the matching service, translate
InvalidArgumentError into a sanitized MCP tool error, and (for primitive,
single-capability tools only) translate a fully FAILED capability into a
tool error too, since there is nothing else useful to return in that case.
get_operational_snapshot never does this: a FAILED or NOT_CONFIGURED section
stays in its structured result alongside whatever else succeeded.

Any exception that isn't InvalidArgumentError or a deliberate ToolError is a
bug, not something safe to describe to a caller: its message never crosses
the tool boundary, only a fixed generic ToolError does. The original
exception is chained with `from exc` for local debugging, not for display.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from cloudops_mcp.domain.models import (
    CollectionStatus,
    DeploymentsResult,
    HealthResult,
    LogsResult,
    MetricsResult,
    OperationalSnapshot,
    ServiceCatalogResult,
    TimeRange,
)
from cloudops_mcp.errors import InvalidArgumentError
from cloudops_mcp.scenarios import ScenarioContext
from cloudops_mcp.services import (
    catalog_service,
    deployment_service,
    health_service,
    logs_service,
    metrics_service,
    snapshot_service,
)

_PrimitiveResult = HealthResult | LogsResult | MetricsResult | DeploymentsResult

_INTERNAL_FAILURE_MESSAGE = "internal tool failure"


@contextmanager
def _sanitize_tool_errors() -> Iterator[None]:
    try:
        yield
    except InvalidArgumentError as exc:
        raise ToolError(str(exc)) from exc
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(_INTERNAL_FAILURE_MESSAGE) from exc


def _raise_if_failed(result: _PrimitiveResult) -> None:
    collection = result.collection
    if collection is not None and collection.status == CollectionStatus.FAILED:
        reason = collection.failure_reason.value if collection.failure_reason else "unknown"
        raise ToolError(f"{result.capability.value} query failed: {reason}")


def register_tools(server: MCPServer, ctx: ScenarioContext) -> None:
    @server.tool(description="List known services and which capabilities are configured for each.")
    async def get_services(
        environment: str | None = None, max_services: int | None = None
    ) -> ServiceCatalogResult:
        with _sanitize_tool_errors():
            return catalog_service.list_services(
                ctx.registry, environment=environment, max_services=max_services
            )

    @server.tool(description="Provider-reported health signal for a service, never inferred")
    async def get_service_health(service: str, environment: str) -> HealthResult:
        with _sanitize_tool_errors():
            result = await health_service.get_service_health(
                ctx.registry, ctx.health_provider, service=service, environment=environment
            )
            _raise_if_failed(result)
            return result

    @server.tool(description="Get recent deployment events for a service, bounded by time/count")
    async def get_recent_deployments(
        service: str,
        environment: str,
        time_range: TimeRange | None = None,
        max_events: int | None = None,
    ) -> DeploymentsResult:
        with _sanitize_tool_errors():
            result = await deployment_service.get_recent_deployments(
                ctx.registry,
                ctx.deployments_provider,
                service=service,
                environment=environment,
                time_range=time_range,
                max_events=max_events,
                now=ctx.now,
            )
            _raise_if_failed(result)
            return result

    @server.tool(
        description=(
            "Get log events for a service, bounded by time range, count, and message length. "
            "Message content is opaque and never interpreted."
        )
    )
    async def get_logs(
        service: str,
        environment: str,
        time_range: TimeRange | None = None,
        max_events: int | None = None,
    ) -> LogsResult:
        with _sanitize_tool_errors():
            result = await logs_service.get_logs(
                ctx.registry,
                ctx.logs_provider,
                service=service,
                environment=environment,
                time_range=time_range,
                max_events=max_events,
                now=ctx.now,
            )
            _raise_if_failed(result)
            return result

    @server.tool(
        description=(
            "Get metric series for a service. metric_names must be canonical names from "
            "get_services' available_metrics for that service. Always includes deterministic "
            "aggregates (min/max/average/last); raw points only when include_points=True, bounded."
        )
    )
    async def get_metrics(
        service: str,
        environment: str,
        metric_names: list[str],
        time_range: TimeRange | None = None,
        max_points: int | None = None,
        include_points: bool = False,
    ) -> MetricsResult:
        with _sanitize_tool_errors():
            result = await metrics_service.get_metrics(
                ctx.registry,
                ctx.metrics_provider,
                service=service,
                environment=environment,
                metric_names=metric_names,
                time_range=time_range,
                max_points=max_points,
                include_points=include_points,
                now=ctx.now,
            )
            _raise_if_failed(result)
            return result

    @server.tool(
        description=(
            "Get a bounded operational snapshot for a service: recent deployments, its "
            "configured snapshot metrics, recent logs, and health. Each section reports its own "
            "status independently; a failure or unconfigured capability in one section never "
            "hides the others. Reports facts only, never a root cause."
        )
    )
    async def get_operational_snapshot(
        service: str, environment: str, time_range: TimeRange | None = None
    ) -> OperationalSnapshot:
        with _sanitize_tool_errors():
            return await snapshot_service.get_operational_snapshot(
                ctx.registry,
                logs_provider=ctx.logs_provider,
                metrics_provider=ctx.metrics_provider,
                deployments_provider=ctx.deployments_provider,
                health_provider=ctx.health_provider,
                service=service,
                environment=environment,
                time_range=time_range,
                now=ctx.now,
            )
