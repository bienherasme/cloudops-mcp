"""Three deterministic fake scenarios, selectable by name.

Each scenario builds its own ServiceRegistry and fake providers from scratch.
Nothing here is random: every timestamp and value is fixed so runs and tests
are reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from cloudops_mcp.domain.identity import ServiceRegistry
from cloudops_mcp.providers.base import (
    DeploymentsProvider,
    HealthProvider,
    LogsProvider,
    MetricsProvider,
)


@dataclass(frozen=True)
class ScenarioContext:
    name: str
    registry: ServiceRegistry
    logs_provider: LogsProvider
    metrics_provider: MetricsProvider
    deployments_provider: DeploymentsProvider
    health_provider: HealthProvider
    now: datetime


def load_scenario(name: str) -> ScenarioContext:
    from cloudops_mcp.scenarios import bad_deploy, healthy, partial

    builders = {
        "healthy": healthy.build,
        "bad_deploy": bad_deploy.build,
        "partial": partial.build,
    }
    builder = builders.get(name)
    if builder is None:
        raise ValueError(f"unknown scenario {name!r}, expected one of {sorted(builders)}")
    return builder()
