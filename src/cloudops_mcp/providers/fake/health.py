"""Deterministic fake HealthProvider backed by canned per-scenario signals."""

from __future__ import annotations

from cloudops_mcp.domain.models import (
    CollectionResult,
    CollectionStatus,
    HealthSignal,
    ProviderFailureReason,
)
from cloudops_mcp.providers.base import SimulatedProviderFailure


class FakeHealthProvider:
    def __init__(
        self,
        signal_by_ref: dict[str, HealthSignal],
        failing_refs: dict[str, ProviderFailureReason] | None = None,
    ) -> None:
        self._signal_by_ref = signal_by_ref
        self._failing_refs = failing_refs or {}

    async def get_health(self, *, provider_ref: str) -> CollectionResult[HealthSignal]:
        if provider_ref in self._failing_refs:
            raise SimulatedProviderFailure(self._failing_refs[provider_ref])

        signal = self._signal_by_ref.get(provider_ref)
        if signal is None:
            return CollectionResult(status=CollectionStatus.EMPTY, data=[])
        return CollectionResult(status=CollectionStatus.SUCCESS, data=[signal])
