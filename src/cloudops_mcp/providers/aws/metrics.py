"""AWS CloudWatch metrics provider: GetMetricData only, MetricStat queries.

No metric math expressions, no Metrics Insights, no ListMetrics/dynamic
discovery. Read-only via a single IAM action: cloudwatch:GetMetricData.

A single GetMetricData call per get_metrics invocation, no pagination loop:
if AWS caps the response (NextToken present) or marks a series PartialData,
that is reported as PARTIAL rather than chased with further requests.

IAM scoping note: unlike logs:FilterLogEvents (which can be scoped to a
log-group ARN), cloudwatch:GetMetricData has no resource-level permissions in
AWS's own authorization model, per the Service Authorization Reference.
Metrics aren't ARN-addressable for this action, so the IAM policy for it must
use Resource: "*". This is an AWS limitation, not a choice made here.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

import boto3  # type: ignore[import-untyped]

from cloudops_mcp.domain.models import (
    CollectionResult,
    CollectionStatus,
    MetricPoint,
    ProviderFailureReason,
    TimeRange,
)
from cloudops_mcp.providers.aws.client import build_botocore_config
from cloudops_mcp.providers.aws.config import CloudWatchMetricsServiceConfig
from cloudops_mcp.providers.aws.errors import KNOWN_AWS_EXCEPTIONS, classify_aws_exception
from cloudops_mcp.providers.base import ProviderQueryError, RawMetricSeries

# AWS caps a single GetMetricData request at 100,800 total datapoints across
# every requested series, this is that ceiling, not a bound we invented.
_AWS_MAX_DATAPOINTS = 100_800


class _CloudWatchClient(Protocol):
    """The one boto3 CloudWatch client method this provider calls."""

    def get_metric_data(self, **kwargs: Any) -> dict[str, Any]: ...


def _query_id(index: int) -> str:
    # AWS requires: starts with a lowercase letter, alphanumeric/underscore only.
    return f"q{index}"


class AwsCloudWatchMetricsProvider:
    def __init__(
        self,
        metrics_config_by_ref: dict[str, CloudWatchMetricsServiceConfig],
        client: _CloudWatchClient | None = None,
    ) -> None:
        self._metrics_config_by_ref = metrics_config_by_ref
        self._client: _CloudWatchClient = client or boto3.session.Session().client(
            "cloudwatch", config=build_botocore_config()
        )

    async def get_metrics(
        self,
        *,
        provider_ref: str,
        metric_queries: dict[str, str],
        time_range: TimeRange,
        max_points: int,
    ) -> CollectionResult[RawMetricSeries]:
        # a missing entry here is our own composition wiring being wrong, not
        # an AWS failure, so this is a plain KeyError, not a ProviderQueryError
        service_config = self._metrics_config_by_ref[provider_ref]

        id_to_canonical: dict[str, str] = {}
        unit_by_id: dict[str, str] = {}
        aws_queries: list[dict[str, Any]] = []
        for index, (canonical_name, vendor_query) in enumerate(metric_queries.items()):
            query_config = service_config.queries[vendor_query]
            query_id = _query_id(index)
            id_to_canonical[query_id] = canonical_name
            unit_by_id[query_id] = query_config.unit
            aws_queries.append(
                {
                    "Id": query_id,
                    "MetricStat": {
                        "Metric": {
                            "Namespace": query_config.namespace,
                            "MetricName": query_config.metric_name,
                            "Dimensions": [
                                {"Name": name, "Value": value}
                                for name, value in query_config.dimensions.items()
                            ],
                        },
                        "Period": query_config.period_seconds,
                        "Stat": query_config.statistic,
                    },
                    "ReturnData": True,
                }
            )

        max_datapoints = min(max_points * len(aws_queries), _AWS_MAX_DATAPOINTS)

        try:
            response = await asyncio.to_thread(
                self._client.get_metric_data,
                MetricDataQueries=aws_queries,
                StartTime=time_range.start,
                EndTime=time_range.end,
                ScanBy="TimestampDescending",
                MaxDatapoints=max_datapoints,
            )
        except KNOWN_AWS_EXCEPTIONS as exc:
            raise ProviderQueryError(classify_aws_exception(exc)) from exc

        series_list: list[RawMetricSeries] = []
        any_partial = False
        forbidden_without_data = False
        internal_error_without_data = False

        for result in response.get("MetricDataResults", []):
            query_id = result.get("Id", "")
            result_canonical_name = id_to_canonical.get(query_id)
            if result_canonical_name is None:
                continue  # response referenced an id we didn't ask for; skip defensively

            timestamps = result.get("Timestamps", [])
            values = result.get("Values", [])
            status_code = result.get("StatusCode")

            if not timestamps:
                if status_code == "Forbidden":
                    forbidden_without_data = True
                elif status_code == "InternalError":
                    internal_error_without_data = True
                continue

            points = [
                MetricPoint(timestamp=ts, value=float(value))
                for ts, value in zip(timestamps, values, strict=True)
            ]
            if status_code == "PartialData":
                any_partial = True
            series_list.append(
                RawMetricSeries(
                    metric_name=result_canonical_name, unit=unit_by_id[query_id], points=points
                )
            )

        has_more = bool(response.get("NextToken"))

        if not series_list:
            if forbidden_without_data:
                raise ProviderQueryError(ProviderFailureReason.AUTH_ERROR)
            if internal_error_without_data:
                raise ProviderQueryError(ProviderFailureReason.UNAVAILABLE)
            if has_more:
                # extraction is known incomplete even though nothing usable
                # came back yet, that's PARTIAL, not EMPTY
                return CollectionResult(
                    status=CollectionStatus.PARTIAL,
                    truncated=True,
                    truncation_reason="pagination_not_exhausted",
                )
            return CollectionResult(status=CollectionStatus.EMPTY)

        any_failed_series = forbidden_without_data or internal_error_without_data
        truncated = any_partial or any_failed_series or has_more
        truncation_reason = (
            "partial_data_from_provider"
            if any_partial
            else (
                "partial_series_unavailable"
                if any_failed_series
                else ("max_datapoints_reached" if has_more else None)
            )
        )

        status = CollectionStatus.PARTIAL if truncated else CollectionStatus.SUCCESS
        return CollectionResult(
            status=status,
            data=series_list,
            truncated=truncated,
            truncation_reason=truncation_reason,
        )
