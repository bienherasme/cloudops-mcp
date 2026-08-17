"""Bounded composite snapshot.

Composes the four primitive services with no logic of its own: same bounds,
same providers, same capability semantics. Metrics use each service's
snapshot_metrics configuration, never all available_metrics and never chosen
dynamically. A failure or NOT_CONFIGURED in one section never fails the
snapshot as a whole, each section keeps its own status.

The four primitive queries are independent of each other, so they run
concurrently via asyncio.gather rather than one after another.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from cloudops_mcp.domain.identity import ServiceRegistry
from cloudops_mcp.domain.models import OperationalSnapshot, TimeRange
from cloudops_mcp.errors import InvalidArgumentError
from cloudops_mcp.providers.base import (
    DeploymentsProvider,
    HealthProvider,
    LogsProvider,
    MetricsProvider,
)
from cloudops_mcp.services import deployment_service, health_service, logs_service, metrics_service


async def get_operational_snapshot(
    registry: ServiceRegistry,
    *,
    logs_provider: LogsProvider,
    metrics_provider: MetricsProvider,
    deployments_provider: DeploymentsProvider,
    health_provider: HealthProvider,
    service: str,
    environment: str,
    time_range: TimeRange | None,
    now: datetime | None = None,
) -> OperationalSnapshot:
    entry = registry.get(service, environment)
    if entry is None:
        raise InvalidArgumentError(f"unknown service {service!r} in environment {environment!r}")

    now = now or datetime.now(UTC)

    deployments, logs, metrics, health = await asyncio.gather(
        deployment_service.get_recent_deployments(
            registry,
            deployments_provider,
            service=service,
            environment=environment,
            time_range=time_range,
            max_events=None,
            now=now,
        ),
        logs_service.get_logs(
            registry,
            logs_provider,
            service=service,
            environment=environment,
            time_range=time_range,
            max_events=None,
            now=now,
        ),
        metrics_service.get_metrics(
            registry,
            metrics_provider,
            service=service,
            environment=environment,
            metric_names=list(entry.snapshot_metrics),
            time_range=time_range,
            max_points=None,
            include_points=False,
            now=now,
        ),
        health_service.get_service_health(
            registry, health_provider, service=service, environment=environment
        ),
    )

    return OperationalSnapshot(
        service=service,
        environment=environment,
        deployments=deployments,
        metrics=metrics,
        logs=logs,
        health=health,
    )
