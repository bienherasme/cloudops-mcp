"""Normalized, provider-agnostic operational domain models.

These are the shapes every provider adapter normalizes into, and the shapes
every MCP tool returns as structured output. Nothing here is vendor-specific.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

T = TypeVar("T", bound=BaseModel)


def _require_utc(value: datetime) -> datetime:
    """Reject naive datetimes and normalize aware ones to UTC.

    A naive datetime has no way to say what timezone it means, so guessing
    one would silently corrupt the timestamp. Callers must attach a timezone
    themselves; this only normalizes, it never assumes.
    """
    if value.tzinfo is None:
        raise ValueError("naive datetimes are not allowed, timestamps must be timezone-aware")
    return value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_require_utc)]


class CollectionStatus(StrEnum):
    """Outcome of a provider query that was actually attempted."""

    SUCCESS = "success"
    EMPTY = "empty"
    PARTIAL = "partial"
    FAILED = "failed"


class CapabilityAvailability(StrEnum):
    """Whether a capability has a provider wired up for a given service, prior to querying it."""

    CONFIGURED = "configured"
    NOT_CONFIGURED = "not_configured"


class ProviderFailureReason(StrEnum):
    AUTH_ERROR = "auth_error"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class CapabilityName(StrEnum):
    LOGS = "logs"
    METRICS = "metrics"
    DEPLOYMENTS = "deployments"
    HEALTH = "health"


class DeploymentStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    IN_PROGRESS = "in_progress"
    UNKNOWN = "unknown"


class HealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class TimeRange(BaseModel):
    """UTC time range. Start is inclusive, end is exclusive."""

    model_config = ConfigDict(frozen=True)

    start: UtcDatetime
    end: UtcDatetime


class QueryBounds(BaseModel):
    """Bounds either requested by the caller or actually applied to a query."""

    model_config = ConfigDict(frozen=True)

    time_range: TimeRange | None = None
    max_items: int | None = None


class LogEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp: UtcDatetime
    # Not every log source has a portable severity concept (CloudWatch Logs
    # FilterLogEvents doesn't); this is populated only when a provider
    # genuinely has one, never guessed or parsed from the message.
    level: str | None = None
    service: str
    environment: str
    message: str
    message_truncated: bool = False
    source: str
    provider: str
    attributes: dict[str, str] = Field(default_factory=dict)
    reference: str | None = None


class MetricPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp: UtcDatetime
    value: float


class MetricAggregates(BaseModel):
    model_config = ConfigDict(frozen=True)

    minimum: float
    maximum: float
    average: float
    last: float


class MetricSeries(BaseModel):
    model_config = ConfigDict(frozen=True)

    metric_name: str
    unit: str
    aggregates: MetricAggregates
    points: list[MetricPoint] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    provider: str
    source: str | None = None


class DeploymentEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    service: str
    environment: str
    version: str
    timestamp: UtcDatetime
    status: DeploymentStatus
    source: str
    provider: str
    actor: str | None = None
    reference: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)


class HealthSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    service: str
    environment: str
    reported_status: HealthStatus
    source: str
    provider: str
    timestamp: UtcDatetime
    details: str | None = None


class CollectionResult(BaseModel, Generic[T]):
    """Outcome of a single provider query that was actually attempted.

    Never used to represent a capability with no provider configured, see
    CapabilityResult for that distinction.

    status and (data, truncated, failure_reason) are not independent: each
    status implies exactly one shape, enforced below rather than left to
    callers to get right by convention.
    """

    status: CollectionStatus
    data: list[T] = Field(default_factory=list)
    provider: str | None = None
    source: str | None = None
    truncated: bool = False
    truncation_reason: str | None = None
    requested_bounds: QueryBounds | None = None
    applied_bounds: QueryBounds | None = None
    failure_reason: ProviderFailureReason | None = None

    @model_validator(mode="after")
    def _check_status_shape(self) -> CollectionResult[T]:
        has_data = bool(self.data)
        has_failure = self.failure_reason is not None
        has_truncation_reason = self.truncation_reason is not None

        match self.status:
            case CollectionStatus.SUCCESS:
                if not has_data or self.truncated or has_failure:
                    raise ValueError("SUCCESS requires non-empty data, truncated=False, no failure")
            case CollectionStatus.EMPTY:
                if has_data or self.truncated or has_failure:
                    raise ValueError("EMPTY requires empty data, truncated=False, no failure")
            case CollectionStatus.PARTIAL:
                # data may or may not be empty: extraction is known incomplete
                # either way (e.g. every page scanned so far was empty but a
                # nextToken remains), that's still not EMPTY.
                if not self.truncated or has_failure or not has_truncation_reason:
                    raise ValueError(
                        "PARTIAL requires truncated=True, a truncation_reason, no failure"
                    )
            case CollectionStatus.FAILED:
                if has_data or self.truncated or not has_failure:
                    raise ValueError(
                        "FAILED requires empty data, truncated=False, a failure_reason"
                    )
        return self


def _check_capability_shape(
    availability: CapabilityAvailability, collection: CollectionResult[T] | None
) -> None:
    if availability == CapabilityAvailability.NOT_CONFIGURED and collection is not None:
        raise ValueError("NOT_CONFIGURED capability must not carry a collection")
    if availability == CapabilityAvailability.CONFIGURED and collection is None:
        raise ValueError("CONFIGURED capability must carry a collection")


class CapabilityResult(BaseModel, Generic[T]):
    """Per-capability result. availability gates whether collection was even attempted."""

    capability: str
    availability: CapabilityAvailability
    collection: CollectionResult[T] | None = None

    @model_validator(mode="after")
    def _check_availability_matches_collection(self) -> CapabilityResult[T]:
        _check_capability_shape(self.availability, self.collection)
        return self


class ServiceSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    service: str
    environment: str
    capabilities: dict[CapabilityName, CapabilityAvailability]
    available_metrics: list[str] = Field(default_factory=list)


class ServiceCatalogResult(BaseModel):
    services: list[ServiceSummary] = Field(default_factory=list)
    truncated: bool = False
    returned_count: int
    requested_max: int
    applied_max: int


class HealthResult(BaseModel):
    capability: Literal[CapabilityName.HEALTH] = CapabilityName.HEALTH
    availability: CapabilityAvailability
    collection: CollectionResult[HealthSignal] | None = None

    @model_validator(mode="after")
    def _check_availability_matches_collection(self) -> HealthResult:
        _check_capability_shape(self.availability, self.collection)
        return self


class LogsResult(BaseModel):
    capability: Literal[CapabilityName.LOGS] = CapabilityName.LOGS
    availability: CapabilityAvailability
    collection: CollectionResult[LogEvent] | None = None

    @model_validator(mode="after")
    def _check_availability_matches_collection(self) -> LogsResult:
        _check_capability_shape(self.availability, self.collection)
        return self


class MetricsResult(BaseModel):
    capability: Literal[CapabilityName.METRICS] = CapabilityName.METRICS
    availability: CapabilityAvailability
    collection: CollectionResult[MetricSeries] | None = None

    @model_validator(mode="after")
    def _check_availability_matches_collection(self) -> MetricsResult:
        _check_capability_shape(self.availability, self.collection)
        return self


class DeploymentsResult(BaseModel):
    capability: Literal[CapabilityName.DEPLOYMENTS] = CapabilityName.DEPLOYMENTS
    availability: CapabilityAvailability
    collection: CollectionResult[DeploymentEvent] | None = None

    @model_validator(mode="after")
    def _check_availability_matches_collection(self) -> DeploymentsResult:
        _check_capability_shape(self.availability, self.collection)
        return self


class OperationalSnapshot(BaseModel):
    service: str
    environment: str
    deployments: DeploymentsResult
    metrics: MetricsResult
    logs: LogsResult
    health: HealthResult
