"""End-to-end snapshot behavior across the three fake scenarios.

These exercise the real scenario fixtures (not hand-rolled test data) to
confirm the composite snapshot behaves correctly against the same data the
demo and manual server run use.
"""

from __future__ import annotations

from cloudops_mcp.domain.models import (
    CapabilityAvailability,
    CollectionStatus,
    ProviderFailureReason,
)
from cloudops_mcp.scenarios import load_scenario
from cloudops_mcp.services import snapshot_service


async def test_healthy_snapshot() -> None:
    ctx = load_scenario("healthy")
    snapshot = await snapshot_service.get_operational_snapshot(
        ctx.registry,
        logs_provider=ctx.logs_provider,
        metrics_provider=ctx.metrics_provider,
        deployments_provider=ctx.deployments_provider,
        health_provider=ctx.health_provider,
        service="checkout-api",
        environment="production",
        time_range=None,
        now=ctx.now,
    )

    for section in (snapshot.deployments, snapshot.metrics, snapshot.logs, snapshot.health):
        assert section.availability is CapabilityAvailability.CONFIGURED
        assert section.collection is not None
        assert section.collection.status == CollectionStatus.SUCCESS

    assert snapshot.metrics.collection is not None
    metric_names = {series.metric_name for series in snapshot.metrics.collection.data}
    assert metric_names == {"error_rate", "latency_p99", "request_count"}

    assert snapshot.health.collection is not None
    assert snapshot.health.collection.data[0].reported_status == "healthy"


async def test_bad_deploy_snapshot_tells_facts_not_causes() -> None:
    ctx = load_scenario("bad_deploy")
    snapshot = await snapshot_service.get_operational_snapshot(
        ctx.registry,
        logs_provider=ctx.logs_provider,
        metrics_provider=ctx.metrics_provider,
        deployments_provider=ctx.deployments_provider,
        health_provider=ctx.health_provider,
        service="checkout-api",
        environment="production",
        time_range=None,
        now=ctx.now,
    )

    assert snapshot.deployments.collection is not None
    assert snapshot.deployments.collection.data[0].version == "v1.43.0"

    assert snapshot.metrics.collection is not None
    error_rate = next(s for s in snapshot.metrics.collection.data if s.metric_name == "error_rate")
    # a real shift is visible, but nothing here claims the deployment caused it
    assert error_rate.aggregates.maximum > error_rate.aggregates.minimum * 2

    assert snapshot.logs.collection is not None
    assert any("timeout" in e.message for e in snapshot.logs.collection.data)

    # only facts: the snapshot model has no severity/root-cause field to smuggle interpretation into
    expected_fields = {"service", "environment", "deployments", "metrics", "logs", "health"}
    assert set(type(snapshot).model_fields) == expected_fields


async def test_partial_snapshot_preserves_every_section() -> None:
    ctx = load_scenario("partial")
    snapshot = await snapshot_service.get_operational_snapshot(
        ctx.registry,
        logs_provider=ctx.logs_provider,
        metrics_provider=ctx.metrics_provider,
        deployments_provider=ctx.deployments_provider,
        health_provider=ctx.health_provider,
        service="checkout-api",
        environment="production",
        time_range=None,
        now=ctx.now,
    )

    assert snapshot.deployments.collection is not None
    assert snapshot.deployments.collection.status == CollectionStatus.SUCCESS

    assert snapshot.metrics.collection is not None
    assert snapshot.metrics.collection.status == CollectionStatus.SUCCESS

    assert snapshot.logs.availability is CapabilityAvailability.CONFIGURED
    assert snapshot.logs.collection is not None
    assert snapshot.logs.collection.status == CollectionStatus.FAILED
    assert snapshot.logs.collection.failure_reason == ProviderFailureReason.TIMEOUT

    assert snapshot.health.availability is CapabilityAvailability.NOT_CONFIGURED
    assert snapshot.health.collection is None
