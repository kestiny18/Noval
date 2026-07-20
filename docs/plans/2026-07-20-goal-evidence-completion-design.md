# Goal, Evidence, and Completion Design

## Problem

Noval can execute tools, run deterministic project Hooks, persist a semantic
completion verdict, and return a public terminal status. Those pieces do not
yet form one trustworthy completion contract:

- a host cannot state the exact goal, authorized scope, and acceptance
  criteria as structured data;
- tool execution is visible in model context but is not exposed as a bounded,
  durable, JSON-safe receipt;
- deterministic validation and semantic judgment are not combined under an
  explicit precedence rule; and
- `completed` can describe a fluent final reply even when external acceptance
  evidence is absent or stale.

The design must improve truthfulness without prescribing how the model plans,
investigates, or performs the work.

## Design goals

1. Let a host opt into an explicit, portable goal contract.
2. Record what the runtime actually attempted and executed without persisting
   argument values, tool output, credentials, or hidden reasoning.
3. Tie verification evidence to named acceptance criteria and freshness rules.
4. Produce a deterministic completion report that cannot be upgraded by model
   confidence or semantic judgment.
5. Preserve the current lightweight behavior when no structured goal is
   supplied.
6. Persist and recover the contract as derived task state without changing the
   canonical Session schema.

## Approaches considered

### 1. Expand the semantic judge

The judge could receive more prompt instructions and ask whether the visible
answer sounds sufficiently verified.

This is inexpensive, but it cannot prove hidden tool execution or current
external state. It would preserve the exact self-reporting gap this work is
intended to close.

### 2. Explicit contract plus runtime evidence (selected)

The host may attach a `GoalContract` to a turn. The runtime records bounded
`ActionReceipt` values for tool calls, accepts explicit `VerificationResult`
values from trusted hosts, converts mapped Stop Hook results into verification
results, and derives a `CompletionReport` from the latest sufficiently fresh
evidence for every criterion.

This adds an opt-in truth boundary while leaving method selection with the
model. It also keeps verification extensible: a CLI, service, test runner, or
future host can submit evidence without teaching the executor domain intent.

### 3. Infer goals and acceptance from model behavior

The runtime could classify user intent, infer targets from tool arguments, and
decide which actions prove completion.

This appears automatic but couples the executor to probabilistic intent
guessing, risks persisting sensitive values, and creates hidden workflow
policy. It is intentionally rejected.

## Public contract

### GoalContract

A goal is host-supplied structured context, not a plan and not a permission
grant. It contains:

- a stable goal id;
- the intended objective;
- concise scope and authority notes; and
- one or more named acceptance criteria.

Each acceptance criterion may require a specific verification source and may
set a maximum evidence age. A source is a stable identifier such as
`hook:test-suite` or `host:deployment-api`. Omitting a source lets a trusted
host provide the result explicitly, but never lets a tool receipt satisfy the
criterion by itself.

Submitting a different goal id replaces the active structured goal. Submitting
the same goal id must be contract-compatible; silently changing acceptance
criteria under an existing id is rejected.

### ActionReceipt

Every requested tool call produces a safe receipt containing:

- receipt and Provider call ids;
- tool name and effective risk;
- observation/action classification;
- whether execution occurred and its outcome;
- start and completion timestamps;
- argument key names, duration, truncation, and redaction flags; and
- a digest of the already-redacted model-facing result.

The target is a safe runtime identifier (`tool:<name>`), not reconstructed
argument data. Receipts prove that an invocation occurred; they do not prove
that an acceptance criterion passed.

### VerificationResult

A verification result identifies the goal, criterion, source, outcome,
observation time, optional safe subject and summary, and related receipt ids.
The task layer redacts free text before persistence. A result whose source does
not match a criterion's required source cannot satisfy that criterion.

Configured Stop Hooks are deterministic sources. A matching Hook result maps
as follows:

| Hook outcome | Verification outcome |
|---|---|
| `allow` | `passed` |
| `deny` | `failed` |
| `context` | `unknown` |

Pre- and PostToolUse Hooks keep their current policy and diagnostic roles; they
do not become completion proof.

### CompletionReport

For an explicit goal, the runtime evaluates each criterion from its latest
matching verification result:

- any current `failed` result makes the goal `incomplete`;
- any missing, stale, or `unknown` result makes the goal `uncertain`; and
- only current `passed` results for every criterion make the goal `completed`.

The report exposes criterion-level states and evidence references. Operational
stop reasons remain separate. A runtime error still reports a failed turn;
otherwise the contracted completion status becomes the public task status.

The semantic judge remains visible as a semantic assessment of the final
reply. It cannot replace, upgrade, or override contracted evidence. When no
structured goal exists, Noval preserves the current semantic-judge behavior.

## Runtime flow

```text
TurnRequest(goal?)
  -> activate or verify compatible GoalContract
  -> expose goal and current criterion state as observed turn context
  -> model chooses its own method
  -> executor produces safe ActionReceipt values
  -> Stop Hooks produce mapped VerificationResult values
  -> task layer evaluates current evidence
  -> semantic judge records a separate visible-reply assessment
  -> TurnResult(completion, receipts, operational stop reason)
```

A host may also record verification while the Session is idle, then query the
updated completion report. This uses the same per-Session concurrency boundary
as permission changes.

## Persistence and recovery

Goal, receipt, verification, semantic verdict, and completion report are
derived task state stored in the existing task sidecar. The canonical Session
JSONL remains unchanged and remains the only conversation truth source.

Task event schema v2 accepts existing schema-v1 snapshots and migrates their
semantic state in memory. Stored receipt and verification history is bounded.
Corrupt derived records are skipped as today, so task evidence failure cannot
destroy or rewrite the Session.

## Safety and privacy

- Goal scope and authority notes document intent but do not bypass permissions,
  confinement, sandboxing, Hooks, or system policy.
- Receipts never persist argument values or raw tool output.
- Verification free text is redacted before persistence.
- Evidence ids and source ids have bounded formats and lengths.
- Future timestamps and cross-goal or cross-criterion evidence are rejected.
- No opaque Provider thinking enters the contract.

## Compatibility

- Existing `TurnRequest` and `TurnResult` JSON remain valid through additive
  optional fields.
- Existing callers that omit a goal keep the current behavior.
- Existing Hook files remain valid without changes.
- Existing task sidecars restore semantic history but have no fabricated
  structured evidence.
- Session schema v2 and checkpoint behavior do not change.

## Validation strategy

- DTO round-trip, strict parsing, compatibility, and golden API tests.
- Unit tests for missing, stale, mismatched, unknown, failed, and passing
  verification.
- Tool receipt tests covering success, denial, failure, redaction, and safe
  persistence.
- Stop Hook composition tests and explicit semantic-judge non-escalation.
- Persistent Session resume and corrupt task-sidecar recovery tests.
- Offline behavior Eval cases using `MockClient`; no live model or network is
  required.
