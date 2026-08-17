"""Bounds resolution: defaults, clamping to hard caps, and invalid-range rejection.

Clamping alone never becomes PARTIAL, see resolve_time_range's docstring and
the logs/metrics service tests for where PARTIAL actually comes from.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cloudops_mcp.bounds import clamp_max_items, resolve_time_range
from cloudops_mcp.domain.models import TimeRange
from cloudops_mcp.errors import InvalidArgumentError

_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def test_time_range_and_count_resolution() -> None:
    # no range requested: falls back to the default window, nothing to echo as "requested"
    applied, requested_echo = resolve_time_range(
        None, default_span=timedelta(hours=1), hard_cap_span=timedelta(hours=24), now=_NOW
    )
    assert applied == TimeRange(start=_NOW - timedelta(hours=1), end=_NOW)
    assert requested_echo is None

    # within the hard cap: passed through unchanged
    within_cap = TimeRange(start=_NOW - timedelta(hours=2), end=_NOW)
    applied, requested_echo = resolve_time_range(
        within_cap, default_span=timedelta(hours=1), hard_cap_span=timedelta(hours=24), now=_NOW
    )
    assert applied == within_cap
    assert requested_echo == within_cap

    # exceeds the hard cap: clamped, but the original ask is still reported
    too_wide = TimeRange(start=_NOW - timedelta(days=30), end=_NOW)
    applied, requested_echo = resolve_time_range(
        too_wide, default_span=timedelta(hours=1), hard_cap_span=timedelta(hours=24), now=_NOW
    )
    assert applied == TimeRange(start=_NOW - timedelta(hours=24), end=_NOW)
    assert requested_echo == too_wide

    # start >= end is a caller mistake, never silently clamped
    backwards = TimeRange(start=_NOW, end=_NOW - timedelta(hours=1))
    with pytest.raises(InvalidArgumentError):
        resolve_time_range(
            backwards, default_span=timedelta(hours=1), hard_cap_span=timedelta(hours=24), now=_NOW
        )

    assert clamp_max_items(None, default=50, hard_cap=200) == (50, 50)
    assert clamp_max_items(80, default=50, hard_cap=200) == (80, 80)
    assert clamp_max_items(500, default=50, hard_cap=200) == (200, 500)
    with pytest.raises(InvalidArgumentError):
        clamp_max_items(0, default=50, hard_cap=200)
