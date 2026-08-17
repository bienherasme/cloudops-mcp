"""AWS mode configuration: TOML schema, validation, and registry construction.

Pure config/domain logic, no boto3 import here on purpose: this module (and
the ServiceRegistry it builds) can be exercised without the aws extra
installed and without any network access. Only providers/aws/logs.py and
providers/aws/metrics.py touch boto3.

CapabilityBinding stays provider-neutral: AWS-specific detail (log group
names, CloudWatch namespaces/dimensions) never enters the domain layer. This
module keeps that detail in config_by_ref maps keyed by an opaque
provider_ref, handed to the AWS provider constructors separately.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cloudops_mcp.bounds import SNAPSHOT_METRICS_HARD_CAP
from cloudops_mcp.domain.identity import CapabilityBinding, ServiceCatalogEntry, ServiceRegistry
from cloudops_mcp.domain.models import CapabilityName


class CloudWatchLogsServiceConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: Literal["aws_cloudwatch"]
    log_group: str


class CloudWatchMetricQueryConfig(BaseModel):
    """One MetricStat query: a canonical metric mapped to a real CloudWatch metric.

    First-slice scope: MetricStat only, no metric math expressions and no
    Metrics Insights. `unit` is our normalized output label (e.g.
    "milliseconds"), never sent to CloudWatch's MetricStat.Unit filter.
    """

    model_config = ConfigDict(frozen=True)

    namespace: str
    metric_name: str
    statistic: str
    period_seconds: int = Field(gt=0)
    unit: str
    dimensions: dict[str, str] = Field(default_factory=dict)


class CloudWatchMetricsServiceConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: Literal["aws_cloudwatch"]
    snapshot_metrics: tuple[str, ...] = ()
    queries: dict[str, CloudWatchMetricQueryConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_snapshot_metrics(self) -> CloudWatchMetricsServiceConfig:
        if len(self.snapshot_metrics) > SNAPSHOT_METRICS_HARD_CAP:
            raise ValueError(f"snapshot_metrics exceeds hard cap of {SNAPSHOT_METRICS_HARD_CAP}")
        unknown = set(self.snapshot_metrics) - set(self.queries)
        if unknown:
            raise ValueError(f"snapshot_metrics references undefined queries: {sorted(unknown)}")
        return self


class AwsServiceConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    environment: str
    logs: CloudWatchLogsServiceConfig | None = None
    metrics: CloudWatchMetricsServiceConfig | None = None


class AwsConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    services: tuple[AwsServiceConfig, ...] = ()


def load_aws_config(path: Path) -> AwsConfig:
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return AwsConfig.model_validate(raw)


def _provider_ref(service: AwsServiceConfig) -> str:
    return f"{service.name}:{service.environment}"


def build_registry(
    config: AwsConfig,
) -> tuple[ServiceRegistry, dict[str, str], dict[str, CloudWatchMetricsServiceConfig]]:
    """Turn validated AWS config into a ServiceRegistry plus two opaque lookup
    tables the AWS providers use internally: provider_ref -> log group name,
    and provider_ref -> metrics config. Neither table is ever exposed publicly.
    """
    log_group_by_ref: dict[str, str] = {}
    metrics_config_by_ref: dict[str, CloudWatchMetricsServiceConfig] = {}
    entries: list[ServiceCatalogEntry] = []

    for service in config.services:
        ref = _provider_ref(service)
        bindings: dict[CapabilityName, CapabilityBinding] = {}

        if service.logs is not None:
            log_group_by_ref[ref] = service.logs.log_group
            bindings[CapabilityName.LOGS] = CapabilityBinding("aws_cloudwatch_logs", ref)

        if service.metrics is not None:
            metrics_config_by_ref[ref] = service.metrics
            metric_map = {name: name for name in service.metrics.queries}
            bindings[CapabilityName.METRICS] = CapabilityBinding(
                "aws_cloudwatch_metrics", ref, metric_map=metric_map
            )

        entries.append(
            ServiceCatalogEntry(
                service=service.name,
                environment=service.environment,
                capability_bindings=bindings,
                snapshot_metrics=service.metrics.snapshot_metrics if service.metrics else (),
            )
        )

    return ServiceRegistry(entries), log_group_by_ref, metrics_config_by_ref
