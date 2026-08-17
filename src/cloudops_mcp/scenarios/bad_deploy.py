"""Bad-deploy scenario: a deployment, then an error-rate shift, then timeout logs.

This only seeds correlated facts at fixed timestamps. It is up to the
consuming agent to draw any causal conclusion, the fake data itself makes
none.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cloudops_mcp.domain.identity import CapabilityBinding, ServiceCatalogEntry, ServiceRegistry
from cloudops_mcp.domain.models import (
    CapabilityName,
    DeploymentEvent,
    DeploymentStatus,
    HealthSignal,
    HealthStatus,
    LogEvent,
    MetricPoint,
)
from cloudops_mcp.providers.base import RawMetricSeries
from cloudops_mcp.providers.fake.deployments import FakeDeploymentsProvider
from cloudops_mcp.providers.fake.health import FakeHealthProvider
from cloudops_mcp.providers.fake.logs import FakeLogsProvider
from cloudops_mcp.providers.fake.metrics import FakeMetricsProvider
from cloudops_mcp.scenarios import ScenarioContext

_SERVICE = "checkout-api"
_ENVIRONMENT = "production"
_LOGS_REF = "checkout-api-logs"
_METRICS_REF = "checkout-api-metrics"
_DEPLOY_REF = "checkout-api-deploy"
_HEALTH_REF = "checkout-api-health"

_NOW = datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
_DEPLOY_TIME = datetime(2026, 1, 15, 14, 3, tzinfo=UTC)


def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 1, 15, hour, minute, tzinfo=UTC)


def build() -> ScenarioContext:
    registry = ServiceRegistry(
        [
            ServiceCatalogEntry(
                service=_SERVICE,
                environment=_ENVIRONMENT,
                capability_bindings={
                    CapabilityName.LOGS: CapabilityBinding("fake_logs", _LOGS_REF),
                    CapabilityName.METRICS: CapabilityBinding(
                        "fake_metrics",
                        _METRICS_REF,
                        metric_map={
                            "request_count": "RequestCount",
                            "error_rate": "ErrorRate",
                            "latency_p99": "LatencyP99",
                        },
                    ),
                    CapabilityName.DEPLOYMENTS: CapabilityBinding("fake_deployments", _DEPLOY_REF),
                    CapabilityName.HEALTH: CapabilityBinding("fake_health", _HEALTH_REF),
                },
                snapshot_metrics=("error_rate", "latency_p99", "request_count"),
            )
        ]
    )

    logs_provider = FakeLogsProvider(
        {
            _LOGS_REF: [
                LogEvent(
                    timestamp=_at(13, 40),
                    level="INFO",
                    service=_SERVICE,
                    environment=_ENVIRONMENT,
                    message="request completed in 40ms",
                    source="app",
                    provider="fake_logs",
                ),
                LogEvent(
                    timestamp=_at(14, 7),
                    level="ERROR",
                    service=_SERVICE,
                    environment=_ENVIRONMENT,
                    message="upstream timeout calling payments-service",
                    source="app",
                    provider="fake_logs",
                ),
                LogEvent(
                    timestamp=_at(14, 8),
                    level="ERROR",
                    service=_SERVICE,
                    environment=_ENVIRONMENT,
                    message="upstream timeout calling payments-service",
                    source="app",
                    provider="fake_logs",
                ),
                LogEvent(
                    timestamp=_at(14, 12),
                    level="ERROR",
                    service=_SERVICE,
                    environment=_ENVIRONMENT,
                    message="upstream timeout calling payments-service",
                    source="app",
                    provider="fake_logs",
                ),
            ]
        }
    )

    metrics_provider = FakeMetricsProvider(
        {
            _METRICS_REF: {
                "RequestCount": RawMetricSeries(
                    metric_name="request_count",
                    unit="count",
                    points=[
                        MetricPoint(timestamp=_at(13, 35), value=130.0),
                        MetricPoint(timestamp=_at(14, 0), value=131.0),
                        MetricPoint(timestamp=_at(14, 6), value=125.0),
                        MetricPoint(timestamp=_at(14, 10), value=90.0),
                        MetricPoint(timestamp=_at(14, 20), value=85.0),
                    ],
                ),
                "ErrorRate": RawMetricSeries(
                    metric_name="error_rate",
                    unit="percent",
                    points=[
                        MetricPoint(timestamp=_at(13, 35), value=0.4),
                        MetricPoint(timestamp=_at(14, 0), value=0.6),
                        MetricPoint(timestamp=_at(14, 6), value=3.0),
                        MetricPoint(timestamp=_at(14, 10), value=7.5),
                        MetricPoint(timestamp=_at(14, 20), value=8.0),
                    ],
                ),
                "LatencyP99": RawMetricSeries(
                    metric_name="latency_p99",
                    unit="milliseconds",
                    points=[
                        MetricPoint(timestamp=_at(13, 35), value=90.0),
                        MetricPoint(timestamp=_at(14, 0), value=95.0),
                        MetricPoint(timestamp=_at(14, 6), value=140.0),
                        MetricPoint(timestamp=_at(14, 10), value=310.0),
                        MetricPoint(timestamp=_at(14, 20), value=330.0),
                    ],
                ),
            }
        }
    )

    deployments_provider = FakeDeploymentsProvider(
        {
            _DEPLOY_REF: [
                DeploymentEvent(
                    service=_SERVICE,
                    environment=_ENVIRONMENT,
                    version="v1.43.0",
                    timestamp=_DEPLOY_TIME,
                    status=DeploymentStatus.SUCCEEDED,
                    source="ci",
                    provider="fake_deployments",
                )
            ]
        }
    )

    health_provider = FakeHealthProvider(
        {
            _HEALTH_REF: HealthSignal(
                service=_SERVICE,
                environment=_ENVIRONMENT,
                reported_status=HealthStatus.DEGRADED,
                source="target-group-health",
                provider="fake_health",
                timestamp=_NOW,
            )
        }
    )

    return ScenarioContext(
        name="bad_deploy",
        registry=registry,
        logs_provider=logs_provider,
        metrics_provider=metrics_provider,
        deployments_provider=deployments_provider,
        health_provider=health_provider,
        now=_NOW,
    )
