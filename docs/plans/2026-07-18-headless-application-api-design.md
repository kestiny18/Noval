# Headless Application API Design

## Status

Accepted on 2026-07-18.

## Goal

Turn Noval into an embeddable, process-safe Python runtime without coupling the
core to CLI, Electron, HTTP, or any other host. The CLI becomes the first host
adapter for the same public API that future desktop and web adapters will use.

## Non-goals

- Electron, Node SDK, stdio JSON-RPC, HTTP, or WebSocket transports.
- Streaming model output or an asyncio rewrite.
- Multiple concurrent turns within one session.
- Runtime-owned request queues, model routing, multi-agent orchestration, or
  JavaScript tool callbacks.
- JSON serialization of Python extension objects such as callables, clients,
  stores, or tool implementations.

## Layering

```text
Host adapters (CLI / future desktop / future web)
                    |
Application API (NovalRuntime / AgentSession / DTOs)
                    |
Agent engine (canonical conversation and tool loop)
                    |
Core ports (LLMClient / Executor / SessionStore / ProcessRuntime)
                    |
Infrastructure adapters (providers / JSONL / MCP / sandbox)
```

Dependencies only point downward. Host adapters never assemble `Agent`
directly. `Agent` does not depend on the Application API. Existing core ports
remain the only locations for provider, tool execution, persistence, and
process policy.

## Lifetimes and ownership

### Process scope: `NovalRuntime`

The runtime owns immutable configuration, per-session component factories, an
immutable tool catalog snapshot, process-level logging, and a locked registry
of live session handles. It never owns a current workdir, current permission
state, current messages, or last-turn metrics.

### Session scope: `AgentSession`

Every live session owns its own Agent, canonical messages, client and judge
client, store, context manager, permission controller, confinement policy,
process runtime, hooks, skills, MCP registry and transports, task controller,
usage meter, event dispatcher, cancellation state, and non-blocking turn lock.
Two sessions may point at the same workdir but may not share mutable session
components.

### Turn scope

Every turn owns its turn id, client correlation id, temporary context, tool
activity, Stop-hook state, usage and timing metrics, cancellation request, and
terminal result. Last-turn state is returned in `TurnResult`, not exposed via
mutable fields on the public session handle.

## Isolation invariants

- No `os.chdir()` and no per-session mutation of `os.environ`.
- Every filesystem and process operation receives an explicit workdir.
- Different sessions may execute concurrently from different host threads.
- A session permits one active turn. A second call fails immediately with
  `session_busy`; the runtime never queues it.
- Provider clients and all mutable adapters are created per session unless an
  advanced caller explicitly supplies a documented thread-safe shared object.
- Tool catalogs are frozen when the runtime is created. Advanced extension
  factories create stateful tools per session.
- Persistent sessions have one live writer per process and an exclusive
  cross-process lease while open.
- Runtime logging is configured once and every record carries session, turn,
  and request correlation identifiers.

## Public object model

The stable import surface is exported from `noval` and implemented in
`noval/application.py` and `noval/api.py`.

```python
with NovalRuntime.from_settings() as runtime:
    session = runtime.create_session(
        SessionOptions(
            workdir="project",
            persistence=SessionPersistence.PERSISTENT,
        )
    )
    result = session.run_turn(TurnRequest(text="Inspect this project"))
```

`NovalRuntime` creates, resumes, lists, and closes sessions. `AgentSession`
exposes session information, turn execution, cooperative cancellation,
permission state changes while idle, and close. `TurnRequest`, `TurnResult`,
events, public errors, options, identities, usage, and metrics are explicit
data transfer objects.

Session persistence is selected per session:

- `DEFAULT`: inherit the immutable runtime configuration.
- `PERSISTENT`: create or resume the JSONL store.
- `EPHEMERAL`: remain memory-only and do not appear in persisted listings.

Provider, model, judge model, sandbox, network, and persistence settings may
override runtime defaults per session. Credentials remain runtime-owned and
never enter session DTOs, events, persistence, or transport JSON.

## Turn outcomes and public errors

