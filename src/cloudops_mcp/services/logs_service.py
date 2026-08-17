"""Deterministic logs lookup: bounds resolution, message normalization, capability wrapping.

Log messages are treated as untrusted, opaque data: normalized shape and a
length cap only, never parsed, executed, or interpreted.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cloudops_mcp.bounds import (
    LOGS_DEFAULT_MAX_EVENTS,
    LOGS_DEFAULT_TIME_SPAN,
    LOGS_HARD_CAP_MAX_EVENTS,
    LOGS_HARD_CAP_TIME_SPAN,
    LOGS_MESSAGE_MAX_CHARS,
    cap_ordered_items,
    clamp_max_items,
    resolve_time_range,
)
from cloudops_mcp.domain.identity import CapabilityBinding, ServiceRegistry
from cloudops_mcp.domain.models import (
    CapabilityAvailability,
    CapabilityName,
    CollectionResult,
    CollectionStatus,
    LogEvent,
    LogsResult,
    ProviderFailureReason,
    QueryBounds,
    TimeRange,
)
from cloudops_mcp.errors import InvalidArgumentError
from cloudops_mcp.providers.base import LogsProvider, ProviderQueryError

_TRUNCATION_SUFFIX = "...(truncated)"


def _cap_message(event: LogEvent) -> LogEvent:
    if len(event.message) <= LOGS_MESSAGE_MAX_CHARS:
        return event
    capped = event.message[: LOGS_MESSAGE_MAX_CHARS - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX
    return LogEvent(
        timestamp=event.timestamp,
        level=event.level,
        service=event.service,
        environment=event.environment,
        message=capped,
        message_truncated=True,
        source=event.source,
        provider=event.provider,
        attributes=event.attributes,
        reference=event.reference,
    )


def _failed(
    binding: CapabilityBinding,
    applied_bounds: QueryBounds,
    requested_bounds: QueryBounds,
    reason: ProviderFailureReason,
) -> LogsResult:
    return LogsResult(
        availability=CapabilityAvailability.CONFIGURED,
        collection=CollectionResult(
            status=CollectionStatus.FAILED,
            failure_reason=reason,
            provider=binding.provider_name,
            applied_bounds=applied_bounds,
            requested_bounds=requested_bounds,
        ),
    )


async def get_logs(
    registry: ServiceRegistry,
    provider: LogsProvider,
    *,
    service: str,
    environment: str,
    time_range: TimeRange | None,
    max_events: int | None,
    now: datetime | None = None,
) -> LogsResult:
    entry = registry.get(service, environment)
    if entry is None:
        raise InvalidArgumentError(f"unknown service {service!r} in environment {environment!r}")

    binding = entry.binding(CapabilityName.LOGS)
    if binding is None:
        return LogsResult(availability=CapabilityAvailability.NOT_CONFIGURED, collection=None)

    now = now or datetime.now(UTC)
    applied_tr, requested_tr = resolve_time_range(
        time_range,
        default_span=LOGS_DEFAULT_TIME_SPAN,
        hard_cap_span=LOGS_HARD_CAP_TIME_SPAN,
        now=now,
    )
    applied_max, requested_max = clamp_max_items(
        max_events, default=LOGS_DEFAULT_MAX_EVENTS, hard_cap=LOGS_HARD_CAP_MAX_EVENTS
    )
    applied_bounds = QueryBounds(time_range=applied_tr, max_items=applied_max)
    requested_bounds = QueryBounds(time_range=requested_tr, max_items=requested_max)

    result: CollectionResult[LogEvent]
    try:
        result = await provider.get_logs(
            provider_ref=binding.provider_ref,
            service=service,
            environment=environment,
            time_range=applied_tr,
            max_events=applied_max,
        )
    except ProviderQueryError as exc:
        return _failed(binding, applied_bounds, requested_bounds, exc.reason)

    capped_events, truncated, truncation_reason = cap_ordered_items(
        result.data,
        key=lambda e: e.timestamp,
        applied_max=applied_max,
        provider_truncated=result.truncated,
        provider_truncation_reason=result.truncation_reason,
    )
    final_events = [_cap_message(e) for e in capped_events]

    # truncated wins even with no data yet: e.g. every page scanned so far
    # was empty but a nextToken remains, that's incomplete, not EMPTY.
    status = (
        CollectionStatus.PARTIAL
        if truncated
        else (CollectionStatus.EMPTY if not final_events else CollectionStatus.SUCCESS)
    )

    return LogsResult(
        availability=CapabilityAvailability.CONFIGURED,
        collection=CollectionResult(
            status=status,
            data=final_events,
            provider=binding.provider_name,
            source=result.source,
            truncated=truncated,
            truncation_reason=truncation_reason,
            applied_bounds=applied_bounds,
            requested_bounds=requested_bounds,
        ),
    )
