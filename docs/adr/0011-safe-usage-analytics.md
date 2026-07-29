# ADR-0011: Expose safe usage analytics through the Application API

- Status: Accepted for implementation
- Date: 2026-07-30

## Context

Noval already records Provider-reported token usage in a user-level,
append-only JSONL side channel. The records contain timestamps, Provider model
identities, purposes, and token counts, but no project path or message content.
CLI consumers can summarize one day, while the first-party Desktop needs a
bounded 52-week activity view, all-time summary values, and model filtering.

The existing records do not contain complete Turn boundaries. A Provider
request duration is not a task duration because one Turn may contain several
model requests and tool calls. Deriving task duration from Session creation and
last-active timestamps would measure conversation lifetime rather than
execution.

Electron must not parse Core-owned usage files directly. Usage data remains a
derived side channel whose absence or corruption must never affect model
execution or canonical Session truth.

## Decision

The Core usage side channel will support two safe event kinds:

- model-usage events retain Provider-reported token counts and actual Provider
  model identity;
- terminal Turn events contain only timestamp, Provider model identity, and
  total Turn duration in milliseconds.

Neither event contains a project path, Session id, message body, tool arguments,
credentials, request payload, or Provider-private replay state. The file name
may continue to use the existing sanitized per-Session writer identity to avoid
cross-process write races; that identity is never projected through analytics.

`NovalRuntime` will expose a read-only usage analytics DTO through the
Application API. It contains:

- all-time total tokens;
- all-time peak daily tokens;
- all-time longest terminal Turn duration;
- the same all-time values per Provider model;
- exactly 364 daily buckets ending on the local current day, with per-model
  token totals for client-side filtering.

The Desktop Sidecar exposes this DTO as the additive `usage.analytics`
capability. Electron Main/Preload relay the JSON-safe response, and the Renderer
does not receive raw usage files or paths. The current envelope protocol version
does not change because the method is additive and capability-advertised.

“Peak Token count” means the greatest token total recorded on one local
calendar day. “Task duration” means elapsed wall-clock time from admitted Turn
start to its terminal result, including model, tool, approval, and validation
time. Failed and cancelled terminal Turns are included because they consumed
real user time.

## Consequences

- Existing schema-v1 token events remain readable. New typed events use a new
  event schema while aggregation tolerates missing and corrupt files.
- Historical token activity is visible immediately. Longest task duration
  begins accumulating only after this change; no duration is fabricated for
  older records.
- Model filtering is exact for the Provider model identity reported by the
  adapter. Renaming a Configured Model does not rewrite historical usage.
- Analytics read or write failures degrade to empty or partial statistics and
  structured warnings; they never change a model response or Turn result.
- Project-, Session-, cost-, weekly-, and monthly analytics remain out of
  scope.

## Alternatives considered

### Read usage JSONL directly from Electron

Rejected. It would duplicate schema ownership, expose Core storage paths to the
Desktop boundary, and couple Renderer behavior to persistence details.

### Infer task duration from Provider requests

Rejected. It excludes tools, approvals, validation, and multi-request work, so
it is not a task-duration measurement.

### Persist a full analytics database

Rejected for the current scale. Append-only daily JSONL plus bounded aggregation
preserves the existing failure model and operational simplicity.
