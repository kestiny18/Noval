# ADR-0001: Add a Headless Application API Boundary

## Status

Accepted.

## Context

Noval's CLI currently acts as presentation layer, composition root, session
manager, and dependency factory. Direct embedding requires callers to know
internal Agent construction and mutable fields. Future CLI, desktop, and web
hosts need one stable programmatic contract without moving host concerns into
the Agent engine.

## Decision

Add an Application API centered on `NovalRuntime` and `AgentSession`. Move CLI
presentation to a host adapter. Keep Agent as the canonical conversation and
tool-loop engine, and preserve all existing provider, registry, executor,
session, and process ports below the Application API.

The stable public surface is exported from `noval`. Python extension ports
remain native and distinct from JSON-safe host DTOs.

## Consequences

### Positive

- CLI, desktop, and web hosts can share the same use cases and semantics.
- Internal Agent construction can evolve without breaking host applications.
- Safety, persistence, and execution policy remain centralized in existing
  core ports.

### Negative

- DTO mapping and a new lifecycle layer add code and tests.
- v1 public contracts require deliberate compatibility management.

### Neutral

- Agent remains usable internally but is not a v1 stable entry point.

## Alternatives considered

- Add more methods to Agent: rejected because it freezes internal mutable
  implementation and preserves constructor explosion.
- Rewrite Agent as a fully asynchronous event engine: rejected as unnecessary
  for the synchronous v1 SDK and too broad for this release.
- Build an HTTP service first: rejected because transport is a host adapter,
  not the core embedding contract.

