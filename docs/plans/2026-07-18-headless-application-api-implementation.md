# Headless Application API Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task.

**Goal:** Build a stable, serializable, multi-session Headless/Application API
and migrate the CLI to it without weakening Noval's existing safety boundaries.

**Architecture:** Add a host-facing Application layer above Agent and existing
core ports. Runtime state is process-scoped and immutable; all mutable execution
state is owned by isolated AgentSession instances. Different sessions may run
in parallel while each session rejects a concurrent turn.

**Tech Stack:** Python 3.10+, standard-library dataclasses/enums/threading,
pytest, existing Noval provider/session/process abstractions.

---

### Task 1: Public DTO and error contracts

**Files:**
- Create: `noval/api.py`
- Create: `tests/test_application_api.py`
- Modify: `noval/__init__.py`

1. Write failing round-trip tests for runtime/session options, requests,
   results, events, metrics, usage, and public errors.
2. Verify the tests fail because the public types do not exist.
3. Implement frozen dataclasses, enums, stable codes, unknown-field request
   rejection, additive response tolerance, and explicit JSON conversion.
4. Export the stable names from `noval` and run the focused tests.
5. Run `git diff --check` and commit `feat: add headless API contracts`.

### Task 2: Agent turn result and event seams

**Files:**
- Modify: `noval/agent.py`
- Modify: `noval/executor.py`
- Modify: `noval/hooks.py`
- Modify: `tests/test_agent.py`
- Modify: `tests/test_executor.py`

1. Add failing tests for explicit turn metrics/results, ordered lifecycle
   callbacks, and observer failure isolation.
2. Replace public reliance on `last_turn_metrics` with a structured internal
   turn outcome while retaining a temporary compatibility wrapper for tests.
3. Emit model, tool, validation, and terminal lifecycle notifications using an
   internal observer port; sanitize payloads at existing boundaries.
4. Run Agent/Executor/Hook tests and commit `refactor: expose structured agent turns`.

### Task 3: Runtime and session composition

**Files:**
- Create: `noval/application.py`
- Modify: `noval/config.py`
- Modify: `tests/test_application_api.py`

1. Add failing tests for runtime creation, persistent and ephemeral sessions,
   per-session overrides, list APIs, and context-manager cleanup.
2. Implement `NovalRuntime`, `AgentSession`, factories, and session-owned
   dependency composition by extracting logic from the current CLI.
3. Ensure no call changes cwd or process environment.
4. Run focused tests and commit `feat: add isolated runtime sessions`.

### Task 4: Concurrency, cancellation, and permissions

**Files:**
- Modify: `noval/application.py`
- Modify: `noval/permissions.py`
- Modify: `noval/process.py`
- Modify: `tests/test_application_api.py`
- Modify: `tests/test_permissions.py`

1. Add barrier-based tests for parallel independent sessions and immediate
   same-session `session_busy` rejection.
2. Add serializable permission request/decision adapters with fail-closed
   handler behavior.
3. Add cooperative per-turn cancellation and owned-process termination hooks
   without claiming that Python threads can be force-killed.
4. Test handler failures, event sink failures, cancel, close, and runtime-busy
   behavior; commit `feat: enforce session concurrency boundaries`.

### Task 5: Persistent session writer lease

**Files:**
- Modify: `noval/session.py`
- Modify: `noval/application.py`
- Modify: `tests/test_session.py`
- Modify: `tests/test_application_api.py`

1. Add tests proving a persistent session cannot be opened twice for writing
   and that close releases the lease.
2. Implement a cross-platform advisory writer lease owned for the live store
   lifetime, with clear `session_locked` errors and no destructive stale-lock
   cleanup.
3. Run session/application tests and commit `feat: lease persistent sessions`.

### Task 6: CLI host adapter migration

**Files:**
- Create: `noval/cli.py`
- Modify: `noval/agent.py`
- Modify: `noval/__main__.py`
- Modify: `pyproject.toml`
- Create: `tests/test_cli.py`

1. Add CLI tests for new session, resume, approval, slash commands, and clean
   shutdown through mocked public API objects.
2. Move parsing/formatting and terminal interaction into `cli.py`.
3. Replace direct Agent dependency assembly with `NovalRuntime` and
   `AgentSession` calls; keep a narrow compatibility import if required.
4. Prove CLI creation never calls `os.chdir()` and run all CLI/Agent tests.
5. Commit `refactor: run CLI through application API`.

### Task 7: Request provenance and inspection

**Files:**
- Create: `noval/requests.py`
- Modify: `noval/client.py`
- Modify: `noval/session.py`
- Modify: `noval/application.py`
- Create: `tests/test_requests.py`
- Modify: `tests/test_provider_architecture.py`

1. Add failing tests for request ids, canonical provenance persistence,
   reconstruction after resume, and explicit adapter rendering.
2. Implement an append-only request journal linked to session/turn/step and
   content-addressed system/tool snapshots.
3. Expose `inspect_request` without leaking credentials or moving provider wire
   interpretation outside adapters.
4. Run request, session, client, and architecture tests; commit
   `feat: reconstruct model requests`.

### Task 8: Isolation and contract acceptance suite

**Files:**
- Create: `tests/test_application_isolation.py`
- Create: `tests/fixtures/application_api_v1.json`
- Modify: `tests/test_provider_architecture.py`

1. Add concurrent two-session tests covering messages, workdirs, permissions,
   hooks, skills, MCP, usage, logs, failures, and process state.
2. Add golden JSON contract fixtures and round-trip validation.
3. Assert no Application/Agent/Session code reads provider wire keys and no
   host path constructs Agent directly.
4. Run focused tests and commit `test: enforce application isolation`.

### Task 9: Documentation and release gates

**Files:**
- Modify: `README.md`
- Modify: `DESIGN.md`
- Modify: `CHANGELOG.md`
- Create: `examples/headless-api/README.md`
- Create: `examples/headless-api/main.py`

1. Document the Python embedding API, lifecycle, concurrency, persistence,
   cancellation, events, errors, and security boundaries.
2. Add a runnable two-session example using mock clients.
3. Run `pytest -q`, Context Eval, Task Eval, `compileall`, package build and
   artifact inspection.
4. Run `git diff --check`, inspect status/diff for unrelated or sensitive
   changes, and commit `docs: document headless application API`.

### Task 10: Delivery

1. Push the feature branch and wait for CI.
2. Update the associated issue if one exists.
3. Fast-forward or merge the verified branch into `main`, push main, and wait
   for main CI.
4. Report commit sequence, verification results, residual risks, and the next
   v1.0 release-gate task.

