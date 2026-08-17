# Architecture

This document goes deeper than the README. It assumes you already know what CloudOps MCP
does and want to know how it is built.

## 1. Layers

```
providers/   -> vendor-specific adapters (fake, aws)
domain/      -> normalized models, shared by every provider and service
services/    -> deterministic bounds, ordering, aggregation, capability wrapping
tools/       -> MCP tool registration, the only layer that touches the MCP SDK
```

Each layer only talks to the one below it. Tools call services, services call providers
through a `Protocol`, providers never see the MCP layer at all. A provider adapter can be
swapped (fake for AWS, or a future vendor) without touching `services/` or `tools/`.

## 2. ServiceRegistry and service identity

Identity is `(service, environment)`, nothing else. `ServiceRegistry`
(`domain/identity.py`) is a static, configuration-backed lookup, not a CMDB and not
auto-discovered. Entries are declared up front, either by a fake scenario module or by
parsing a TOML config in AWS mode, and identity is never inferred or fuzzy-matched at
runtime.

## 3. Capability bindings

A `ServiceCatalogEntry` holds a `CapabilityBinding` per capability (`logs`, `metrics`,
`deployments`, `health`) that is actually configured for that service. A missing binding
means the capability is `NOT_CONFIGURED`, no provider call is ever attempted for it.

`CapabilityBinding.provider_ref` is an opaque string a provider adapter uses to look up its
own configuration (a log group name, a set of CloudWatch metric queries). It is never
AWS-specific by name, `ServiceRegistry` and `CapabilityBinding` stay provider-neutral;
AWS-specific config (log group, namespace, dimensions) lives in
`providers/aws/config.py`, keyed by that same opaque ref, and never becomes part of the
domain layer.

`available_metrics` is derived, not separately configured: it comes straight from a
metrics binding's `metric_map` keys. There is a single source of truth for which canonical
metric names exist for a service.

## 4. Provider interfaces

Four `Protocol` classes in `providers/base.py`: `LogsProvider`, `MetricsProvider`,
`DeploymentsProvider`, `HealthProvider`. Each maps to one telemetry domain, mirroring how
real vendors actually split these APIs (CloudWatch Logs and CloudWatch Metrics are separate
services). None of them declare a mutation method. A provider receives already-resolved
bounds (a time range, a max count) and is expected to respect them and report truncation,
the same way a real vendor call reports an incomplete page through a pagination cursor.

Providers only ever raise `ProviderQueryError` for failures they can classify (see
section 11). Anything else that escapes a provider call is a bug, not telemetry.

## 5. Normalized domain

`domain/models.py` defines the shapes every provider normalizes into: `LogEvent`,
`MetricPoint` / `MetricSeries`, `DeploymentEvent`, `HealthSignal`, and the wrapper types
around them. Nothing in this module is vendor-specific. Timestamps use a shared
`UtcDatetime` type that rejects naive datetimes and normalizes aware ones to UTC, a naive
timestamp is a bug, not something to guess a timezone for.

## 6. Collection semantics

`CollectionResult[T]` carries the outcome of one provider query that was actually
attempted: `status`, `data`, `truncated`, `truncation_reason`, `failure_reason`, plus
`requested_bounds` / `applied_bounds`. A Pydantic model validator enforces that each status
implies a specific shape, so an inconsistent result cannot be constructed anywhere in the
codebase, fake or real:

| Status | data | truncated | truncation_reason | failure_reason |
|---|---|---|---|---|
| `SUCCESS` | non-empty | `False` | - | absent |
| `EMPTY` | empty | `False` | - | absent |
| `PARTIAL` | either | `True` | required | absent |
| `FAILED` | empty | `False` | - | required |

`PARTIAL` deliberately allows empty data. Extraction can be known incomplete before any
usable data has been collected, for example CloudWatch Logs can return an empty page while a
`nextToken` still exists. That is not `EMPTY` (which means extraction was exhausted and
nothing matched), it is `PARTIAL` with `data=[]` and a `truncation_reason` explaining why.
This is not the same thing as server-side clamping: a request clamped down to the hard cap
that is still fully satisfied within that clamped window is `SUCCESS`, not `PARTIAL`.

`CapabilityResult` (and its four concrete wrappers, `HealthResult`, `LogsResult`,
`MetricsResult`, `DeploymentsResult`) separately enforces `NOT_CONFIGURED` implies no
collection and `CONFIGURED` implies one. `CapabilityAvailability` and `CollectionStatus` are
independent axes: whether a capability exists for a service is a different question from
what happened when it was queried.

## 7. Bounds and defensive normalization

`bounds.py` is the single place every hard cap and default window lives. Services resolve
bounds through `resolve_time_range` and `clamp_max_items` rather than hardcoding numbers.

The service layer is the last barrier before MCP output, so it never trusts a provider's
ordering or count alone, including the fake providers. `cap_ordered_items` re-sorts
newest-first and re-caps to the applied bound regardless of what a provider already claims
to have done; `logs_service` and `deployment_service` both use it. `metrics_service` runs
the equivalent logic for points: sort ascending by timestamp, then keep the most recent
points if a cap is needed, so `last` in the aggregates always means the temporally most
recent value, never whatever position a provider happened to return it in.

## 8. Log message handling

Log messages are opaque, untrusted text. They are never parsed, executed, or interpreted,
there is no keyword search for "ERROR" and no attempt to infer a severity level from
content. `LogEvent.level` is optional: fake providers populate it because their canned data
happens to carry one, CloudWatch Logs has no portable severity concept and always reports
`level=None`. Messages are capped to a fixed length; when a message is truncated,
`LogEvent.message_truncated` is set explicitly rather than leaving it to be inferred from a
suffix in the text.

## 9. Metric normalization and aggregates

