"""Deterministic fake MetricsProvider backed by canned per-scenario datasets."""

from __future__ import annotations

from cloudops_mcp.domain.models import (
    CollectionResult,
    CollectionStatus,
    ProviderFailureReason,
    TimeRange,
)
from cloudops_mcp.providers.base import RawMetricSeries, SimulatedProviderFailure


class FakeMetricsProvider:
    def __init__(
        self,
        series_by_ref: dict[str, dict[str, RawMetricSeries]],
        failing_refs: dict[str, ProviderFailureReason] | None = None,
    ) -> None:
        # series_by_ref[provider_ref][vendor_query] -> full, uncapped RawMetricSeries
        self._series_by_ref = series_by_ref
        self._failing_refs = failing_refs or {}

    async def get_metrics(
        self,
        *,
        provider_ref: str,
        metric_queries: dict[str, str],
        time_range: TimeRange,
        max_points: int,
    ) -> CollectionResult[RawMetricSeries]:
        if provider_ref in self._failing_refs:
            raise SimulatedProviderFailure(self._failing_refs[provider_ref])

        available = self._series_by_ref.get(provider_ref, {})
        result_series: list[RawMetricSeries] = []
        truncated = False

        for canonical_name, vendor_query in metric_queries.items():
            raw = available.get(vendor_query)
            if raw is None:
                continue
            filtered = [p for p in raw.points if time_range.start <= p.timestamp < time_range.end]
            if not filtered:
                continue
            if len(filtered) > max_points:
                truncated = True
                # keep the most recent max_points, regardless of the dataset's own order
                filtered = sorted(filtered, key=lambda p: p.timestamp)[-max_points:]
            result_series.append(
                RawMetricSeries(
                    metric_name=canonical_name,
                    unit=raw.unit,
                    points=filtered,
                    labels=raw.labels,
                )
            )

        status = (
            CollectionStatus.EMPTY
            if not result_series
            else (CollectionStatus.PARTIAL if truncated else CollectionStatus.SUCCESS)
        )
        return CollectionResult(
            status=status,
            data=result_series,
            truncated=truncated,
            truncation_reason="max_points_reached" if truncated else None,
        )
