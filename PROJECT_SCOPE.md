# CloudOps MCP

## Purpose

CloudOps MCP is a read-oriented Model Context Protocol server for exposing operational infrastructure data to AI agents through a small set of well-defined tools.

The goal is to provide a consistent interface over operational signals such as logs, metrics, deployments, and service health without giving the agent unrestricted access to infrastructure systems.

## Initial Scope

The first version will focus on:

- service and environment discovery
- recent deployment history
- operational metrics
- relevant log events
- service health signals
- bounded time-range queries
- normalized responses across providers
- explicit handling of partial or unavailable telemetry

## Architecture Direction

The system will separate:

- provider-specific API clients
- normalized operational domain models
- deterministic aggregation and classification
- MCP tool definitions
- transport and configuration

Provider integrations should remain behind narrow interfaces so the MCP contract is not coupled to a specific cloud or observability vendor.

Service identity is normalized across providers: a service is identified by name and environment, with vendor-specific references kept internal to provider bindings rather than exposed in the public contract.

The initial implementation may use simulated providers before real integrations are introduced.

## Safety

The default design is read-only.

The server should not:

- deploy services
- restart infrastructure
- modify cloud resources
- execute shell commands
- acknowledge alerts
- change monitoring configuration

Operational writes belong behind separate, explicitly controlled workflows.

## Design Principles

- read-oriented by default
- deterministic operational facts before agent interpretation
- compact and typed tool responses
- bounded queries
- explicit partial-data semantics
- provider isolation
- no raw credential exposure
- no assumption that missing data means healthy infrastructure

## Out of Scope Initially

- autonomous remediation
- infrastructure mutation
- deployment orchestration
- incident ticket creation
- general-purpose cloud administration
- production-scale distributed persistence
- AI-generated infrastructure changes

## Relationship to Other Personal Projects

This project is intended to provide operational context that can later be consumed by independent incident-response or engineering automation systems.

## Project Origin

This project concept and its initial scope were defined before the start of my next employment engagement.