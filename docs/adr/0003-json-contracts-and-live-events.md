# ADR-0003: Use JSON-safe Contracts and Live-only Events

## Status

Accepted.

## Context

The v1 API is a Python SDK, but the next host may run in another language or
process. Coupling public results to Python callables, SDK objects, tracebacks,
or provider wire messages would make a future Node, desktop, or HTTP adapter a
second redesign. Persisting events would also create another source of truth
beside session records and runtime logs.

## Decision

Host-facing requests, results, events, options, and public errors use explicit
JSON-safe DTOs with schema version 1. Python extension ports remain native.
Events are ordered, best-effort, live-only observations. Permissions use a
separate request/decision control port and fail closed when unavailable.

Session JSONL remains the conversation truth source. Runtime logs remain the
execution trace. Provider replay payload and raw SDK errors never enter events.

## Consequences

### Positive

- Future transports can map the same contract without changing core behavior.
- Public data can be validated with round-trip and golden schema tests.
- Event consumers cannot corrupt Agent control flow.

### Negative

- Rich Python objects require mapping at the boundary.
- Schema evolution and unknown-field behavior become long-term obligations.
- Slow event consumers need their own queues.

### Neutral

- In-process Python calls do not actually encode and decode JSON.
- Event replay is a future host concern and is not promised by v1.

## Alternatives considered

- Require every extension object to serialize: rejected because it would
  reduce Python's tool and provider extensibility to JSON's lowest common
  denominator.
- Persist every event: rejected because it duplicates session and trace state.
- Let permission requests use ordinary events: rejected because observations
  must not become an implicit control channel.

