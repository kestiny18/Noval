# Goal, Evidence, and Completion Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an opt-in, JSON-safe contract that ties explicit goals to safe runtime receipts, criterion-level verification, freshness, and truthful completion without imposing a workflow on the model.

**Architecture:** Extend the existing task layer with structured goal and evidence values, keep canonical Session schema v2 unchanged, generate safe receipts at the executor/agent seam, map only Stop Hooks to configured criteria, and expose derived reports through the headless Application API. Preserve current semantic judging for turns without a goal; for explicit goals, deterministic evidence alone controls completion.

**Tech Stack:** Python 3.9+, dataclasses, JSONL derived state, pytest, offline MockClient Evals, GitHub Actions.

---

## Task 1: Lock the public data contract with failing tests

**Files:**

- Modify: `tests/test_application_api.py`
- Modify: `tests/fixtures/application_api_v1.json`
- Modify: `tests/test_task.py`
- Modify: `noval/task.py`
- Modify: `noval/api.py`
- Modify: `noval/__init__.py`

**Steps:**

1. Add failing round-trip and strict-parser tests for `GoalContract`,
   `AcceptanceCriterion`, `ActionReceipt`, `VerificationResult`, criterion
   state, and `CompletionReport`.
2. Add compatibility tests proving old `TurnRequest` and `TurnResult` payloads
   remain valid and new fields are additive.
3. Implement bounded enums and dataclasses with JSON-safe `to_dict` and
   `from_dict` methods.
4. Add optional `goal` to `TurnRequest`; add optional `completion` and bounded
   per-turn `receipts` to `TurnResult`.
5. Export the public contract from `noval`.
6. Run the focused tests and `git diff --check`.
7. Commit as `feat: add goal evidence completion DTOs`.

## Task 2: Derive completion from current criterion evidence

**Files:**

- Modify: `tests/test_task.py`
- Modify: `noval/task.py`

**Steps:**

1. Add failing tests for compatible goal activation and rejection of silent
   redefinition under the same id.
2. Add failing tests for passing, failed, missing, unknown, mismatched-source,
   cross-goal, and stale verification.
3. Add a clock seam so freshness tests are deterministic.
4. Implement goal activation, bounded receipt and verification recording, and
   deterministic `CompletionReport` derivation.
5. Preserve the semantic verdict as a separate field; prove it cannot upgrade
   an explicit goal.
6. Render a bounded observed goal/status block for model context.
7. Run focused tests and `git diff --check`.
8. Commit as `feat: evaluate criterion evidence and freshness`.

## Task 3: Persist and recover schema-v2 derived task state

**Files:**

- Modify: `tests/test_task.py`
- Modify: `tests/test_application_api.py`
- Modify: `noval/task.py`
- Modify: `noval/session.py` only if a compatibility seam is required

**Steps:**

1. Add failing tests for schema-v2 goal/evidence recovery, schema-v1 semantic
   state migration, bounded history, and corrupt-tail fallback.
2. Advance only the task-sidecar schema to v2 and accept v1 snapshots.
3. Ensure persistence failure remains non-fatal and does not modify canonical
   Session JSONL.
4. Resume an Application Session and prove the latest completion report is
   reconstructed.
5. Run focused tests and `git diff --check`.
6. Commit as `feat: persist recoverable task evidence`.

## Task 4: Record safe tool receipts

**Files:**

- Modify: `tests/test_executor.py`
- Modify: `tests/test_agent.py` or `tests/test_task.py`
- Modify: `noval/executor.py`
- Modify: `noval/agent.py`

**Steps:**

1. Add failing tests for successful, failed, and non-executed tool-call
   receipts.
2. Prove receipts contain argument keys and a digest of redacted output, but no
   argument values, raw output, credentials, or opaque thinking.
3. Add timing metadata at the agent/executor seam without moving execution
   policy into tools.
4. Record receipts through `TaskController` and include only receipts created
   by the current turn in `AgentTurnOutcome`.
5. Expose receipt-safe live event payloads without removing existing event
   compatibility.
6. Run focused tests and `git diff --check`.
7. Commit as `feat: record safe tool action receipts`.

## Task 5: Compose Stop Hooks with acceptance criteria

**Files:**

