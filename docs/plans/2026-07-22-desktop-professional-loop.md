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

1. Show only the selected Session title in the conversation header. Do not show
   its workspace path or an inline rename control, and render no conversation
   header while no Session is selected.
2. Add permission grant inspection, revoke, and reset controls.
3. Show evidence-aware completion separately from the assistant answer.
4. Keep the selected Session visibly highlighted and reconstruct its transcript
   whenever it is activated or restored.
5. When a workspace is selected without a Session, show the composer directly;
   the first submitted message lazily creates the Session.
6. Place user-message time and copy actions outside the message bubble, using
   the same metadata treatment as assistant messages.
7. Present Session access as a 380 px descriptive popover above the composer:
   include a short heading, icon, title, safety description, and trailing check
   for each Runtime-supported mode. Keep `ask` neutral and use the warning
   accent only when `full_access` is selected; do not invent a third permission
   mode that Core does not support.
8. Present Session model selection as a compact 220 px list above a persistent
   neutral pill. Each row contains only the public model label and a trailing
   check for the selected model.
9. Both menus close after selection, on outside pointer input, and on Escape;
   opening a menu focuses its selected item, and Arrow/Home/End keys move
   between options.
10. Add focused interaction tests and visually inspect the light and dark
    results against the supplied HTML references.

### Task 4: Validate and deliver

1. Add Playwright Electron smoke coverage for workspace gating and recovery-safe UI state.
2. Run Python, Eval, TypeScript, Vitest, Playwright, packaging, and packaged-sidecar checks.
3. Inspect the diff and sensitive content, then commit and push the existing preview branch.
4. Update Draft PR #22 and Issue #5; do not merge before human installation acceptance.
