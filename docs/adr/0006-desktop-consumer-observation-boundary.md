# ADR-0006: Expose Safe Desktop-consumer Observation Contracts

## Status

Accepted.

## Context

The Application API already isolates Sessions and emits JSON-safe lifecycle
events, but a first-party desktop host still lacks four basic capabilities:
safe transcript reads, explicit Session titles, reconnectable live events, and
incremental visible model output. Provider-native reasoning state is also easy
to confuse with a user-facing progress channel.

The canonical Session must remain the only conversation truth source. Event
delivery must not create a second durable log, and a stronger UI must not make
opaque chain-of-thought part of Noval's public contract.

## Decision

Noval will extend the Application API with a **safe observation boundary**:

- A paged transcript projects canonical Session records into host-facing
  entries. It excludes system messages, Provider replay state, provenance,
  and raw tool arguments. Persisted tool results are already normalized,
  truncated, and redacted before they reach this boundary.
- Session titles are mutable metadata. Renaming is idle-only, bounded, and
  never rewrites canonical Session JSONL.
- Every live Session keeps a bounded in-memory ring of its runtime events.
  Hosts may replay events after a sequence number and receive an explicit gap
  marker if older events were evicted.
- Event replay is process-local and observation-only. It is lost when the live
  Session closes and never replays permission control flow.
- Provider adapters may implement an optional streaming capability. It emits
  only visible assistant text deltas and returns the same final canonical
  `LLMResponse` as non-streaming completion.
- Clients without streaming support keep using `complete()` unchanged.
- Lifecycle events (`model.started`, text deltas, terminal model/turn events)
  provide ephemeral activity state. Raw reasoning, thinking blocks,
  signatures, tool-argument fragments, SDK objects, and credentials never
  enter events or public DTOs.
- Partial text from a failed or cancelled Provider stream is an ephemeral
  observation. It is marked aborted and is not appended to canonical Session
  history.

ADR-0003 remains authoritative for JSON-safe contracts and non-persistence.
This ADR supersedes only its statement that event replay is not promised: the
new replay window is explicitly bounded and memory-only.

## Responsibility boundaries

| Concern | Owner |
|---|---|
| Canonical conversation and recovery | Session JSONL |
| Safe historical projection | Application API transcript DTOs |
| Visible text reconstruction | Provider adapter |
| Streaming capability selection and fallback | Agent/provider seam |
| Event sequencing, buffering, and gap reporting | Live `AgentSession` |
| Transport queues and rendering | Desktop or CLI host |
| Opaque reasoning replay | Owning Provider adapter only |

## Non-functional requirements

- **Privacy:** no opaque reasoning or raw tool arguments cross the public
  observation boundary.
- **Compatibility:** existing `LLMClient.complete()` implementations continue
  to work without modification.
- **Reliability:** streamed and non-streamed calls produce equivalent final
  canonical responses.
- **Recoverability:** event loss never affects Session recovery; hosts rebuild
  durable UI state from the transcript.
- **Boundedness:** transcript pages and the live event window have fixed upper
  bounds.
- **Ordering:** event sequence numbers remain Session-local and monotonic for
  the lifetime of one open Session.
- **Thin harness:** the runtime exposes facts and lifecycle state without
  inventing a mandatory model workflow or interpreting private reasoning.

## Consequences

### Positive

- A desktop host can render history, live output, and reconnect within a live
  process without reaching into Agent internals.
- Provider-private reasoning stays available for protocol replay without
  becoming product-visible chain-of-thought.
- Session rename does not weaken append-only conversation recovery.
- Custom clients remain source-compatible.

### Negative

- Providers need adapter-specific stream reconstruction and parity tests.
- A slow consumer may observe an event gap and must fall back to transcript
  state.
- Events containing text deltas increase short-lived memory use.

### Neutral

- This decision does not choose a desktop toolkit or transport.
- It does not add Session deletion. Reversible archival can be designed later.
- It does not promise token-level chunks or expose raw model thinking.

## Alternatives considered

### Persist the complete event stream

Rejected because it duplicates canonical Session history and makes recovery
depend on UI delivery details.

### Expose Provider reasoning blocks to hosts

Rejected because those blocks are private protocol state, Provider-specific,
and unsafe to treat as a stable product surface.

### Replace `complete()` with a mandatory streaming API

Rejected because it would break third-party clients and couple the core to a
delivery preference.

## References

- [Detailed design](../plans/2026-07-21-desktop-consumer-readiness-design.md)
- [ADR-0001](0001-application-api-boundary.md)
- [ADR-0003](0003-json-contracts-and-live-events.md)
- [Canonical design](../../DESIGN.md)
