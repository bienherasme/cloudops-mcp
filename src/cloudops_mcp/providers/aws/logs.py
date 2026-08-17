"""AWS CloudWatch Logs provider: FilterLogEvents only.

No Logs Insights, no StartQuery/GetQueryResults, no unmask. Read-only via a
single IAM action: logs:FilterLogEvents.

FilterLogEvents pagination is defensive on purpose: a page can be empty or
partial while nextToken is still present, so "empty page" never means
"finished" here, only an absent nextToken does.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Protocol

import boto3  # type: ignore[import-untyped]

from cloudops_mcp.domain.models import (
    CollectionResult,
    CollectionStatus,
    LogEvent,
    TimeRange,
)
from cloudops_mcp.providers.aws.client import build_botocore_config
from cloudops_mcp.providers.aws.errors import KNOWN_AWS_EXCEPTIONS, classify_aws_exception
from cloudops_mcp.providers.base import ProviderQueryError

# Defensive guard against a pathological run of empty-but-not-finished pages.
# 10 pages is generous headroom over what a well-behaved log group needs to
# fill max_events, without letting one query fetch indefinitely.
_MAX_PAGES = 10

# AWS only allows startFromHead=False (newest-first) when startTime is on or
# after this date. Older ranges must page oldest-first instead.
_NEWEST_FIRST_MIN_START = datetime(2024, 1, 1, tzinfo=UTC)


class _LogsClient(Protocol):
    """The one boto3 CloudWatch Logs client method this provider calls."""

    def filter_log_events(self, **kwargs: Any) -> dict[str, Any]: ...


def _to_epoch_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _normalize_event(raw: dict[str, Any], *, service: str, environment: str) -> LogEvent:
    timestamp = datetime.fromtimestamp(raw["timestamp"] / 1000, tz=UTC)
    return LogEvent(
        timestamp=timestamp,
        level=None,
        service=service,
        environment=environment,
        message=raw.get("message", ""),
        source=raw.get("logStreamName", "cloudwatch_logs"),
        provider="aws_cloudwatch_logs",
        reference=raw.get("eventId"),
    )


class AwsCloudWatchLogsProvider:
    def __init__(
        self,
        log_group_by_ref: dict[str, str],
        client: _LogsClient | None = None,
    ) -> None:
        self._log_group_by_ref = log_group_by_ref
        self._client: _LogsClient = client or boto3.session.Session().client(
            "logs", config=build_botocore_config()
        )

    async def get_logs(
        self,
        *,
        provider_ref: str,
        service: str,
        environment: str,
        time_range: TimeRange,
        max_events: int,
    ) -> CollectionResult[LogEvent]:
        # a missing entry here means our own composition wiring is wrong, not
        # an AWS failure, so this is a plain KeyError, not a ProviderQueryError
        log_group = self._log_group_by_ref[provider_ref]

        start_ms = _to_epoch_ms(time_range.start)
        end_ms = _to_epoch_ms(time_range.end)
        # AWS rejects startFromHead=False for ranges that start before this
        # cutoff, so historical windows must page oldest-first instead.
        newest_first = time_range.start >= _NEWEST_FIRST_MIN_START

        events: list[LogEvent] = []
        next_token: str | None = None
        stopped_at_max_events = False
        stopped_at_page_guard = False

        try:
            for page in range(_MAX_PAGES):
                kwargs: dict[str, Any] = {
                    "logGroupName": log_group,
                    "startTime": start_ms,
                    "endTime": end_ms,
                    "limit": max_events,
                    "startFromHead": not newest_first,
                }
                if next_token:
                    kwargs["nextToken"] = next_token

                response = await asyncio.to_thread(self._client.filter_log_events, **kwargs)

                for raw_event in response.get("events", []):
                    normalized = _normalize_event(
                        raw_event, service=service, environment=environment
                    )
                    # defend our end-exclusive contract regardless of AWS's own
                    # boundary handling
                    if time_range.start <= normalized.timestamp < time_range.end:
                        events.append(normalized)

                if not newest_first and len(events) > max_events:
                    # oldest-first: events arrive in ascending order, so the
                    # tail is always the most recent seen so far. Trim after
                    # every page rather than only at the end, so memory stays
                    # bounded even across a long historical scan.
                    events = events[-max_events:]

                next_token = response.get("nextToken")

                if next_token is None:
                    break
                if newest_first and len(events) >= max_events:
                    # Oldest-first mode never stops here: the events collected
                    # so far would be the oldest in the window, not the most
                    # recent, so it must keep going until exhausted or guarded.
                    stopped_at_max_events = True
                    break
                if page == _MAX_PAGES - 1:
                    stopped_at_page_guard = True
        except KNOWN_AWS_EXCEPTIONS as exc:
            if events:
                # partial data already collected before the failure: salvage it
                # rather than discarding a usable result
                return CollectionResult(
                    status=CollectionStatus.PARTIAL,
                    data=events,
                    truncated=True,
                    truncation_reason="provider_error_after_partial_data",
                )
            raise ProviderQueryError(classify_aws_exception(exc)) from exc

        truncated = stopped_at_max_events or stopped_at_page_guard
        truncation_reason = (
            "max_events_reached"
            if stopped_at_max_events
            else ("pagination_guard_reached" if stopped_at_page_guard else None)
        )

        if truncated:
            # Extraction is known incomplete, with or without data collected
            # so far, that's PARTIAL either way, never EMPTY.
            return CollectionResult(
                status=CollectionStatus.PARTIAL,
                data=events,
                truncated=True,
                truncation_reason=truncation_reason,
            )
        if not events:
            return CollectionResult(status=CollectionStatus.EMPTY)
        return CollectionResult(status=CollectionStatus.SUCCESS, data=events)
