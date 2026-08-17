"""Deterministic metrics lookup: canonical-name validation, aggregate computation, capping.

Aggregates (min/max/average/last) are always computed here, never supplied by
a provider, so every vendor is normalized the same way. Points are always
sorted ascending by timestamp before capping and aggregating, so "last" means
the temporally most recent point, not whatever order a provider happened to
return.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cloudops_mcp.bounds import (
    METRICS_DEFAULT_POINTS,
    METRICS_DEFAULT_TIME_SPAN,
    METRICS_HARD_CAP_POINTS,
    METRICS_HARD_CAP_TIME_SPAN,
    clamp_max_items,
    resolve_time_range,
)
from cloudops_mcp.domain.identity import CapabilityBinding, ServiceRegistry
from cloudops_mcp.domain.models import (
    CapabilityAvailability,
    CapabilityName,
    CollectionResult,
    CollectionStatus,
    MetricAggregates,
    MetricPoint,
    MetricSeries,
    MetricsResult,
    ProviderFailureReason,
    QueryBounds,
    TimeRange,
)
from cloudops_mcp.errors import InvalidArgumentError
from cloudops_mcp.providers.base import MetricsProvider, ProviderQueryError, RawMetricSeries


def compute_aggregates(points: list[MetricPoint]) -> MetricAggregates:
    values = [p.value for p in points]
    return MetricAggregates(
        minimum=min(values),
        maximum=max(values),
        average=sum(values) / len(values),
        last=points[-1].value,
    )


def _defensive_points(
    points: list[MetricPoint], *, applied_max: int
) -> tuple[list[MetricPoint], bool]:
    """Sort ascending by timestamp and cap to applied_max, never trusting the provider alone.

    Keeps the most recent points when capping is needed: an agent asking for a
    bounded window of a metric almost always wants what's happening now, not
    the oldest points in the window.
    """
    ordered = sorted(points, key=lambda p: p.timestamp)
    if len(ordered) <= applied_max:
        return ordered, False
    return ordered[-applied_max:], True


def _failed(
    binding: CapabilityBinding,
    applied_bounds: QueryBounds,
    requested_bounds: QueryBounds,
    reason: ProviderFailureReason,
) -> MetricsResult:
    return MetricsResult(
        availability=CapabilityAvailability.CONFIGURED,
        collection=CollectionResult(
            status=CollectionStatus.FAILED,
            failure_reason=reason,
            provider=binding.provider_name,
            applied_bounds=applied_bounds,
            requested_bounds=requested_bounds,
        ),
    )


async def get_metrics(
    registry: ServiceRegistry,
    provider: MetricsProvider,
    *,
    service: str,
    environment: str,
    metric_names: list[str],
    time_range: TimeRange | None,
    max_points: int | None,
    include_points: bool,
    now: datetime | None = None,
) -> MetricsResult:
    entry = registry.get(service, environment)
    if entry is None:
        raise InvalidArgumentError(f"unknown service {service!r} in environment {environment!r}")

    binding = entry.binding(CapabilityName.METRICS)
    if binding is None:
        return MetricsResult(availability=CapabilityAvailability.NOT_CONFIGURED, collection=None)

    unknown = sorted(set(metric_names) - set(entry.available_metrics))
    if unknown:
        raise InvalidArgumentError(
            f"unknown metric_names {unknown} for {service}/{environment}; "
            f"available: {sorted(entry.available_metrics)}"
        )

    now = now or datetime.now(UTC)
    applied_tr, requested_tr = resolve_time_range(
        time_range,
        default_span=METRICS_DEFAULT_TIME_SPAN,
        hard_cap_span=METRICS_HARD_CAP_TIME_SPAN,
        now=now,
    )
    applied_max, requested_max = clamp_max_items(
        max_points, default=METRICS_DEFAULT_POINTS, hard_cap=METRICS_HARD_CAP_POINTS
    )
    applied_bounds = QueryBounds(time_range=applied_tr, max_items=applied_max)
    requested_bounds = QueryBounds(time_range=requested_tr, max_items=requested_max)

    if not metric_names:
        return MetricsResult(
            availability=CapabilityAvailability.CONFIGURED,
            collection=CollectionResult(
                status=CollectionStatus.EMPTY,
                provider=binding.provider_name,
                applied_bounds=applied_bounds,
                requested_bounds=requested_bounds,
            ),
        )

    metric_queries = {name: binding.metric_map[name] for name in metric_names}

    raw_result: CollectionResult[RawMetricSeries]
    try:
        raw_result = await provider.get_metrics(
            provider_ref=binding.provider_ref,
            metric_queries=metric_queries,
            time_range=applied_tr,
            max_points=applied_max,
        )
    except ProviderQueryError as exc:
        return _failed(binding, applied_bounds, requested_bounds, exc.reason)

    if raw_result.status == CollectionStatus.FAILED:
        reason = raw_result.failure_reason or ProviderFailureReason.UNKNOWN
        return _failed(binding, applied_bounds, requested_bounds, reason)

    series_list: list[MetricSeries] = []
    any_defensively_truncated = False
    for raw in raw_result.data:
        capped_points, series_truncated = _defensive_points(raw.points, applied_max=applied_max)
        if not capped_points:
            continue
        any_defensively_truncated = any_defensively_truncated or series_truncated
        series_list.append(
            MetricSeries(
                metric_name=raw.metric_name,
                unit=raw.unit,
                aggregates=compute_aggregates(capped_points),
                points=list(capped_points) if include_points else [],
                labels=raw.labels,
                provider=binding.provider_name,
            )
        )

    truncated = any_defensively_truncated or raw_result.truncated
    truncation_reason = (
        "max_points_reached"
        if any_defensively_truncated
        else (raw_result.truncation_reason if raw_result.truncated else None)
    )

    # truncated wins even with no series yet, see logs_service for why.
    status = (
        CollectionStatus.PARTIAL
        if truncated
        else (CollectionStatus.EMPTY if not series_list else CollectionStatus.SUCCESS)
    )

    return MetricsResult(
        availability=CapabilityAvailability.CONFIGURED,
        collection=CollectionResult(
            status=status,
            data=series_list,
            provider=binding.provider_name,
            source=raw_result.source,
            truncated=truncated,
            truncation_reason=truncation_reason,
            applied_bounds=applied_bounds,
            requested_bounds=requested_bounds,
        ),
    )
