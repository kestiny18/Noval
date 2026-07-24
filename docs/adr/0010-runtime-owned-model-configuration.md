# ADR-0010: Adopt Runtime-owned OpenAI-compatible Model Configuration

## Status

Accepted for implementation on 2026-07-24.

## Context

Noval currently loads one flat Provider, model, judge model, endpoint, and
credential source when a Runtime starts. The first-party Desktop can replace
that configuration only by storing an encrypted credential in Electron,
writing a temporary flat Runtime settings file, and restarting the Python
Sidecar.

The product now needs multiple Provider Connections, multiple selectable
models, durable per-Session selection, and configuration changes that do not
mutate an active Turn. The execution kernel must remain the sole owner of
Provider resolution, Session state, request construction, redaction, and
evidence. Desktop must not become a second Provider factory.

Supporting multiple protocols at the same time would combine the configuration
contract with cross-Adapter context conversion and private replay routing.
Phase 1 should first prove the model through the dominant OpenAI-compatible
protocol.

Noval is still an internal pre-release product. Existing settings, Session, API,
and Sidecar contracts have no external compatibility commitment.

## Decision

### Configuration model

Runtime owns four distinct concepts:

```text
trusted Provider Profile
→ user Connection
→ user-selectable Configured Model
→ immutable per-Turn Model Binding
```

Phase 1 exposes only OpenAI-compatible Profiles and Custom Connections. The
Connection and binding schemas retain Adapter identity, but Desktop does not
offer an Adapter selector. The existing Anthropic Adapter remains in the
repository and is not exposed through the new configuration product.

### State and versions

Phase 1 intentionally advances these independent contracts without migration:

```text
settings schema v2
canonical Session schema v3
Application API schema v2
Desktop Sidecar protocol v2
```

Old files are rejected without rewriting, deletion, inference, or fallback.
Only current-schema Sessions participate in discovery.

The append-only Session JSONL remains canonical conversation truth. The current
Configured Model selection is mutable Session application metadata, persisted
atomically in the existing metadata sidecar. Missing or corrupt v3 application
metadata fails explicitly and never falls back to the global default.

### Turn consistency

Each Turn captures one immutable configuration snapshot, agent binding, judge
binding, and their clients before becoming active. Selection and configuration
updates may occur during a Turn but affect only a later Turn.

Agent completion, Context compaction, and semantic completion judging all use
the captured Turn clients. Runtime caches SDK transports by safe Connection
identity and creates lightweight model-bound clients for each Turn. Active
Turns retain retired transports until their references are released.

### Replay isolation

Provider-private replay state remains opaque. Safe routing metadata binds it to
an Adapter, Connection, Provider model, transport revision, and Adapter schema.
Another scope never receives it, even when both Providers use the
OpenAI-compatible Adapter.

Optional foreign replay is omitted. Replay required for protocol correctness
fails explicitly.

### Credentials

Runtime configuration owns Connection credentials. Phase 1 may store API keys
as plaintext in the user-local `~/.noval/settings.json` file. Desktop submits
write-only replace/clear patches and never receives an existing value.

This supersedes ADR-0007's statement that Electron Main owns encrypted Provider
credentials. Electron continues to own presentation-only preferences.

Secret values and sensitive mutation DTOs have redacted representations.
Credentials never enter Session data, public DTOs, events, errors, logs,
traces, usage, request journals, diagnostics, or Desktop preferences.

### Configuration concurrency

A Runtime configuration store performs mutation as one validated transaction
under an in-process lock and a short-lived cross-process writer lease. It
atomically replaces the settings file and then swaps one immutable in-memory
snapshot. Other live Runtime processes observe the update only after explicit
reload or restart.

### Host boundary

Application API v2 is the only model-configuration authority. Desktop Sidecar
protocol v2 maps that contract without duplicating validation or credential
storage. CLI operations use the same Runtime methods.

## Consequences

### Positive

- Multiple Providers and models do not add dispatch logic to the Agent loop.
- Active Turns are deterministic under selection and configuration changes.
- Desktop no longer restarts the Sidecar to update model configuration.
- Provider-private replay cannot cross accounts or endpoints that share one
  Adapter.
- The first implementation validates the dominant protocol without coupling the
  schema to it.
- A future Anthropic phase can reuse configuration, Session selection, and Turn
  binding semantics.

### Negative

- Existing internal settings and Sessions become undiscoverable until recreated
  in the new schema.
- API keys are stored locally as plaintext in Phase 1.
- Runtime gains configuration locking and transport-lifecycle complexity.
- Application API and Sidecar consumers must update to v2 together.

### Neutral

- Existing Anthropic Adapter code and tests remain, but Anthropic is not a Phase
  1 product capability.
- Runtime does not watch settings files for external changes.
- Session references to deleted Configured Models are weak and fail only when a
  later Turn resolves them.

## Superseded clauses

- ADR-0007 remains authoritative for the Desktop process and security boundary,
  except that Runtime configuration now owns Provider credentials.
- ADR-0008's discover-only-current-schema policy remains authoritative, while
  the current canonical Session schema advances from v2 to v3.
- ADR-0003's JSON-safe and live-only event decisions remain authoritative, while
  the Application API schema advances from v1 to v2.

## Alternatives considered

### Keep Electron `safeStorage` as the credential authority

Rejected for Phase 1 because CLI and other hosts would require a separate
credential resolver, and Runtime configuration updates would remain dependent
on Host-specific transport. A future credential-slot interface may restore
native secure storage without changing Connection ids.

### Support OpenAI-compatible and Anthropic configuration together

Deferred because it would make cross-Adapter context and replay semantics part
of the first vertical slice. Provider-neutral internal types are retained.

### Store the current selection in the Session JSONL header

Rejected because the header is immutable while selection may change during an
active Turn.

### Append selection events to canonical conversation JSONL

Rejected for Phase 1 because it would mix mutable operational state with
message-sequence and checkpoint semantics. The existing metadata sidecar is the
established Session state boundary.

### Cache complete model-bound clients

Rejected because one Connection may expose multiple models, while metering and
request journals are Session- and Turn-specific.

### Migrate old settings and Sessions

Rejected because there is no external compatibility commitment and inference
could select the wrong credential destination.

## References

- [Detailed design](../plans/2026-07-24-provider-model-configuration-design.md)
- [ADR-0001](0001-application-api-boundary.md)
- [ADR-0002](0002-session-isolation-and-concurrency.md)
- [ADR-0003](0003-json-contracts-and-live-events.md)
- [ADR-0006](0006-desktop-consumer-observation-boundary.md)
- [ADR-0007](0007-first-party-desktop-host.md)
- [ADR-0008](0008-current-session-schema-discovery.md)
- [Canonical design](../../DESIGN.md)