Errors before `turn.started` are raised as `NovalError` subclasses. Once a turn
starts, expected operational failures produce exactly one terminal
`TurnResult` and one terminal event. Unexpected defects are logged with their
traceback and exposed only as a safe `internal_error`.

`TurnResult` carries session id, turn id, optional client request id, status,
canonical assistant message, stop reason, aggregate usage, metrics, and an
optional safe error. Normal stop reasons such as completed, max-steps, and
cancelled are not exceptions.

Public errors have a stable machine code, safe message, retryable flag,
optional correlation ids, and JSON-safe details. Raw SDK exceptions, response
bodies, credentials, and tracebacks never cross the boundary.

## Events and permission decisions

Runtime events are typed, JSON-safe, live-only observations. Each event has an
opaque id, session id, optional turn id, session-local monotonic sequence, UTC
timestamp, type, and typed payload. Initial event families cover session,
turn, model, permission, tool, and validation lifecycle.

Event sinks run synchronously on the session execution thread. They must be
fast; transport adapters may enqueue them. Sink failures are logged and do not
affect the turn.

Permissions are control flow, not observations. A session has a separate
`PermissionHandler` which receives a serializable request and returns
allow-once, allow-session, or deny. ASK without a handler fails closed.
Handler failure denies the operation. Waiting for permission blocks only that
session.

Events do not become another truth source. Session JSONL remains the canonical
conversation source and runtime logs remain the execution trace. A future host
reconstructs UI state from persisted session state, not event replay.

## Serialization contract

Public DTOs use standard-library dataclasses and enums with explicit
`to_dict`/`from_dict` functions. Wire keys use snake_case, timestamps use UTC
ISO-8601, ids are opaque strings, and serialized envelopes carry schema
version 1. Requests reject unknown fields; clients must tolerate additive
response fields and unknown event types.

Python extension ports remain native and may contain callables. JSON is only a
property of the host-facing data plane.

## Cancellation and shutdown

`cancel_active_turn()` is cooperative and safe to call from another thread.
It prevents the next model/tool step and asks `ProcessRuntime` to terminate an
owned subprocess where supported. It cannot forcibly kill a Python thread or
promise immediate interruption of a blocking provider SDK call; provider
timeouts remain the hard bound.

`close()` is idempotent for idle objects. Closing an active session returns
`session_busy`. Runtime shutdown rejects new sessions and returns
`runtime_busy` while active turns remain. Hosts cancel, wait for a terminal
result, and then close.

## Request reconstruction

Every model call receives a request id and records canonical request
provenance: session, turn, step, provider identity, adapter schema, selected
message/checkpoint inputs, tool catalog snapshot, model parameters, and the
system/context snapshot required for reconstruction.

`inspect_request(request_id)` reconstructs JSON-semantic model input. It does
not reproduce API keys, authorization headers, connection metadata, or exact
HTTP bytes. Provider payload generation remains owned by the matching adapter
and is available only through the explicit inspection API; Agent, Context,
Session, Task, and Usage never read provider wire keys.

## CLI migration

CLI parsing, formatting, slash commands, and terminal approval move to
`noval/cli.py`. The console entry point targets that module. The CLI creates a
runtime and session, consumes public events/results, and does not construct
Agent dependencies or inspect Agent fields. Existing user-visible CLI behavior
must remain covered by regression tests.

## Acceptance criteria

- The CLI runs entirely through the Application API.
- Parallel sessions cannot leak messages, workdirs, permissions, hooks, MCP,
  skills, context, usage, logs, or process state.
- Process cwd and environment are unchanged by session creation and turns.
- Same-session concurrent turns deterministically return `session_busy`.
- Persistent, ephemeral, cross-process lease, close, and cancellation behavior
  have deterministic tests.
- Event and permission failures are isolated to the owning session.
- Public DTOs pass JSON round-trip and golden schema tests.
- Every started turn has exactly one terminal result and terminal event.
- Request inspection reconstructs canonical and adapter-owned request input
  without credentials.
- Existing tests, Context Eval, Task Eval, and Linux Bubblewrap escape tests
  continue to pass.

