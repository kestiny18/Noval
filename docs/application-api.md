# Application API

[简体中文](application-api.zh-CN.md) · [ADR-0005](adr/0005-goal-evidence-completion-contract.md) · [ADR-0006](adr/0006-desktop-consumer-observation-boundary.md)

Noval's Application API keeps operational termination separate from task
completion:

- `TurnResult.stop_reason` describes why the agent loop stopped;
- `TurnResult.status` describes the public turn/task status; and
- `TurnResult.completion` contains criterion-level evidence for an explicit
  goal.

This distinction is intentional. A model can finish speaking while required
evidence is still missing, and a Provider can fail after a goal was already
verified.

## Desktop-consumer observation

The same headless API provides the primitives needed by a desktop or terminal
host without exposing Agent internals:

```python
from queue import SimpleQueue
from noval import NovalRuntime, SessionOptions, TurnRequest

events = SimpleQueue()
with NovalRuntime.from_settings(event_sink=events.put) as runtime:
    with runtime.create_session(SessionOptions(workdir="project")) as session:
        history = session.transcript(limit=100)
        session.rename("Architecture review")

        # Run on the host's worker thread. The event sink must stay fast.
        result = session.run_turn(TurnRequest("Review this project."))

        replay = session.replay_events(after_sequence=0, limit=100)
        if replay.gap_detected:
            history = session.transcript(limit=100)
```

`transcript()` returns stable one-based sequence numbers and cursor pagination.
It omits system messages, Provider replay state, provenance, and tool argument
values. Tool calls expose only argument keys; stored tool results have already
crossed the executor's normalization, truncation, and redaction boundary.

`rename()` is idle-only and stores a title in mutable Session metadata. It never
rewrites canonical JSONL. `replay_events()` reads a per-open-Session memory
window. `gap_detected=True` means older events were evicted, so durable UI state
must be rebuilt from the transcript before consuming later events.

Clients may optionally stream visible assistant text. Hosts observe
`model.started`, zero or more `model.output.delta` events, then
`model.completed`; a failed partial stream emits `model.output.aborted` and is
not written to canonical history. A legacy client that implements only
`complete()` remains compatible and simply emits no deltas.

Provider thinking/reasoning blocks are opaque replay state. They are not
displayed, logged, judged, included in transcript pages, or emitted as events.
Reasoning token counts may appear in completed metrics. Events are
live-process-only and are not restored after close or process restart.

## Define an explicit goal

```python
from noval import AcceptanceCriterion, GoalContract, TurnRequest

goal = GoalContract(
    goal_id="release-0.12.0",
    objective="Publish v0.12.0 after all required checks pass.",
    scope=("current repository", "release metadata"),
    authority=("deliver through a pull request",),
    acceptance_criteria=(
        AcceptanceCriterion(
            criterion_id="ci",
            description="Required CI checks pass.",
            verification_source="host:github-checks",
            max_age_seconds=3600,
        ),
        AcceptanceCriterion(
            criterion_id="project-tests",
            description="The configured project test Hook passes.",
            verification_source="hook:test-suite",
        ),
    ),
)

result = session.run_turn(TurnRequest(
    text="Prepare the release.",
    client_request_id="release-action-42",
    goal=goal,
))
```

The contract is observed host data. `scope` and `authority` help preserve user
intent but do not grant tool permission, widen confinement, bypass Hooks, or
change sandbox policy.

Submitting the same `goal_id` with the same content is idempotent. Reusing an
active id with different content returns a failed turn with
`goal_contract_error`. A different id replaces the active goal and starts with
no inherited receipts or verification.

## Contract values

| Value | Meaning |
|---|---|
| `AcceptanceCriterion` | A named condition, optional required source, and optional maximum evidence age |
| `ActionReceipt` | Safe facts about one tool attempt: call/tool ids, risk, outcome, timestamps, argument keys, and redacted-result digest |
| `VerificationResult` | A trusted source's passed/failed/unknown observation for one goal criterion |
| `CriterionReport` | The current passed/failed/missing/stale/unknown state of one criterion |
| `CompletionReport` | The derived status of the complete explicit goal plus a separate semantic assessment |

Receipts never contain argument values or raw tool output. A receipt may be
referenced by a verification result, but it cannot pass a criterion by itself.

## Record host verification

`record_verification()` is available only while the Session is idle. It uses
the same concurrency boundary as permission mutations.

```python
from datetime import datetime, timezone
from noval import EvidenceOutcome, VerificationResult

report = session.record_verification(VerificationResult(
    verification_id="github-checks-run-42",
    goal_id="release-0.12.0",
    criterion_id="ci",
    source="host:github-checks",
    outcome=EvidenceOutcome.PASSED,
    observed_at=datetime.now(timezone.utc).isoformat(),
    subject="pull request checks",
))

current = session.completion_report()
```

Noval rejects cross-goal, unknown-criterion, wrong-source, unknown-receipt, and
far-future verification. Free-text subject and summary fields are redacted
before task-sidecar persistence. Runtime events expose only bounded verification
metadata, not those free-text fields.

## Completion precedence

For an explicit goal, the latest matching result for each criterion is
evaluated at report time:

1. any current failure makes the goal `incomplete`;
2. any missing, stale, or unknown result makes it `uncertain`; and
3. only current passing results for every criterion make it `completed`.

The semantic judge is returned under `completion.semantic`. It assesses the
visible reply only and cannot upgrade or override contracted evidence. Without
an explicit goal, Noval preserves the legacy lightweight semantic-ledger
behavior.

An operational `error` always makes the turn `failed`, even when the independent
completion report says the goal was already complete. For other stop reasons,
the explicit completion status becomes the public status while `stop_reason`
remains independently inspectable.

## Hooks as verification

An acceptance criterion may require `verification_source="hook:<hook-id>"`.
Only a matching Stop Hook produces evidence:

| Stop Hook outcome | Verification outcome |
|---|---|
| `allow` | `passed` |
| `deny` | `failed` |
| `context` | `unknown` |

PreToolUse and PostToolUse Hooks cannot satisfy completion criteria. See
[Hooks and completion evidence](hooks.md).

## Persistence, events, and compatibility

- Canonical Session JSONL remains schema v2 and the only conversation truth.
- Goal/evidence snapshots use recoverable task-sidecar schema v2; schema-v1
  semantic snapshots remain readable and do not gain fabricated evidence.
- `TurnRequest.goal`, `TurnResult.receipts`, and `TurnResult.completion` are
  additive optional API-schema-v1 fields. Existing callers remain valid.
- `turn.started` includes `goal_id`; `tool.completed` includes a safe receipt;
  `turn.completed`/`turn.failed` include receipts and completion; idle host
  verification emits `verification.recorded`.
- A corrupt task-sidecar tail is skipped without rewriting canonical Session
  history.
