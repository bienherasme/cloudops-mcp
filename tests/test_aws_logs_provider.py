"""AWS CloudWatch Logs provider: normalization, pagination, truncation, error mapping.

Uses a small stub CloudWatch Logs client (not real boto3, not moto), and
feeds it pages in the order a real startFromHead=False response would
actually arrive (newest-first) since the provider trusts AWS's own ordering
and relies on logs_service's separate defensive re-sort for the final
correctness guarantee.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

pytest.importorskip("botocore", reason="aws extra not installed")

from botocore.exceptions import ClientError  # type: ignore[import-untyped]  # noqa: E402

from cloudops_mcp.domain.models import (
    CollectionResult,
    CollectionStatus,
    LogEvent,
    ProviderFailureReason,
    TimeRange,
)
from cloudops_mcp.providers.aws.logs import AwsCloudWatchLogsProvider
from cloudops_mcp.providers.base import ProviderQueryError

_REF = "checkout-api:production"
_WINDOW = TimeRange(
    start=datetime(2026, 1, 15, 11, 0, tzinfo=UTC), end=datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
)


def _ms(hour: int, minute: int) -> int:
    return int(datetime(2026, 1, 15, hour, minute, tzinfo=UTC).timestamp() * 1000)


def _raw_event(hour: int, minute: int, message: str, event_id: str) -> dict[str, Any]:
    return {
        "timestamp": _ms(hour, minute),
        "message": message,
        "logStreamName": "2026/01/15/[LATEST]abc123",
        "eventId": event_id,
    }


class _StubLogsClient:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self._pages = pages
        self.call_count = 0

    def filter_log_events(self, **kwargs: Any) -> dict[str, Any]:
        page = self._pages[self.call_count]
        self.call_count += 1
        return page


class _AlwaysEmptyWithTokenClient:
    """Every page is empty but claims more exists: the page-guard case."""

    def __init__(self) -> None:
        self.call_count = 0

    def filter_log_events(self, **kwargs: Any) -> dict[str, Any]:
        self.call_count += 1
        return {"events": [], "nextToken": "still-more"}


async def _fetch(
    provider: AwsCloudWatchLogsProvider, max_events: int
) -> CollectionResult[LogEvent]:
    return await provider.get_logs(
        provider_ref=_REF,
        service="checkout-api",
        environment="production",
        time_range=_WINDOW,
        max_events=max_events,
    )


async def test_aws_logs_provider_pagination_truncation_and_error_mapping() -> None:
    # two pages, newest-first, together exactly satisfying max_events=4
    pages: list[dict[str, Any]] = [
        {
            "events": [
                _raw_event(11, 50, "request completed", "e4"),
                _raw_event(11, 45, "request completed", "e3"),
            ],
            "nextToken": "page2",
        },
        {
            "events": [
                _raw_event(11, 40, "request completed", "e2"),
                _raw_event(11, 30, "request completed", "e1"),
            ],
        },
    ]
    client = _StubLogsClient(pages)
    provider = AwsCloudWatchLogsProvider({_REF: "/aws/lambda/checkout-api"}, client=client)

    result = await _fetch(provider, max_events=4)

    assert client.call_count == 2  # real pagination happened, not just the first page
    assert result.status == CollectionStatus.SUCCESS
    assert result.truncated is False
    assert [e.reference for e in result.data] == ["e4", "e3", "e2", "e1"]
    first = result.data[0]
    assert first.level is None
    assert first.provider == "aws_cloudwatch_logs"
    assert first.source == "2026/01/15/[LATEST]abc123"
    assert first.timestamp == datetime(2026, 1, 15, 11, 50, tzinfo=UTC)

    # a single page already meets max_events, but nextToken remains: PARTIAL
    one_page: list[dict[str, Any]] = [
        {
            "events": [_raw_event(11, 50, "x", "e1"), _raw_event(11, 45, "x", "e2")],
            "nextToken": "more",
        }
    ]
    provider2 = AwsCloudWatchLogsProvider(
        {_REF: "/aws/lambda/checkout-api"}, client=_StubLogsClient(one_page)
    )
    truncated_result = await _fetch(provider2, max_events=2)
    assert truncated_result.status == CollectionStatus.PARTIAL
    assert truncated_result.truncated is True
    assert truncated_result.truncation_reason == "max_events_reached"

    # every page empty but nextToken never runs out: page guard, PARTIAL with
    # no data, never EMPTY, since extraction never actually finished
    guard_client = _AlwaysEmptyWithTokenClient()
    provider4 = AwsCloudWatchLogsProvider({_REF: "/aws/lambda/checkout-api"}, client=guard_client)
    guarded_result = await _fetch(provider4, max_events=10)
    assert guard_client.call_count == 10  # the page guard, not an unbounded loop
    assert guarded_result.status == CollectionStatus.PARTIAL
    assert guarded_result.data == []
    assert guarded_result.truncated is True
    assert guarded_result.truncation_reason == "pagination_guard_reached"

    # error before any usable data becomes a sanitized ProviderQueryError
    class _FailingClient:
        def filter_log_events(self, **kwargs: Any) -> dict[str, Any]:
            raise ClientError({"Error": {"Code": "AccessDeniedException"}}, "FilterLogEvents")

    provider3 = AwsCloudWatchLogsProvider(
        {_REF: "/aws/lambda/checkout-api"}, client=_FailingClient()
    )
    with pytest.raises(ProviderQueryError) as exc_info:
        await _fetch(provider3, max_events=10)
    assert exc_info.value.reason == ProviderFailureReason.AUTH_ERROR