- Modify: `tests/test_hooks.py`
- Modify: `tests/test_agent.py` or `tests/test_task.py`
- Modify: `noval/agent.py`
- Modify: `noval/task.py`

**Steps:**

1. Add failing tests for `hook:<id>` source mapping across allow, deny, and
   context results.
2. Prove PreToolUse and PostToolUse results cannot satisfy completion criteria.
3. Record mapped Stop Hook verification after each Stop batch, including the
   step-limit path.
4. Reference relevant current-turn receipt ids without persisting Hook output.
5. Prove missing and denied Hooks leave the explicit goal non-complete.
6. Run focused tests and `git diff --check`.
7. Commit as `feat: use stop hooks as completion evidence`.

## Task 6: Integrate the Application API terminal contract

**Files:**

- Modify: `tests/test_application_api.py`
- Modify: `noval/application.py`
- Modify: `noval/agent.py`
- Modify: `noval/api.py`

**Steps:**

1. Add failing end-to-end tests for a goal-bearing turn, uncertain missing
   evidence, completed Hook evidence, semantic non-escalation, and operational
   failure precedence.
2. Activate the request goal before the model call and inject only observed,
   bounded contract context.
3. Add idle-only `AgentSession.record_verification()` and
   `AgentSession.completion_report()` APIs.
4. Map contracted status to the public terminal status while retaining the
   independent operational `stop_reason`.
5. Include completion and receipts in turn-completed events.
6. Run focused tests and `git diff --check`.
7. Commit as `feat: expose evidence-aware completion API`.

## Task 7: Add offline behavioral and recovery Evals

**Files:**

- Modify: `evals/task/cases.jsonl`
- Modify: `evals/task/run.py`
- Modify: `tests/test_task_eval.py`
- Add or modify: `evals/recovery/` assets if recovery coverage does not fit the
  task runner

**Steps:**

1. Add cases proving model confidence cannot replace missing evidence.
2. Add cases for stale evidence, failed Hook evidence, all-pass completion, and
   legacy semantic-only behavior.
3. Add deterministic recovery coverage for schema-v1 migration and corrupt
   task-sidecar tails.
4. Keep the runner offline and Provider-independent.
5. Run the Eval suite and focused tests.
6. Commit as `test: add completion evidence evals`.

## Task 8: Publish the contract in English and Chinese

**Files:**

- Modify: `PHILOSOPHY.md`
- Modify: `PHILOSOPHY.zh-CN.md`
- Modify: `DESIGN.md`
- Modify: `DESIGN.zh-CN.md`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/application-api.md`
- Modify: `docs/application-api.zh-CN.md`
- Modify: `docs/hooks.md`
- Modify: `docs/hooks.zh-CN.md`
- Modify: `CHANGELOG.md`

**Steps:**

1. Explain goal versus plan, receipt versus verification, freshness, and
   deterministic versus semantic completion.
2. Document precedence, host APIs, JSON examples, migration, and explicit
   non-goals.
3. Update stale version references and keep English canonical with direct
   Chinese navigation.
4. Verify all local Markdown links and English-only source policy.
5. Commit as `docs: publish the completion contract`.

## Task 9: Release-quality validation and v0.12.0 decision

**Files:**

- Modify release metadata only if all acceptance criteria pass.

**Steps:**

1. Run formatting/static checks used by CI, all unit tests, all offline Evals,
   package build, artifact inspection, and `git diff --check`.
2. Inspect the entire branch diff and scan for secrets, accidental binaries,
   generated reports, and unrelated changes.
3. Re-read ADR-0005 acceptance against observed test evidence. If any contract
   property remains incomplete, stop without changing the version.
4. If complete, update version metadata and release notes to 0.12.0, rebuild,
   and rerun the full release validation.
5. Commit release preparation as `chore: prepare v0.12.0`.
6. Push the feature branch, update Issue #14 with commit and validation
   evidence, and open a pull request.
7. Wait for `CI gate` and `Analyze Python`, resolve every review conversation,
   and merge only when required checks pass.
8. Confirm `main` contains the merge, create and push annotated tag `v0.12.0`,
   create the GitHub release, and verify its public URL and assets.
9. Close Issue #14 only after every acceptance criterion is satisfied; update
   the roadmap and related Issues with remaining follow-up work.
