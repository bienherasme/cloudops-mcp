# AWS CloudWatch mode

Practical reference for running CloudOps MCP against real AWS CloudWatch. For how the
adapter is built internally (pagination, error mapping, concurrency), see
[`architecture.md`](architecture.md). For the tool surface itself, see the README.

## Install

```bash
pip install -e ".[aws]"        # runtime only
pip install -e ".[dev,aws]"    # development, needed to run the AWS provider tests
```

The base package and fake mode never require boto3. It is only imported inside the AWS
provider modules, which are only imported when `CLOUDOPS_MCP_MODE=aws`.

## Environment

```bash
CLOUDOPS_MCP_MODE=aws
CLOUDOPS_MCP_CONFIG=/path/to/cloudops.toml
```

`CLOUDOPS_MCP_MODE` accepts only `fake` (default) or `aws`; anything else fails immediately
with a clear error rather than silently falling back to fake mode. `CLOUDOPS_MCP_CONFIG` is
required when the mode is `aws`.

AWS credentials and region come entirely from boto3's standard provider chain:
`AWS_PROFILE`, `AWS_REGION` / `AWS_DEFAULT_REGION`, environment credentials, or an IAM role.
None of that is read from the CloudOps MCP config file, and none of it is logged.

## Config schema

See [`examples/aws-cloudwatch.toml`](../examples/aws-cloudwatch.toml) for a full example.
Shape, field by field:

```toml
[[services]]
name = "checkout-api"        # public identity, paired with environment
environment = "production"

[services.logs]
provider = "aws_cloudwatch"
log_group = "/aws/lambda/checkout-api"

[services.metrics]
provider = "aws_cloudwatch"
snapshot_metrics = ["request_count", "latency_p99"]   # subset of the queries below

[services.metrics.queries.request_count]   # key is the canonical metric name
namespace = "AWS/Lambda"
metric_name = "Invocations"
statistic = "Sum"
period_seconds = 60
unit = "count"                              # our output label, not sent to AWS

[services.metrics.queries.request_count.dimensions]
FunctionName = "checkout-api"
```

Validated at load, before any AWS call:

- `period_seconds` must be positive.
- `snapshot_metrics` must be a subset of that service's `queries`, and capped at 5 entries
  (the same snapshot metric cap fake mode uses).
- `dimensions` is a plain string-to-string mapping.

There is no `[services.deployments]` or `[services.health]` section: AWS mode does not
implement those capabilities yet, so a service configured this way reports `NOT_CONFIGURED`
for them.

## Canonical metric mapping

A canonical name (`request_count`, `latency_p99`, `error_count`, ...) is only meaningful for
a service if its config maps that name to a real `MetricStat` query. The vocabulary is open,
not a fixed enum, if a service only has `request_count` and `error_count` configured,
`get_metrics` only accepts those two names for it. `get_services` reports each service's
`available_metrics` so an agent can discover what is actually configured before calling
`get_metrics`.

Only `MetricStat` queries are supported in this version, no metric math expressions and no
Metrics Insights. A canonical name like `error_rate` should only be configured if the
underlying metric genuinely represents a rate; do not point it at a raw error count.

## IAM

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "logs:FilterLogEvents",
      "Resource": "arn:aws:logs:us-east-1:123456789012:log-group:/aws/lambda/checkout-api"
    },
    {
      "Effect": "Allow",
      "Action": "cloudwatch:GetMetricData",
      "Resource": "*"
    }
  ]
}
```

`FilterLogEvents` can be scoped to the log group ARN. `GetMetricData` has no resource-level
scoping in AWS's IAM model for the `MetricStat` queries this integration issues, so that
statement uses `Resource: "*"`, that reflects the API, not a choice made here. No other
permission is requested.

## No auto-discovery

CloudOps MCP never calls `ListMetrics`, `DescribeLogGroups`, or any other discovery API. If a
metric or log group is not in the config, it does not exist as far as CloudOps MCP is
concerned. This is deliberate: the tool surface should never be able to enumerate an
account's resources on its own.

## Historical log queries

`FilterLogEvents` only allows newest-first ordering (`startFromHead=False`) for time ranges
starting on or after 2024-01-01. For an older range, the provider automatically switches to
oldest-first pagination and keeps only the most recent events found so far in a bounded
buffer, so a valid CloudOps MCP time range never fails just because it is historical. See
`architecture.md` section 14 for the full mechanics.

## Live validation status

As of this version, the AWS integration has been validated with:

- Typed config parsing and `ServiceRegistry` construction against the example config, no
  network call.
- Deterministic stub-client tests for both providers (pagination, truncation, error mapping,
  out-of-order points).
- The real MCP client/server boundary (tool discovery, structured output) in both fake and
  AWS composition mode.

It has not yet been run against a real AWS account with real telemetry. CloudOps MCP does
not probe or discover account resources to find something to validate against, that requires
a user-configured log group and metrics that actually exist. If you point a config at real
resources and run it, you are doing the first live validation pass; nothing here assumes
that has already happened.
