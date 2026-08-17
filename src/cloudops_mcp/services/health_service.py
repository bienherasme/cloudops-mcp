"""Deterministic service-health lookup.

Health is never synthesized from logs, metrics, or deployments here. It is
either explicitly reported by a configured provider, or unavailable.
"""

from __future__ import annotations

from cloudops_mcp.domain.identity import CapabilityBinding, ServiceRegistry
from cloudops_mcp.domain.models import (
    CapabilityAvailability,
    CapabilityName,
    CollectionResult,
    CollectionStatus,
    HealthResult,
    HealthSignal,
    ProviderFailureReason,
)
from cloudops_mcp.errors import InvalidArgumentError
from cloudops_mcp.providers.base import HealthProvider, ProviderQueryError


def _failed(binding: CapabilityBinding, reason: ProviderFailureReason) -> HealthResult:
    return HealthResult(
        availability=CapabilityAvailability.CONFIGURED,
        collection=CollectionResult(
            status=CollectionStatus.FAILED,
            failure_reason=reason,
            provider=binding.provider_name,
        ),
    )


async def get_service_health(
    registry: ServiceRegistry,
    provider: HealthProvider,
    *,
    service: str,
    environment: str,
) -> HealthResult:
    entry = registry.get(service, environment)
    if entry is None:
        raise InvalidArgumentError(f"unknown service {service!r} in environment {environment!r}")

    binding = entry.binding(CapabilityName.HEALTH)
    if binding is None:
        return HealthResult(availability=CapabilityAvailability.NOT_CONFIGURED, collection=None)

    result: CollectionResult[HealthSignal]
    try:
        result = await provider.get_health(provider_ref=binding.provider_ref)
    except ProviderQueryError as exc:
        return _failed(binding, exc.reason)

    signal = result.data[0] if result.data else None
    return HealthResult(
        availability=CapabilityAvailability.CONFIGURED,
        collection=CollectionResult(
            status=result.status,
            data=[signal] if signal is not None else [],
            provider=binding.provider_name,
            source=result.source,
        ),
    )
