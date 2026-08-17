"""Deterministic, bounded service catalog listing."""

from __future__ import annotations

from cloudops_mcp.bounds import SERVICES_DEFAULT_MAX, SERVICES_HARD_CAP, clamp_max_items
from cloudops_mcp.domain.identity import ServiceRegistry
from cloudops_mcp.domain.models import CapabilityName, ServiceCatalogResult, ServiceSummary


def list_services(
    registry: ServiceRegistry,
    *,
    environment: str | None,
    max_services: int | None,
) -> ServiceCatalogResult:
    applied_max, requested_max = clamp_max_items(
        max_services, default=SERVICES_DEFAULT_MAX, hard_cap=SERVICES_HARD_CAP
    )

    entries = registry.list_entries()
    if environment is not None:
        entries = [e for e in entries if e.environment == environment]
    entries.sort(key=lambda e: (e.service, e.environment))

    truncated = len(entries) > applied_max
    selected = entries[:applied_max]

    summaries = [
        ServiceSummary(
            service=e.service,
            environment=e.environment,
            capabilities={cap: e.availability(cap) for cap in CapabilityName},
            available_metrics=list(e.available_metrics),
        )
        for e in selected
    ]

    return ServiceCatalogResult(
        services=summaries,
        truncated=truncated,
        returned_count=len(summaries),
        requested_max=requested_max,
        applied_max=applied_max,
    )
