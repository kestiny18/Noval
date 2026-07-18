# ADR-0002: Isolate Mutable State Per Session

## Status

Accepted.

## Context

A headless process must host multiple sessions concurrently. The current CLI
changes process cwd and assembles state for one active session. Global cwd,
environment, metrics, mutable registries, clients, transports, and logging
handlers can leak data or behavior between concurrent sessions.

## Decision

One runtime may own many sessions. Different sessions may run concurrently on
host threads. A session allows one active turn and rejects another immediately
with `session_busy`; Noval does not queue turns.

All mutable execution state is session-owned. Process-level shared state is
immutable or explicitly thread-safe. Noval never changes process cwd or
environment for a session. Persistent sessions use an exclusive writer lease
while open.

## Consequences

### Positive

- Session data, permissions, tools, processes, and diagnostics have explicit
  ownership.
- Hosts control scheduling without an asyncio rewrite or hidden queue.
- Isolation is deterministic and testable.

### Negative

- Clients and mutable adapters are created more often.
- Hosts must handle `session_busy` and choose their own queue policy.
- Cross-process leases add platform-specific file-locking code.

### Neutral

- Stateless immutable definitions may still be shared.
- Explicitly shared third-party mutable objects remain caller responsibility.

## Alternatives considered

- One global Agent: rejected because sessions cannot be isolated.
- Runtime-wide lock: rejected because one blocked session would stall all
  others.
- Per-session automatic queue: rejected because stale user input could execute
  much later without host control.

