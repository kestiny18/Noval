# Noval Desktop Preview Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task.

**Goal:** Deliver an unsigned Windows x64 Noval Desktop preview that consumes the public Python Application API through an embedded sidecar.

**Architecture:** Electron Main owns the product shell and sidecar lifecycle, Preload exposes narrow typed IPC, React renders safe state, and a versioned JSONL sidecar adapts commands to `NovalRuntime`. Core remains free of Electron and transport concerns.

**Tech Stack:** Electron, TypeScript, React, Vite, Zod, Vitest, Testing Library, Playwright, Python, pytest, PyInstaller, electron-builder.

---

### Task 1: Record architecture and repository boundaries

**Files:** ADR-0007, ADR index, canonical design, desktop design and this plan.

1. Add the accepted decision and detailed design.
2. Link ADR-0007 from the index and `DESIGN.md`.
3. Run documentation and diff checks.
4. Commit the architecture independently.

### Task 2: Build the Python sidecar protocol

**Files:** `desktop/sidecar/noval_sidecar/`, `desktop/sidecar/tests/`.

1. Write protocol parser, envelope, size-limit, and safe-error tests.
2. Implement stdout-safe JSONL transport and hello/version commands.
3. Add Runtime, workspace, Session, transcript, permission, cancellation, and
   turn adapters through public Application API values only.
4. Add concurrent permission-response and turn-event tests with a fake client.
5. Run sidecar and existing Application API tests, then commit.

### Task 3: Scaffold the secure Electron host

**Files:** `desktop/package.json`, configs, `src/main/`, `src/preload/`, `src/shared/`.

1. Add deterministic Desktop version generation and workspace scripts.
2. Define Zod protocol schemas and compatibility tests.
3. Implement the sidecar supervisor with handshake, request correlation,
   bounded restart, stderr diagnostics, and graceful shutdown.
4. Add platform-default Desktop preferences and DPAPI-backed credential store.
5. Add hardened BrowserWindow, navigation policy, CSP, and narrow preload API.
6. Run TypeScript, schema, and Main tests, then commit.

### Task 4: Implement the professional minimum UI

**Files:** `desktop/src/renderer/`, Renderer tests and Playwright flows.

1. Implement workspace chooser and recent workspace state.
2. Implement Session list, create, resume, rename, and transcript pagination.
3. Implement conversation rendering, visible streaming, composer, and cancel.
4. Implement tool lifecycle cards and permission approval surfaces.
5. Implement persisted `ASK`/tool grants/`FULL_ACCESS` controls.
6. Implement Provider profiles, masked credentials, settings, evidence, errors,
   empty/loading/recovery states, keyboard navigation, and focus treatment.
7. Visually inspect light/dark themes and critical flows, then commit.

### Task 5: Bundle and validate the Windows preview

**Files:** PyInstaller spec/build script, electron-builder config, CI, docs.

1. Build a directory-style embedded sidecar and prove it runs with system
   Python unavailable from `PATH`.
2. Generate the Windows icon and package the Electron application.
3. Produce `Noval-Desktop-YY.MDD.commit-count-win-x64.exe` unsigned installer.
4. Run pytest, Context Eval, Task Eval, Desktop tests, Playwright smoke,
   packaged-app smoke, sensitive-content scan, and `git diff --check`.
5. Inspect actual diff and installer checksums, then commit.
6. Open a pull request if authorized, but do not merge until the user installs
   and accepts the preview.
