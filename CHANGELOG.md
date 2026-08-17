# Changelog

## 0.1.0

Initial release.

- Six typed MCP tools: `get_services`, `get_service_health`, `get_recent_deployments`,
  `get_logs`, `get_metrics`, `get_operational_snapshot`.
- Provider-neutral service registry and normalized operational domain (logs, metrics,
  deployments, health) with canonical, extensible metric names.
- Bounded telemetry queries (time range, event count, point count) with explicit
  requested vs applied bounds.
- Explicit data availability semantics: `NOT_CONFIGURED` / `EMPTY` / `PARTIAL` / `FAILED`,
  never inferred from missing data.
- Deterministic fake scenarios (`healthy`, `bad_deploy`, `partial`) for local development
  without any cloud account.
- AWS CloudWatch Logs (`FilterLogEvents`) and Metrics (`GetMetricData`) read-only providers,
  behind an optional `aws` extra.
- Structured MCP tool output via typed Pydantic models (`structuredContent` / output schema).
- Local stdio transport.
