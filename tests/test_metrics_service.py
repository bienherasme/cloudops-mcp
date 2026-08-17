"""Metrics service: canonical-name validation, defensive ordering, deterministic aggregates.

Uses a deliberately naive stub provider (wrong order, ignores max_points)
instead of FakeMetricsProvider. Points are fed out of chronological order on
purpose: "last" must mean the temporally most recent point, not whatever
position the provider happened to put it in.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cloudops_mcp.domain.identity import CapabilityBinding, ServiceCatalogEntry, ServiceRegistry
from cloudops_mcp.domain.models import (
    CapabilityName,
    CollectionResult,
    CollectionStatus,
    MetricPoint,
    TimeRange,
)
from cloudops_mcp.errors import InvalidArgumentError
from cloudops_mcp.providers.base import RawMetricSeries
from cloudops_mcp.services import metrics_service

_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


class _NaiveMetricsProvider:
    """Returns exactly what it's given, in whatever order, ignoring max_points."""

    def __init__(self, series_by_vendor_query: dict[str, RawMetricSeries]) -> None:
        self._series_by_vendor_query = series_by_vendor_query

    async def get_metrics(
        self,
        *,
        provider_ref: str,
        metric_queries: dict[str, str],
        time_range: TimeRange,
        max_points: int,
    ) -> CollectionResult[RawMetricSeries]:
        data = [
            RawMetricSeries(metric_name=name, unit=raw.unit, points=raw.points, labels=raw.labels)
            for name, vendor_query in metric_queries.items()
            if (raw := self._series_by_vendor_query.get(vendor_query)) is not None
        ]
        return CollectionResult(status=CollectionStatus.SUCCESS, data=data)


def _registry() -> ServiceRegistry:
    return ServiceRegistry(
        [
            ServiceCatalogEntry(
                service="checkout-api",
                environment="production",
                capability_bindings={
                    CapabilityName.METRICS: CapabilityBinding(
                        "fake_metrics",
                        "checkout-api-metrics",
                        metric_map={"error_rate": "ErrorRate"},
                    )
                },
            )
        ]
    )


async def test_metrics_defensive_ordering_aggregates_and_unknown_name() -> None:
    # deliberately scrambled: 30, 10, 40, 20 instead of chronological order
    points = [
        MetricPoint(timestamp=_NOW.replace(hour=11, minute=minute), value=float(minute))
        for minute in (30, 10, 40, 20)
    ]
    raw = RawMetricSeries(metric_name="error_rate", unit="percent", points=points)
    provider = _NaiveMetricsProvider({"ErrorRate": raw})

    result = await metrics_service.get_metrics(
        _registry(),
        provider,
        service="checkout-api",
        environment="production",
        metric_names=["error_rate"],
        time_range=None,
        max_points=2,
        include_points=True,
        now=_NOW,
    )

    assert result.collection is not None
    assert result.collection.status == CollectionStatus.PARTIAL
    assert result.collection.truncated is True
    assert result.collection.truncation_reason == "max_points_reached"
    series = result.collection.data[0]
    # capping keeps the 2 most recent points (30, 40), not the first 2 the
    # provider happened to hand over (30, 10)
    assert [p.timestamp.minute for p in series.points] == [30, 40]
    assert series.aggregates.minimum == 30.0
    assert series.aggregates.maximum == 40.0
    assert series.aggregates.average == 35.0
    assert series.aggregates.last == 40.0

    with pytest.raises(InvalidArgumentError):
        await metrics_service.get_metrics(
            _registry(),
            provider,
            service="checkout-api",
            environment="production",
            metric_names=["not_a_real_metric"],
            time_range=None,
            max_points=None,
            include_points=False,
            now=_NOW,
        )
