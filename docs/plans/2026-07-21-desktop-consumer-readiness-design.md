# Desktop Consumer Readiness Design

## Status

Accepted on 2026-07-21.

## Goal

Prepare Noval's headless Application API for a future first-party desktop host
without choosing a UI toolkit or adding a transport. The host must be able to
read safe history, rename a Session, render visible output while it is being
generated, and recover a bounded window of live events after a transient
consumer disconnect.

## Non-goals

- Desktop layout, framework, IPC, WebSocket, HTTP, or Node bindings.
- Session deletion or retention policy.
- Persisted event replay.
- Raw chain-of-thought, Provider thinking blocks, or reasoning signatures.
- An asyncio rewrite or concurrent turns inside one Session.
- A mandatory streaming requirement for custom `LLMClient` implementations.

## Public contracts

### Transcript

`AgentSession.transcript(after_sequence=0, limit=100)` returns a bounded page.
Each entry contains a stable one-based transcript sequence, timestamp when available,
role, visible text, safe tool-call descriptors, and safe tool results. Tool
descriptors expose call id, name, and argument keys rather than raw arguments.
System content, replay state, and provenance are omitted.

Persistent transcripts use JSONL envelope sequence and timestamps. Ephemeral
Sessions keep equivalent in-memory envelopes for their live lifetime. A page
returns `next_sequence` and `has_more`; callers can incrementally fetch history
without loading an entire large Session.

### Rename

`AgentSession.rename(title)` is idle-only. It trims the title, rejects empty or
overlong values, updates the persistent metadata sidecar when present, updates
`SessionInfo`, and emits `session.renamed`. It does not append, delete, or
rewrite conversation records.

### Live event replay

Every event is created and inserted into a per-Session bounded deque before a
best-effort sink call. `replay_events(after_sequence=0, limit=100)` returns
events in order plus the oldest and latest retained sequence and
`gap_detected`. A gap means the requested cursor predates the retained window;
the host must rebuild durable state from `transcript()` and then continue from
the returned events.

The event deque is neither serialized nor restored. Permission events in the
deque are historical observations only; replay never invokes a permission
handler.

### Visible text streaming

`LLMClient.complete()` remains the required Provider port. An optional
streaming capability accepts a callback for provider-neutral stream events and
still returns one final `LLMResponse`. The Agent selects streaming when the
client stack exposes it and otherwise falls back to `complete()`.

Only visible assistant text produces `model.output.delta`. The adapter owns
fragment assembly for tool calls and opaque replay state. On failure after one
or more deltas, the Agent emits `model.output.aborted`; no partial assistant
message is persisted. The final canonical response remains the sole input to
the tool loop, Context, Session, usage meter, and completion judge.

`model.started` and `model.completed` are sufficient for a host to display an
ephemeral activity indicator. Reasoning token counts may appear in terminal
metrics after completion, but raw reasoning content never crosses the adapter
boundary.

## Data flow

```text
Provider SDK stream
  -> Provider adapter reconstructs final response
  -> visible text delta callback only
  -> Agent observer
  -> AgentSession event ring (bounded, memory-only)
  -> best-effort host event sink

Canonical final response
  -> Agent tool loop / Session append
  -> safe transcript projection
  -> durable desktop history
```

## Failure semantics

- A non-streaming client continues normally.
- A stream error uses the existing normalized `ProviderError` path.
- Observed partial text is followed by `model.output.aborted` and never appears
  in the durable transcript unless a final response was successfully formed.
- A sink exception cannot interrupt the Provider stream or turn.
- An event-buffer gap is explicit and recoverable through transcript state.
- A corrupt persistent Session record is skipped according to existing Session
  recovery rules; transcript reads do not rewrite it.
- Rename failure leaves the previous title and canonical history unchanged.

## Verification

- JSON round-trip and golden-shape tests for new DTOs and event types.
- Privacy regression tests proving replay state and raw tool arguments do not
  enter `TurnResult`, transcript DTOs, or events.
- Persistent and ephemeral transcript pagination tests.
- Rename persistence, resume, idle-only, and event tests.
- Event ordering, eviction, gap, sink-failure, and close-lifetime tests.
- Provider adapter tests for text-only, tool-call, usage, thinking, error, and
  cancellation-adjacent streams.
- Fallback tests for legacy custom clients and parity tests for the final
  canonical `LLMResponse`.
- Full repository tests, Context Eval, Task Eval, and `git diff --check`.
