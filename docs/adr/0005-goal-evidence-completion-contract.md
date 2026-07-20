# ADR-0005: Establish Goal, Evidence, and Completion Contracts

## Status

Accepted

## Context

ADR-0004 gives the model responsibility for strategy while the runtime owns
hard invariants and verification boundaries. Noval currently has the pieces of
that boundary—tool results, deterministic Hooks, a semantic completion judge,
task state, and a public terminal result—but no contract that ties a stated
goal to criterion-level evidence.

A semantic judge sees the recent user inputs and final visible reply. It cannot
prove hidden tool execution or external state. Tool success proves only that a
tool returned successfully, not that the user's acceptance conditions hold.
Consequently, model confidence and execution success must not be allowed to
stand in for required evidence.

## Decision

Noval will add an **optional goal, evidence, and completion contract** at the
Application API and task-state boundary.

- A host may submit a `GoalContract` containing a stable id, objective, scope,
  authority notes, and named acceptance criteria.
- A goal contract records intent; it is not a plan, workflow, or permission
  grant.
- Every tool call produces a bounded `ActionReceipt` with safe provenance and
  execution metadata. It excludes argument values, raw output, credentials,
  message bodies, and opaque thinking.
- A trusted host may record a criterion-bound `VerificationResult` while the
  Session is idle.
- A criterion may name a verification source and a maximum evidence age.
- A configured Stop Hook is a deterministic verification source when a
  criterion names `hook:<hook-id>`. Allow, deny, and context map to passed,
  failed, and unknown respectively.
- PreToolUse and PostToolUse Hooks retain their existing policy and diagnostic
  semantics and do not become completion proof.
- A `CompletionReport` is derived from the latest matching, sufficiently fresh
  verification for every criterion.
- Any failed criterion makes the result incomplete. Any missing, stale, or
  unknown criterion makes it uncertain. Only all-current passing criteria make
  it complete.
- Action receipts can be referenced by verification results but never satisfy
  criteria on their own.
- The semantic judge remains a separate assessment of the visible reply. It
  cannot upgrade or override deterministic contracted evidence.
- Without an explicit goal contract, existing semantic completion behavior is
  preserved.

The task sidecar advances to schema v2 as recoverable derived state. Schema-v1
snapshots remain readable. Canonical Session schema v2, checkpoints, and raw
conversation history do not change.

## Responsibility boundaries

| Concern | Owner |
|---|---|
| Objective, scope, authority notes, acceptance criteria | Host/user contract |
| Method selection and user communication | Main model |
| Tool authority and execution semantics | Permission controller and executor |
| Tool invocation facts | Runtime action receipts |
| Project-specific deterministic checks | Stop Hooks |
| External validation | Trusted host integration |
| Criterion freshness and completion derivation | Task layer |
| Visible-reply assessment | Semantic judge |
| Conversation truth and recovery | Canonical Session |

## Non-functional requirements

- **Safety:** evidence cannot expand authority or bypass runtime boundaries.
- **Privacy:** persisted evidence contains no argument values, raw tool output,
  credentials, message bodies, or opaque Provider state.
- **Reliability:** required evidence cannot be replaced by a semantic verdict,
  tool success, or model confidence.
- **Freshness:** time-bounded criteria become uncertain when evidence expires.
- **Recoverability:** corrupt or legacy derived state cannot damage canonical
  Session history.
- **Compatibility:** existing hosts, Hook configuration, and Session files keep
  working when no structured goal is supplied.
- **Efficiency:** direct answers do not require contract construction or an
  evidence workflow.
- **Portability:** contracts are JSON-safe and Provider-neutral.

## Consequences

### Positive

- Hosts can distinguish attempted action, observed evidence, and verified
  completion.
- Completion becomes inspectable at criterion granularity.
- Deterministic project checks compose with a domain-neutral kernel.
- Strong models retain freedom to choose the lightest reliable method.
- Legacy conversational use remains lightweight.

### Negative

- A host that wants evidence-aware completion must define acceptance criteria
  and connect their verification sources.
- Unverified goals will intentionally end as uncertain more often.
- Derived task state and public DTOs become larger.
- Tool receipts identify invocation facts but intentionally omit diagnostic
  content, so detailed debugging still uses existing live events and model
  context.

### Neutral

- This contract does not make Noval a workflow engine.
- It does not establish universal correctness or infer user intent.
- It does not replace permissions, Hooks, sandboxing, or the semantic judge.

## Failure modes and mitigations

| Failure mode | Mitigation |
|---|---|
| A successful tool call is treated as proof | Receipts never pass criteria without a verification result |
| Stale evidence is reused | Evaluate criterion maximum age at report time |
| Evidence is attached to another goal | Reject goal or criterion mismatches |
| A source spoofs a required validator | Match the result source to the criterion source contract |
| Verification text leaks credentials | Redact bounded free text before persistence |
| A goal id is silently redefined | Require contract compatibility for an active id |
| A semantic judge claims completion | Deterministic evidence has exclusive authority for explicit goals |
| A legacy sidecar cannot express evidence | Restore semantic fields only and leave evidence absent |
| A task-sidecar record is corrupt | Skip it and recover from the latest valid derived snapshot |

## Alternatives considered

### Enrich the semantic judge only

Rejected. It cannot observe hidden execution or fresh external state.

### Automatically infer criteria and targets from tool arguments

Rejected. It would make the executor guess intent, risk retaining sensitive
values, and encode a hidden workflow.

### Require a goal contract for every turn

Rejected. It would add ceremony to direct answers and violate the
minimal-sufficient-method principle.

## References

- [Detailed design](../plans/2026-07-20-goal-evidence-completion-design.md)
- [ADR-0004](0004-principle-guided-thin-harness.md)
- [Noval Philosophy](../../PHILOSOPHY.md)
- [Canonical design](../../DESIGN.md)
