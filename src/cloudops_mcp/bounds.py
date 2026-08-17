"""Central query bounds.

Every service module resolves its limits through this module rather than
hardcoding numbers locally, so the whole bounded-query story lives in one
auditable place.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import TypeVar

from cloudops_mcp.domain.models import TimeRange
from cloudops_mcp.errors import InvalidArgumentError

_ItemT = TypeVar("_ItemT")

SERVICES_DEFAULT_MAX = 50
SERVICES_HARD_CAP = 200

LOGS_DEFAULT_MAX_EVENTS = 100
LOGS_HARD_CAP_MAX_EVENTS = 500
LOGS_MESSAGE_MAX_CHARS = 2000
LOGS_DEFAULT_TIME_SPAN = timedelta(hours=1)
LOGS_HARD_CAP_TIME_SPAN = timedelta(hours=24)

METRICS_DEFAULT_POINTS = 500
METRICS_HARD_CAP_POINTS = 500
METRICS_DEFAULT_TIME_SPAN = timedelta(hours=1)
METRICS_HARD_CAP_TIME_SPAN = timedelta(days=7)
SNAPSHOT_METRICS_HARD_CAP = 5

DEPLOYMENTS_DEFAULT_MAX_EVENTS = 20
DEPLOYMENTS_HARD_CAP_MAX_EVENTS = 100
DEPLOYMENTS_DEFAULT_TIME_SPAN = timedelta(hours=24)
DEPLOYMENTS_HARD_CAP_TIME_SPAN = timedelta(hours=24)


def resolve_time_range(
    requested: TimeRange | None,
    *,
    default_span: timedelta,
    hard_cap_span: timedelta,
    now: datetime,
) -> tuple[TimeRange, TimeRange | None]:
    """Resolve a requested time range against a default window and a hard cap.

    Returns (applied, requested_echo). requested_echo is None when the caller
    didn't provide a range at all, since nothing was actually requested.

    An explicit range with start >= end is a caller mistake, not something to
    clamp silently, so it raises InvalidArgumentError.
    """
    if requested is None:
        applied = TimeRange(start=now - default_span, end=now)
        return applied, None

    if requested.start >= requested.end:
        raise InvalidArgumentError("time_range.start must be before time_range.end")

    span = requested.end - requested.start
    if span > hard_cap_span:
        applied = TimeRange(start=requested.end - hard_cap_span, end=requested.end)
        return applied, requested

    return requested, requested


def clamp_max_items(
    requested: int | None, *, default: int, hard_cap: int
) -> tuple[int, int]:
    """Resolve a requested item count against a default and a hard cap.

    Returns (applied, requested_echo). Unlike resolve_time_range, requested_echo
    is never None: a count bound always has exactly one caller-visible value to
    report, so when the caller omits it the default is echoed back as "what was
    requested" instead. This is a deliberate difference from resolve_time_range,
    not an inconsistency: an absent time range has no meaningful "requested"
    value to report (there is no default range to point to), while an absent
    count is fully described by the default that governs the query.
    """
    if requested is None:
        return default, default
    if requested <= 0:
        raise InvalidArgumentError("max_items must be a positive integer")
    return min(requested, hard_cap), requested


def cap_ordered_items(
    items: Sequence[_ItemT],
    *,
    key: Callable[[_ItemT], datetime],
    applied_max: int,
    provider_truncated: bool,
    provider_truncation_reason: str | None,
) -> tuple[list[_ItemT], bool, str | None]:
    """Defensively sort newest-first and cap to applied_max.

    The service layer is the last barrier before MCP output, so a provider's
    claimed ordering and count are never trusted alone: this always re-sorts
    and re-caps regardless of what the provider already did.

    Returns (capped_items, truncated, truncation_reason). If this cap is what
    cuts the data, the reason is "max_events_reached" regardless of what the
    provider reported. If this cap never triggers but the provider already
    reported truncation upstream, that report is preserved rather than lost.
    """
    ordered = sorted(items, key=key, reverse=True)
    defensively_truncated = len(ordered) > applied_max
    capped = ordered[:applied_max]
    truncated = defensively_truncated or provider_truncated
    reason: str | None
    if defensively_truncated:
        reason = "max_events_reached"
    elif provider_truncated:
        reason = provider_truncation_reason
    else:
        reason = None
    return capped, truncated, reason
