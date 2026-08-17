"""Deterministic fake DeploymentsProvider backed by canned per-scenario datasets."""

from __future__ import annotations

from cloudops_mcp.domain.models import (
    CollectionResult,
    CollectionStatus,
    DeploymentEvent,
    ProviderFailureReason,
    TimeRange,
)
from cloudops_mcp.providers.base import SimulatedProviderFailure


class FakeDeploymentsProvider:
    def __init__(
        self,
        events_by_ref: dict[str, list[DeploymentEvent]],
        failing_refs: dict[str, ProviderFailureReason] | None = None,
    ) -> None:
        self._events_by_ref = events_by_ref
        self._failing_refs = failing_refs or {}

    async def get_deployments(
        self,
        *,
        provider_ref: str,
        time_range: TimeRange,
        max_events: int,
    ) -> CollectionResult[DeploymentEvent]:
        if provider_ref in self._failing_refs:
            raise SimulatedProviderFailure(self._failing_refs[provider_ref])

        all_events = self._events_by_ref.get(provider_ref, [])
        matching = [e for e in all_events if time_range.start <= e.timestamp < time_range.end]
        matching.sort(key=lambda e: e.timestamp, reverse=True)

        truncated = len(matching) > max_events
        data = matching[:max_events]
        status = (
            CollectionStatus.EMPTY
            if not data
            else (CollectionStatus.PARTIAL if truncated else CollectionStatus.SUCCESS)
        )
        return CollectionResult(
            status=status,
            data=data,
            truncated=truncated,
            truncation_reason="max_events_reached" if truncated else None,
        )
