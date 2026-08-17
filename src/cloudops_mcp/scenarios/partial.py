"""Partial scenario: one capability FAILED, one NOT_CONFIGURED, the rest SUCCESS.

Exercises the exact case a composite snapshot must survive without failing
as a whole: deployments SUCCESS, metrics SUCCESS, logs FAILED, health
NOT_CONFIGURED.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cloudops_mcp.domain.identity import CapabilityBinding, ServiceCatalogEntry, ServiceRegistry
from cloudops_mcp.domain.models import (
    CapabilityName,
    DeploymentEvent,
    DeploymentStatus,
    MetricPoint,
    ProviderFailureReason,
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

_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


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
                        metric_map={"error_rate": "ErrorRate"},
                    ),
                    CapabilityName.DEPLOYMENTS: CapabilityBinding("fake_deployments", _DEPLOY_REF),
                    # no health binding: health is NOT_CONFIGURED for this service
                },
                snapshot_metrics=("error_rate",),
            )
        ]
    )

    logs_provider = FakeLogsProvider(
        events_by_ref={},
        failing_refs={_LOGS_REF: ProviderFailureReason.TIMEOUT},
    )

    metrics_provider = FakeMetricsProvider(
        {
            _METRICS_REF: {
                "ErrorRate": RawMetricSeries(
                    metric_name="error_rate",
                    unit="percent",
                    points=[
                        MetricPoint(timestamp=datetime(2026, 1, 15, 11, 30, tzinfo=UTC), value=0.5),
                        MetricPoint(timestamp=datetime(2026, 1, 15, 11, 45, tzinfo=UTC), value=0.6),
                    ],
                )
            }
        }
    )

    deployments_provider = FakeDeploymentsProvider(
        {
            _DEPLOY_REF: [
                DeploymentEvent(
                    service=_SERVICE,
                    environment=_ENVIRONMENT,
                    version="v1.44.0",
                    timestamp=datetime(2026, 1, 15, 11, 0, tzinfo=UTC),
                    status=DeploymentStatus.SUCCEEDED,
                    source="ci",
                    provider="fake_deployments",
                )
            ]
        }
    )

    health_provider = FakeHealthProvider(signal_by_ref={})

    return ScenarioContext(
        name="partial",
        registry=registry,
        logs_provider=logs_provider,
        metrics_provider=metrics_provider,
        deployments_provider=deployments_provider,
        health_provider=health_provider,
        now=_NOW,
    )
