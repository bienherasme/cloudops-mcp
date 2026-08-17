"""Provider capability interfaces.

Each Protocol maps to one telemetry domain, mirroring how real vendors split
these APIs (CloudWatch Logs vs CloudWatch Metrics are separate services;
Kubernetes events vs metrics-server are separate calls). None of these
declare a mutation method, by design.

Providers receive already-resolved bounds (time range, max items) and are
responsible for respecting them and reporting truncation, the same way a
real vendor call reports an incomplete page via a pagination cursor.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from cloudops_mcp.domain.models import (
    CollectionResult,
    DeploymentEvent,
    HealthSignal,
    LogEvent,
    MetricPoint,
    ProviderFailureReason,
    TimeRange,
)


class ProviderQueryError(Exception):
    """A provider adapter's classification of its own failed call.

    This is the only exception the services layer catches from a provider. A
    real adapter (e.g. the AWS CloudWatch logs/metrics providers) is responsible
    for turning auth/timeout/rate-limit/unavailable failures from its SDK into
    this, sanitized, before it crosses into services. Anything else that escapes
    a provider call - a KeyError, an AssertionError, a Pydantic ValidationError -
    is a bug, not telemetry, and is deliberately left uncaught so it surfaces as
    an error instead of being reported to the agent as ordinary FAILED/UNKNOWN
    operational data.
    """

    def __init__(self, reason: ProviderFailureReason, message: str | None = None) -> None:
        super().__init__(message or f"provider query failed: {reason.value}")
        self.reason = reason


class SimulatedProviderFailure(ProviderQueryError):
    """Raised only by fake providers, to simulate a specific real-world failure mode."""

    def __init__(self, reason: ProviderFailureReason) -> None:
        super().__init__(reason, message=f"simulated provider failure: {reason.value}")


class RawMetricSeries(BaseModel):
    """Provider-level metric data. No aggregates: those are always computed centrally
    by the metrics service, so every provider (fake or real) is judged the same way."""

    model_config = ConfigDict(frozen=True)

    metric_name: str
    unit: str
    points: list[MetricPoint]
    labels: dict[str, str] = Field(default_factory=dict)


class LogsProvider(Protocol):
    async def get_logs(
        self,
        *,
        provider_ref: str,
        service: str,
        environment: str,
        time_range: TimeRange,
        max_events: int,
    ) -> CollectionResult[LogEvent]: ...


class MetricsProvider(Protocol):
    async def get_metrics(
        self,
        *,
        provider_ref: str,
        metric_queries: dict[str, str],
        time_range: TimeRange,
        max_points: int,
    ) -> CollectionResult[RawMetricSeries]: ...


class DeploymentsProvider(Protocol):
    async def get_deployments(
        self,
        *,
        provider_ref: str,
        time_range: TimeRange,
        max_events: int,
    ) -> CollectionResult[DeploymentEvent]: ...


class HealthProvider(Protocol):
    async def get_health(self, *, provider_ref: str) -> CollectionResult[HealthSignal]: ...
