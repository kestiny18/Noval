# Desktop Usage Analytics Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refine the composer permission controls and add safe, daily, model-filterable Token usage analytics above Settings > Models.

**Architecture:** Extend the existing Core-owned usage side channel with safe terminal-Turn duration events and a read-only Application API aggregation DTO. Relay the DTO through the additive Sidecar/Main/Preload capability, then render an all-time summary and a fixed 7×52 daily activity grid in React without exposing raw usage files.

**Tech Stack:** Python 3.10+, dataclasses, append-only JSONL, Electron IPC, TypeScript, React, CSS, Vitest, Playwright.

---

### Task 1: Fix composer permission details

**Files:**
- Modify: `desktop/src/renderer/App.tsx`
- Modify: `desktop/src/renderer/model-selector.css`
- Test: `desktop/src/renderer/App.test.tsx`
- Test: `desktop/e2e/desktop.spec.ts`

**Steps:**

1. Add a failing Renderer test proving the grant trigger is one horizontal
   button and the open grant panel closes from the trigger, Escape, and an
   outside pointer.
2. Add failing structural assertions that both permission choices use the same
   text column and that the permission popover is narrower than before.
3. Implement a dedicated grants anchor, outside-pointer/Escape dismissal, and
   horizontal trigger styling.
4. Use identical permission-menu grid columns and left-aligned copy for Request
   approval and Full access. Reduce the popover from 380 px to 340 px.
5. Make model menu labels explicitly left aligned.
6. Run `npm test -- --run src/renderer/App.test.tsx`.
7. Commit as `fix(desktop): refine permission controls`.

### Task 2: Aggregate safe usage events in Core

**Files:**
- Modify: `noval/usage.py`
- Modify: `noval/api.py`
- Modify: `noval/application.py`
- Modify: `noval/__init__.py`
- Test: `tests/test_usage.py`
- Test: `tests/test_application_api.py`

**Steps:**

1. Add failing tests for legacy token-event compatibility, typed token events,
   terminal-Turn duration events, corrupt-record tolerance, all-time totals,
   peak daily totals, per-model summaries, and exactly 364 zero-filled daily
   buckets.
2. Add immutable `UsageDailyPoint`, `UsageModelSummary`, and `UsageAnalytics`
   DTOs with strict JSON round trips.
3. Extend `JsonlUsageStore` to write typed model-usage and terminal-Turn events
   while continuing to read existing schema-v1 token events.
4. Implement bounded aggregation across all valid date directories. All-time
   summaries scan valid history; daily points cover the rolling 364-day window.
5. Record one terminal-Turn duration in `AgentSession.run_turn()` using the
   admitted Turn's Provider model identity. Catch and log accounting failures.
6. Expose `NovalRuntime.usage_analytics(days=364)` as a read-only method.
7. Run `py -m pytest tests/test_usage.py tests/test_application_api.py -q`.
8. Commit as `feat(core): expose safe usage analytics`.

### Task 3: Relay analytics through Desktop

**Files:**
- Modify: `desktop/sidecar/noval_sidecar/server.py`
- Modify: `desktop/sidecar/tests/test_protocol.py`
- Modify: `desktop/src/shared/protocol.ts`
- Modify: `desktop/src/shared/protocol.test.ts`
- Modify: `desktop/src/main/index.ts`
- Modify: `desktop/src/preload/index.ts`
- Test: `desktop/src/main/index.test.ts`

**Steps:**

1. Add failing Sidecar and TypeScript contract tests for `usage.analytics`.
2. Add the capability to `system.hello` and dispatch it through
   `NovalRuntime.usage_analytics()`.
3. Define strict TypeScript interfaces for the summary, per-model values, and
   364 daily points.
4. Add a narrow `getUsageAnalytics()` preload method and IPC handler.
5. Run Sidecar protocol tests and Desktop typecheck.
6. Commit as `feat(desktop): bridge usage analytics`.

### Task 4: Build the Settings usage view

**Files:**
- Modify: `desktop/src/renderer/App.tsx`
- Modify: `desktop/src/renderer/SettingsPage.tsx`
- Modify: `desktop/src/renderer/i18n.ts`
- Modify: `desktop/src/renderer/settings.css`
- Test: `desktop/src/renderer/App.test.tsx`
- Test: `desktop/e2e/desktop.spec.ts`

**Steps:**

1. Add a failing Settings test for loading, empty, error, all-model, and
   single-model states.
2. Load analytics when opening Settings and pass it to `SettingsPage`.
3. Add an All models / Provider model selector. Recompute all three metrics and
   daily cell values from the selected summary.
4. Render three restrained metric columns: cumulative Tokens, peak daily
   Tokens, and longest task duration. Use locale-aware compact numbers and
   duration formatting.
5. Render exactly 364 keyboard-focusable daily cells as 52 columns × 7 rows.
   Each cell exposes an accessible label and a hover/focus tooltip with date,
   model scope, and Token count. Derive five intensity levels from the selected
   52-week maximum.
6. Keep the model configuration card immediately below analytics. Add localized
   empty/loading/error copy without exposing storage details.
7. Add Electron E2E fixtures and screenshots for light and dark themes.
8. Run Renderer tests, production build, and targeted Electron E2E.
9. Commit as `feat(desktop): show token activity`.

### Task 5: Validate and deliver

**Files:**
- Update: `docs/plans/2026-07-30-desktop-usage-analytics.md` only if acceptance evidence changes the plan.

**Steps:**

1. Run `git diff --check`.
2. Run `py -m pytest -q`.
3. Run `py -m compileall -q noval tests desktop/sidecar`.
4. Run `npm test -- --run`, `npm run typecheck`, and `npm run test:e2e` in
   `desktop/`.
5. Inspect light and dark screenshots for permission alignment, grant dismissal,
   model alignment, empty usage, populated usage, tooltip, and model filtering.
6. Build `npm run package:win`, record size and SHA-256, and smoke-test launch.
7. Push `feature/desktop-preview`, update Draft PR #22, verify no unresolved
   review threads, and wait for CI gate and Analyze Python.
8. Keep the PR Draft until human installation acceptance.
