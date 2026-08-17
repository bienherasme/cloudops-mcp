"""AWS CloudWatch metrics provider: query building, canonical mapping, PartialData, errors.

Uses a small stub CloudWatch client (not real boto3, not moto). Points come
back deliberately out of chronological order to confirm extraction doesn't
silently mismatch timestamps and values, sorting itself is metrics_service's
job (already covered there), not this provider's.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

pytest.importorskip("botocore", reason="aws extra not installed")

from botocore.exceptions import ClientError  # type: ignore[import-untyped]  # noqa: E402

from cloudops_mcp.domain.models import CollectionStatus, ProviderFailureReason, TimeRange
from cloudops_mcp.providers.aws.config import (
    CloudWatchMetricQueryConfig,
    CloudWatchMetricsServiceConfig,
)
from cloudops_mcp.providers.aws.metrics import AwsCloudWatchMetricsProvider
from cloudops_mcp.providers.base import ProviderQueryError

_REF = "checkout-api:production"
_WINDOW = TimeRange(
    start=datetime(2026, 1, 15, 11, 0, tzinfo=UTC), end=datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
)

_SERVICE_CONFIG = CloudWatchMetricsServiceConfig(
    provider="aws_cloudwatch",
    queries={
        "request_count": CloudWatchMetricQueryConfig(
            namespace="AWS/Lambda",
            metric_name="Invocations",
            statistic="Sum",
            period_seconds=60,
            unit="count",
            dimensions={"FunctionName": "checkout-api"},
        ),
        "latency_p99": CloudWatchMetricQueryConfig(
            namespace="AWS/Lambda",
            metric_name="Duration",
            statistic="p99",
            period_seconds=60,
            unit="milliseconds",
            dimensions={"FunctionName": "checkout-api"},
        ),
    },
)


class _StubCloudWatchClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response
        self.last_call: dict[str, Any] | None = None

    def get_metric_data(self, **kwargs: Any) -> dict[str, Any]:
        self.last_call = kwargs
        return self._response


async def test_aws_metrics_provider_query_building_partial_data_and_error_mapping() -> None:
    ts_early = datetime(2026, 1, 15, 11, 10, tzinfo=UTC)
    ts_late = datetime(2026, 1, 15, 11, 40, tzinfo=UTC)
    response: dict[str, Any] = {
        "MetricDataResults": [
            {
                "Id": "q0",
                "StatusCode": "Complete",
                # deliberately out of order: late timestamp first
                "Timestamps": [ts_late, ts_early],
                "Values": [40.0, 10.0],
            },
            {
                "Id": "q1",
                "StatusCode": "PartialData",
                "Timestamps": [ts_early],
                "Values": [95.0],
            },
        ]
    }
    client = _StubCloudWatchClient(response)
    provider = AwsCloudWatchMetricsProvider({_REF: _SERVICE_CONFIG}, client=client)

    result = await provider.get_metrics(
        provider_ref=_REF,
        metric_queries={"request_count": "request_count", "latency_p99": "latency_p99"},
        time_range=_WINDOW,
        max_points=500,
    )

    assert client.last_call is not None
    sent_queries = client.last_call["MetricDataQueries"]
    assert [q["Id"] for q in sent_queries] == ["q0", "q1"]
    first_stat = sent_queries[0]["MetricStat"]
    assert first_stat["Metric"]["Namespace"] == "AWS/Lambda"
    assert first_stat["Metric"]["Dimensions"] == [{"Name": "FunctionName", "Value": "checkout-api"}]
    assert first_stat["Period"] == 60
    assert first_stat["Stat"] == "Sum"
    assert "Unit" not in first_stat  # output_unit is ours, never sent as an AWS query filter

    # overall PARTIAL because one series came back PartialData
    assert result.status == CollectionStatus.PARTIAL
    assert result.truncated is True
    assert result.truncation_reason == "partial_data_from_provider"

    by_name = {s.metric_name: s for s in result.data}
    request_count = by_name["request_count"]
    assert request_count.unit == "count"
    # both points present and correctly paired despite arriving out of order
    assert {(p.timestamp, p.value) for p in request_count.points} == {
        (ts_late, 40.0),
        (ts_early, 10.0),
    }
    assert by_name["latency_p99"].unit == "milliseconds"

    # NextToken present but nothing usable came back yet: PARTIAL, not EMPTY,
    # because extraction is known incomplete
    no_data_response: dict[str, Any] = {"MetricDataResults": [], "NextToken": "more"}
    incomplete_client = _StubCloudWatchClient(no_data_response)
    provider3 = AwsCloudWatchMetricsProvider({_REF: _SERVICE_CONFIG}, client=incomplete_client)
    incomplete_result = await provider3.get_metrics(
        provider_ref=_REF,
        metric_queries={"request_count": "request_count"},
        time_range=_WINDOW,
        max_points=500,
    )
    assert incomplete_result.status == CollectionStatus.PARTIAL
    assert incomplete_result.data == []
    assert incomplete_result.truncated is True
    assert incomplete_result.truncation_reason == "pagination_not_exhausted"

    # error mapping: rate limiting before any usable data
    class _ThrottlingClient:
        def get_metric_data(self, **kwargs: Any) -> dict[str, Any]:
            raise ClientError({"Error": {"Code": "ThrottlingException"}}, "GetMetricData")

    provider2 = AwsCloudWatchMetricsProvider({_REF: _SERVICE_CONFIG}, client=_ThrottlingClient())
    with pytest.raises(ProviderQueryError) as exc_info:
        await provider2.get_metrics(
            provider_ref=_REF,
            metric_queries={"request_count": "request_count"},
            time_range=_WINDOW,
            max_points=500,
        )
    assert exc_info.value.reason == ProviderFailureReason.RATE_LIMITED
