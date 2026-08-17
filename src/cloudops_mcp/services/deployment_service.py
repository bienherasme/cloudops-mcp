"""Deterministic recent-deployments lookup."""

from __future__ import annotations

from datetime import UTC, datetime

from cloudops_mcp.bounds import (
    DEPLOYMENTS_DEFAULT_MAX_EVENTS,
    DEPLOYMENTS_DEFAULT_TIME_SPAN,
    DEPLOYMENTS_HARD_CAP_MAX_EVENTS,
    DEPLOYMENTS_HARD_CAP_TIME_SPAN,
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
    DeploymentEvent,
    DeploymentsResult,
    ProviderFailureReason,
    QueryBounds,
    TimeRange,
)
from cloudops_mcp.errors import InvalidArgumentError
from cloudops_mcp.providers.base import DeploymentsProvider, ProviderQueryError


def _failed(
    binding: CapabilityBinding,
    applied_bounds: QueryBounds,
    requested_bounds: QueryBounds,
    reason: ProviderFailureReason,
) -> DeploymentsResult:
    return DeploymentsResult(
        availability=CapabilityAvailability.CONFIGURED,
        collection=CollectionResult(
            status=CollectionStatus.FAILED,
            failure_reason=reason,
            provider=binding.provider_name,
            applied_bounds=applied_bounds,
            requested_bounds=requested_bounds,
        ),
    )


async def get_recent_deployments(
    registry: ServiceRegistry,
    provider: DeploymentsProvider,
    *,
    service: str,
    environment: str,
    time_range: TimeRange | None,
    max_events: int | None,
    now: datetime | None = None,
) -> DeploymentsResult:
    entry = registry.get(service, environment)
    if entry is None:
        raise InvalidArgumentError(f"unknown service {service!r} in environment {environment!r}")

    binding = entry.binding(CapabilityName.DEPLOYMENTS)
    if binding is None:
        return DeploymentsResult(
            availability=CapabilityAvailability.NOT_CONFIGURED, collection=None
        )

    now = now or datetime.now(UTC)
    applied_tr, requested_tr = resolve_time_range(
        time_range,
        default_span=DEPLOYMENTS_DEFAULT_TIME_SPAN,
        hard_cap_span=DEPLOYMENTS_HARD_CAP_TIME_SPAN,
        now=now,
    )
    applied_max, requested_max = clamp_max_items(
        max_events, default=DEPLOYMENTS_DEFAULT_MAX_EVENTS, hard_cap=DEPLOYMENTS_HARD_CAP_MAX_EVENTS
    )
    applied_bounds = QueryBounds(time_range=applied_tr, max_items=applied_max)
    requested_bounds = QueryBounds(time_range=requested_tr, max_items=requested_max)

    result: CollectionResult[DeploymentEvent]
    try:
        result = await provider.get_deployments(
            provider_ref=binding.provider_ref, time_range=applied_tr, max_events=applied_max
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

    # truncated wins even with no data yet, see logs_service for why.
    status = (
        CollectionStatus.PARTIAL
        if truncated
        else (CollectionStatus.EMPTY if not capped_events else CollectionStatus.SUCCESS)
    )

    return DeploymentsResult(
        availability=CapabilityAvailability.CONFIGURED,
        collection=CollectionResult(
            status=status,
            data=capped_events,
            provider=binding.provider_name,
            source=result.source,
            truncated=truncated,
            truncation_reason=truncation_reason,
            applied_bounds=applied_bounds,
            requested_bounds=requested_bounds,
        ),
    )
