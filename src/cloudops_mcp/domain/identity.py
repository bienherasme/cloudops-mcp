"""Canonical service identity and the config-backed service registry.

Identity is deliberately minimal: service name plus environment. Vendor-specific
references (AWS resource names, Kubernetes object names, Datadog tags) live
inside registry bindings and never leak into the public get_services contract.

ServiceRegistry is static configuration, not a CMDB and not auto-discovered.
Entries are declared up front and identity is never inferred or fuzzy-matched
at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict

from cloudops_mcp.bounds import SNAPSHOT_METRICS_HARD_CAP
from cloudops_mcp.domain.models import CapabilityAvailability, CapabilityName


class ServiceRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    service: str
    environment: str

    def key(self) -> tuple[str, str]:
        return (self.service, self.environment)


@dataclass(frozen=True)
class CapabilityBinding:
    """Internal wiring for one capability of one service. Never exposed as-is."""

    provider_name: str
    provider_ref: str
    metric_map: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ServiceCatalogEntry:
    service: str
    environment: str
    capability_bindings: dict[CapabilityName, CapabilityBinding] = field(default_factory=dict)
    snapshot_metrics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.snapshot_metrics) > SNAPSHOT_METRICS_HARD_CAP:
            raise ValueError(
                f"{self.service}/{self.environment}: snapshot_metrics exceeds hard cap "
                f"of {SNAPSHOT_METRICS_HARD_CAP}"
            )
        if not set(self.snapshot_metrics).issubset(self.available_metrics):
            raise ValueError(
                f"{self.service}/{self.environment}: snapshot_metrics must be a subset "
                "of available_metrics (derived from the metrics capability's metric_map)"
            )

    @property
    def available_metrics(self) -> tuple[str, ...]:
        """Canonical metric names, derived from the metrics binding's metric_map.

        This is the single source of truth: there is no separately configured
        list to drift out of sync. A service with no metrics binding has none.
        """
        metrics_binding = self.capability_bindings.get(CapabilityName.METRICS)
        if metrics_binding is None:
            return ()
        return tuple(metrics_binding.metric_map.keys())

    def availability(self, capability: CapabilityName) -> CapabilityAvailability:
        if capability in self.capability_bindings:
            return CapabilityAvailability.CONFIGURED
        return CapabilityAvailability.NOT_CONFIGURED

    def binding(self, capability: CapabilityName) -> CapabilityBinding | None:
        return self.capability_bindings.get(capability)


class ServiceRegistry:
    def __init__(self, entries: list[ServiceCatalogEntry]) -> None:
        self._entries: dict[tuple[str, str], ServiceCatalogEntry] = {}
        for entry in entries:
            key = (entry.service, entry.environment)
            if key in self._entries:
                raise ValueError(f"duplicate service registry entry for {key}")
            self._entries[key] = entry

    def get(self, service: str, environment: str) -> ServiceCatalogEntry | None:
        return self._entries.get((service, environment))

    def list_entries(self) -> list[ServiceCatalogEntry]:
        return list(self._entries.values())
