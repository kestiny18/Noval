# Noval Desktop Professional Loop Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task.

**Goal:** Close the remaining Desktop preview acceptance gaps without changing Noval Core semantics.

**Architecture:** Electron Main owns sidecar recovery, bounded safe diagnostics, and export. Preload keeps a narrow typed IPC surface; React restores transcript and live-event state and displays Core-owned permissions and completion evidence. The Python sidecar remains a transport adapter over the public Application API.

**Tech Stack:** Electron, TypeScript, React, Zod, Vitest, Playwright, Python, pytest, PyInstaller, electron-builder.

---

### Task 1: Extend the host contract

1. Add typed completion, event replay, permission management, runtime status, and diagnostic export methods.
2. Add protocol and sidecar tests for the exposed Application API mappings.
3. Run Desktop and sidecar tests.

### Task 2: Make the host recoverable

1. Add bounded sidecar restart with retained launch configuration.
2. Re-select the active workspace after restart and publish safe connection lifecycle events.
3. Bound and redact local diagnostics; export them only through an explicit save dialog.
4. Test restart policy and diagnostic sanitization.

### Task 3: Complete the Renderer loop

1. Add inline Session rename.
2. Add permission grant inspection, revoke, and reset controls.
3. Show evidence-aware completion separately from the assistant answer.
4. Reconstruct transcript and replay live events after reconnection.
5. Add focused interaction tests and visually inspect the result.

### Task 4: Validate and deliver

1. Add Playwright Electron smoke coverage for workspace gating and recovery-safe UI state.
2. Run Python, Eval, TypeScript, Vitest, Playwright, packaging, and packaged-sidecar checks.
3. Inspect the diff and sensitive content, then commit and push the existing preview branch.
4. Update Draft PR #22 and Issue #5; do not merge before human installation acceptance.