Aggregates (`minimum`, `maximum`, `average`, `last`) are always computed by
`metrics_service`, never supplied by a provider, so every vendor is judged by the same
arithmetic. Canonical metric names are ours; a service's config maps each one to a real
metric query (an AWS `MetricStat`, or a fake dataset key). The vocabulary is open, not a
closed enum, a service that only has `request_count` and `error_count` and no `error_rate`
simply does not expose `error_rate`, nothing invents it.

## 10. Operational snapshot concurrency

`snapshot_service.get_operational_snapshot` runs its four primitive calls (deployments,
logs, metrics, health) concurrently with `asyncio.gather`, sharing one `now` for the
time-bounded ones. It composes the same primitive services every individual tool uses, it
never talks to a provider directly. A failure or `NOT_CONFIGURED` capability in one section
never hides the others: each section keeps its own `CapabilityResult`, and if a genuine bug
(not a classified provider failure) occurs during one of the concurrent calls, it propagates
rather than being reported as a quiet, misleading `FAILED` section.

## 11. Provider error boundary

`ProviderQueryError(reason: ProviderFailureReason)` is the only exception the services layer
catches from a provider. A real adapter is responsible for turning the specific external
failures its SDK can raise (auth, timeout, rate limit, unavailable) into this, sanitized,
before it crosses into services. Anything else, a `KeyError` from broken wiring, an
`AssertionError`, a Pydantic `ValidationError`, is a bug and is deliberately left uncaught so
it surfaces as an error instead of being silently reported to the agent as ordinary
`FAILED` / `UNKNOWN` telemetry. `UNKNOWN` stays valid only for a genuinely unclassifiable
external error, never as a catch-all for our own bugs.

For AWS, `providers/aws/errors.py` maps a fixed, narrow tuple of botocore exception types
(`ClientError`, `ConnectTimeoutError`, `ReadTimeoutError`, `EndpointConnectionError`,
`NoCredentialsError`, `PartialCredentialsError`) to a `ProviderFailureReason`.
`ParamValidationError` is not in that tuple on purpose: it means our own request
construction was wrong, and should propagate as a bug.

## 12. MCP tool boundary

`tools/register.py` is the only module that imports the MCP SDK. Each tool is a thin
wrapper: call the matching service, and sanitize whatever comes back. `InvalidArgumentError`
becomes a `ToolError` with its own safe message (unknown service, a malformed time range). A
primitive, single-capability tool that ends up fully `FAILED` also becomes a `ToolError`,
there is nothing else useful to return. `get_operational_snapshot` never does this, a
`FAILED` or `NOT_CONFIGURED` section stays in its structured result alongside whatever else
succeeded. Any other exception is treated as a bug: only a fixed, generic message crosses
the boundary, the original exception is chained with `from exc` for local debugging, never
for display.

## 13. Structured output

Every tool's return type is a Pydantic `BaseModel`. The official Python MCP SDK derives
`output_schema` and `structuredContent` directly from that annotation, there is no manual
JSON serialization into a text block anywhere in this codebase. The SDK also serializes the
same object to text for backward compatibility automatically; that channel is not
hand-maintained.

## 14. AWS adapter architecture

`providers/aws/` has five modules:

- `config.py`: the TOML schema and `ServiceRegistry` construction. Pure Pydantic and
  `tomllib`, no boto3 import, so it can be exercised without the `aws` extra installed.
- `client.py`: one shared `botocore.config.Config` (conservative timeouts, a short retry
  budget), used by both providers.
- `errors.py`: the botocore-to-`ProviderFailureReason` mapping described above.
- `logs.py`: `AwsCloudWatchLogsProvider`, `FilterLogEvents` only, with defensive
  pagination (see below).
- `metrics.py`: `AwsCloudWatchMetricsProvider`, `GetMetricData` only, `MetricStat`
  queries.

boto3 is synchronous; both providers wrap their blocking calls in `asyncio.to_thread`
rather than introducing a thread pool of their own. Each provider builds its boto3 client
once, during server composition, and reuses it, boto3 clients are documented as thread-safe
once constructed.

Pagination is defensive because AWS's own API is: a `FilterLogEvents` page can be empty
while a `nextToken` still exists, so an empty page never means finished, only an absent
token does. A bounded page-guard prevents an unbounded scan. AWS also restricts
`startFromHead=False` (newest-first) to ranges starting on or after 2024-01-01; for older
ranges the provider pages oldest-first instead and keeps only the most recent events seen so
far in a bounded rolling buffer, rather than stopping as soon as it has collected enough
(which would return the oldest events in the window, not the most recent).

## 15. Read-only guarantee

This is a design property, not just documentation. The provider `Protocol`s declare no
mutation methods, so there is no interface to accidentally call one through. The AWS
adapters call exactly two botocore methods, `filter_log_events` and `get_metric_data`,
nothing else. There is no shell execution and no cloud CLI subprocess call anywhere in the
codebase.

## 16. Extension path for future providers

Adding a provider (Kubernetes, Datadog, a second AWS capability) means: implement the
relevant `Protocol`(s) in `providers/<vendor>/`, keep vendor-specific config out of
`domain/` and `CapabilityBinding`, map the vendor's failure modes to
`ProviderFailureReason` through `ProviderQueryError`, and wire a `provider_ref` through a
config loader the same way `providers/aws/config.py` does. Nothing in `services/` or
`tools/` needs to change.

## 17. Future consumption

CloudOps MCP is not coupled to any specific consumer. An incident-response agent is one
plausible client, and a useful one to design against mentally, but it talks to CloudOps MCP
the same way any other MCP-capable client would: over stdio, through the six tools described
in the README. No Incident Commander-specific field, tool, or shape exists anywhere in this
codebase.
