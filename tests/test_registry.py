"""ServiceRegistry: identity resolution, capability availability, and config validation."""

from __future__ import annotations

import pytest

from cloudops_mcp.domain.identity import CapabilityBinding, ServiceCatalogEntry, ServiceRegistry
from cloudops_mcp.domain.models import CapabilityAvailability, CapabilityName
from cloudops_mcp.services import catalog_service


def test_capability_availability_and_catalog_summary() -> None:
    registry = ServiceRegistry(
        [
            ServiceCatalogEntry(
                service="checkout-api",
                environment="production",
                capability_bindings={
                    CapabilityName.LOGS: CapabilityBinding("fake_logs", "checkout-api-logs"),
                    CapabilityName.METRICS: CapabilityBinding(
                        "fake_metrics",
                        "checkout-api-metrics",
                        metric_map={"error_rate": "ErrorRate"},
                    ),
                    # deployments and health intentionally unbound
                },
            )
        ]
    )

    entry = registry.get("checkout-api", "production")
    assert entry is not None
    assert entry.availability(CapabilityName.LOGS) is CapabilityAvailability.CONFIGURED
    assert entry.availability(CapabilityName.DEPLOYMENTS) is CapabilityAvailability.NOT_CONFIGURED
    # available_metrics is derived from the metrics binding's metric_map, not separately configured
    assert entry.available_metrics == ("error_rate",)
    assert registry.get("checkout-api", "staging") is None

    result = catalog_service.list_services(registry, environment=None, max_services=None)
    assert result.returned_count == 1
    summary = result.services[0]
    assert summary.capabilities[CapabilityName.LOGS] is CapabilityAvailability.CONFIGURED
    assert summary.capabilities[CapabilityName.HEALTH] is CapabilityAvailability.NOT_CONFIGURED
    assert summary.available_metrics == ["error_rate"]
    # vendor-specific provider_ref never leaks into the public summary
    assert "checkout-api-logs" not in summary.model_dump_json()

    # snapshot_metrics is validated at construction time, derived from metric_map
    metrics_binding = CapabilityBinding("fake_metrics", "ref", metric_map={"a": "A", "b": "B"})
    invalid_configs: list[dict[CapabilityName, CapabilityBinding]] = [
        {CapabilityName.METRICS: metrics_binding},  # exceeds the snapshot metric hard cap
        {CapabilityName.METRICS: metrics_binding},  # contains a metric not present in metric_map
        {},  # metrics not configured at all: snapshot_metrics must be empty
    ]
    invalid_snapshot_metrics = [
        ("a", "b", "c", "d", "e", "f"),
        ("a", "c"),
        ("a",),
    ]
    for bindings, snapshot_metrics in zip(invalid_configs, invalid_snapshot_metrics, strict=True):
        with pytest.raises(ValueError):
            ServiceCatalogEntry(
                service="checkout-api",
                environment="production",
                capability_bindings=bindings,
                snapshot_metrics=snapshot_metrics,
            )
