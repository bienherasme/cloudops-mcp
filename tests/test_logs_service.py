"""Logs service: defensive ordering/truncation and message-length capping.

Uses a deliberately naive stub provider (wrong order, ignores max_events)
instead of FakeLogsProvider, which already behaves well. The point is to
prove logs_service defends the MCP output itself, not that a well-behaved
provider happens to get it right.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cloudops_mcp.bounds import LOGS_MESSAGE_MAX_CHARS
from cloudops_mcp.domain.identity import CapabilityBinding, ServiceCatalogEntry, ServiceRegistry
from cloudops_mcp.domain.models import (
    CapabilityName,
    CollectionResult,
    CollectionStatus,
    LogEvent,
    TimeRange,
)
from cloudops_mcp.services import logs_service

_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


class _NaiveLogsProvider:
    """Returns exactly what it's given, in whatever order, ignoring max_events."""

    def __init__(self, events: list[LogEvent]) -> None:
        self._events = events

    async def get_logs(
        self,
        *,
        provider_ref: str,
        service: str,
        environment: str,
        time_range: TimeRange,
        max_events: int,
    ) -> CollectionResult[LogEvent]:
        return CollectionResult(status=CollectionStatus.SUCCESS, data=self._events)


def _registry() -> ServiceRegistry:
    return ServiceRegistry(
        [
            ServiceCatalogEntry(
                service="checkout-api",
                environment="production",
                capability_bindings={
                    CapabilityName.LOGS: CapabilityBinding("fake_logs", "checkout-api-logs")
                },
            )
        ]
    )


async def test_logs_defensive_ordering_truncation_and_message_cap() -> None:
    long_message = "x" * (LOGS_MESSAGE_MAX_CHARS + 500)
    # deliberately out of chronological order, and more events than max_events allows
    events = [
        LogEvent(
            timestamp=_NOW.replace(hour=11, minute=minute),
            level="INFO",
            service="checkout-api",
            environment="production",
            message=long_message if minute == 30 else "short",
            source="app",
            provider="fake_logs",
        )
        for minute in (20, 10, 30)
    ]
    provider = _NaiveLogsProvider(events)

    result = await logs_service.get_logs(
        _registry(),
        provider,
        service="checkout-api",
        environment="production",
        time_range=None,
        max_events=2,
        now=_NOW,
    )

    assert result.collection is not None
    assert result.collection.status == CollectionStatus.PARTIAL
    assert result.collection.truncated is True
    assert result.collection.truncation_reason == "max_events_reached"
    # newest-first despite the provider handing them back out of order: keeps
    # 11:30 (the long one) and 11:20, drops 11:10
    assert [e.timestamp.minute for e in result.collection.data] == [30, 20]

    long_event, short_event = result.collection.data
    assert len(long_event.message) <= LOGS_MESSAGE_MAX_CHARS
    assert long_event.message_truncated is True
    assert short_event.message_truncated is False
