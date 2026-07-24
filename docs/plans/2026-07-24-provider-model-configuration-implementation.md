# OpenAI-compatible Provider and Model Configuration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deliver Runtime-owned OpenAI-compatible provider/model configuration, immutable per-Turn bindings, strict replay isolation, Application API v2, Sidecar protocol v2, CLI support, and the corresponding Desktop experience.

**Architecture:** `ModelConfigurationStore` owns settings schema v2 and atomic configuration transactions. A Session persists only configured selection ids in its mutable metadata sidecar. At Turn admission, the Runtime resolves an immutable `TurnExecution` snapshot used by the main model, compaction, and semantic judge. Provider adapters receive an explicit replay scope and reject any opaque replay state that does not match it.

**Tech Stack:** Python 3.10+, Pydantic-free dataclasses/typed DTOs, pytest, Electron, React, TypeScript, Vitest, Playwright.

---

## Task 1: Freeze the approved design and architectural decision

**Files:**
- Add: `docs/plans/2026-07-24-provider-model-configuration-design.md`
- Add: `docs/plans/2026-07-24-provider-model-configuration-implementation.md`
- Add: `docs/adr/0010-runtime-owned-model-configuration.md`
- Modify: `docs/plans/2026-07-24-desktop-settings-design.md`
- Modify: `docs/adr/README.md`
- Modify: `docs/adr/0007-first-party-desktop-host.md`
- Modify: `docs/adr/0008-current-session-schema-discovery.md`

**Steps:**
1. Record the OpenAI-compatible-only Phase 1 boundary and explicitly defer Anthropic UI/configuration support.
2. Record settings v2, Session v3, Application API v2, and Sidecar protocol v2 as hard breaks without migration.
3. Record plaintext user-local API-key storage, the write-only Desktop boundary, and prohibited credential surfaces.
4. Record mutable Session selection metadata, immutable Turn bindings, replay scope, configuration locking, and transport ownership.
5. Run `git diff --check`.
6. Review the complete staged diff and scan it for accidental secrets.
7. Commit as `docs: finalize model configuration phase 1`.

## Task 2: Implement settings schema v2 and the configuration store

**Files:**
- Add: `noval/model_config.py`
- Add: `tests/test_model_config.py`
- Modify: `noval/config.py`
- Modify: `tests/test_config.py`

**Steps:**
1. Write failing tests for missing-file defaults, six built-in OpenAI-compatible profiles, strict schema-version validation, and rejected legacy flat settings.
2. Define typed `ProviderProfile`, `ModelDefinition`, `ModelConfiguration`, and safe public summaries.
3. Write failing tests for profile/model references, unique ids, HTTPS-or-loopback base URLs, positive timeouts, and required API keys.
4. Implement validation with stable error codes and field paths.
5. Write failing tests for atomic create/update/delete/activate operations and optimistic revision conflicts.
6. Implement `ModelConfigurationStore` using an in-process lock, a short cross-process writer lease, atomic replace, and directory permission hardening where supported.
7. Write adversarial tests proving API keys are excluded from summaries, exceptions, diagnostics, and representations.
8. Run `python -m pytest tests/test_model_config.py tests/test_config.py -q`.
9. Run `git diff --check`, inspect the staged diff, and commit as `feat(config): add model configuration store`.

## Task 3: Upgrade Session persistence to schema v3

**Files:**
- Modify: `noval/session.py`
- Modify: `tests/test_session.py`
- Modify: `tests/test_session_metadata.py`

**Steps:**
1. Write failing tests that v3 is the only discoverable schema and explicit v2 opens fail without mutation.
2. Change the canonical JSONL header to schema v3 without placing mutable provider/model selections in it.
3. Extend the mutable metadata sidecar with selected main and judge configured-model ids plus configuration revision.
4. Write failing tests for atomic metadata updates, corrupt metadata fallback behavior, and concurrent update serialization.
5. Preserve append-only canonical Session truth and checkpoint recovery behavior.
6. Run `python -m pytest tests/test_session.py tests/test_session_metadata.py tests/test_checkpoint.py -q`.
7. Run `git diff --check`, inspect the staged diff, and commit as `feat(session): persist configured model selection`.

## Task 4: Define Application API v2 configuration DTOs and mutations

**Files:**
- Modify: `noval/api.py`
- Modify: `noval/application.py`
- Modify: `noval/__init__.py`
- Add: `tests/fixtures/application_api_v2.json`
- Modify: `tests/test_application_api_contract.py`
- Modify: `tests/test_application_api.py`

**Steps:**
1. Write failing contract tests for API schema v2 and the JSON-safe configuration/profile/model/session DTO shapes.
2. Replace flat Runtime configuration fields with public configuration summaries that never include API keys.
3. Remove ambiguous `SessionInfo.provider` and `SessionInfo.model`; expose selected and active configured-model ids.
4. Add list/get/upsert/delete/activate configuration methods to `NovalRuntime`.
5. Require API keys only on mutation input and return write-only credential status.
6. Write failure tests for revision conflicts, referenced-profile deletion, invalid activation, and missing credentials.
7. Run `python -m pytest tests/test_application_api.py tests/test_application_api_contract.py -q`.
8. Run `git diff --check`, inspect the staged diff, and commit as `feat(api): expose model configuration api v2`.

## Task 5: Add replay scope to canonical provider requests

**Files:**
- Modify: `noval/messages.py`
- Modify: `noval/client.py`
- Modify: `tests/test_client.py`
- Modify: `tests/test_anthropic_client.py`

**Steps:**
1. Write failing tests for an explicit replay scope containing adapter, profile id, configured-model id, base URL identity, and configuration revision.
2. Bind OpenAI-compatible opaque replay data to the complete scope and reject every mismatch before an SDK request.
3. Keep the existing Anthropic adapter functional while limiting new configuration support to OpenAI-compatible profiles.
4. Ensure replay metadata remains adapter-owned, JSON-safe, credential-free, and absent from public DTOs.
5. Run `python -m pytest tests/test_client.py tests/test_anthropic_client.py -q`.
6. Run `git diff --check`, inspect the staged diff, and commit as `fix(provider): isolate replay state by model scope`.

## Task 6: Resolve immutable Turn execution bindings

**Files:**
- Add: `noval/turn.py`
- Modify: `noval/application.py`
- Modify: `noval/agent.py`
- Modify: `noval/context.py`
- Modify: `noval/task.py`
- Modify: `noval/client.py`
- Modify: `tests/test_application_api.py`
- Modify: `tests/test_agent.py`
- Modify: `tests/test_context.py`
- Modify: `tests/test_task.py`

**Steps:**
1. Write failing tests showing configuration changes during a Turn do not alter the main model, compaction model, judge model, or replay scope for that Turn.
2. Define immutable `TurnExecution` and safe configured-model identity values.
3. Resolve the snapshot only after same-Session admission succeeds, using the current Session selection and configuration revision.
4. Pass Turn clients explicitly through the Agent, ContextManager, and SemanticJudge call paths.
5. Cache only provider SDK transports; do not cache model-bound client wrappers.
6. Ensure the active binding changes only on the next admitted Turn.
7. Add close/admission/configuration race tests and verify no deadlocks under the documented lock order.
8. Run `python -m pytest tests/test_application_api.py tests/test_agent.py tests/test_context.py tests/test_task.py -q`.
9. Run `git diff --check`, inspect the staged diff, and commit as `feat(runtime): bind models immutably per turn`.

## Task 7: Upgrade Sidecar protocol and CLI configuration commands

**Files:**
- Modify: `desktop/src/shared/protocol.ts`
- Modify: `desktop/src/main/sidecar-client.ts`
- Modify: `desktop/tests/fixtures/fake-sidecar.mjs`
- Modify: `noval/sidecar.py`
- Modify: `noval/cli.py`
- Modify: `tests/test_sidecar.py`
- Modify: `tests/test_cli.py`
- Modify: `desktop/src/main/sidecar-client.test.ts`

**Steps:**
1. Write failing protocol tests for Sidecar protocol v2 and configuration request/response messages.
2. Implement configuration list/upsert/delete/activate operations through `NovalRuntime`; do not restart the Sidecar.
3. Remove API keys and provider-private data from all Sidecar responses and diagnostics.
4. Add CLI commands to list profiles/models, validate configuration, update credentials, and select configured models.
5. Use non-echoing credential input for interactive CLI writes and never print credential values.
6. Run `python -m pytest tests/test_sidecar.py tests/test_cli.py -q`.
7. Run `npm test -- --run src/main/sidecar-client.test.ts` from `desktop`.
8. Run `git diff --check`, inspect the staged diff, and commit as `feat(host): add model configuration protocol v2`.

## Task 8: Replace Desktop-owned credentials with Runtime configuration

**Files:**
- Modify: `desktop/src/main/preferences.ts`
- Modify: `desktop/src/main/index.ts`
- Modify: `desktop/src/preload/index.ts`
- Modify: `desktop/src/shared/protocol.ts`
- Modify: `desktop/src/shared/global.d.ts`
- Modify: `desktop/src/main/preferences.test.ts`
- Modify: `desktop/src/main/index.test.ts`
- Modify: `desktop/src/preload/index.test.ts`

**Steps:**
1. Write failing tests proving Desktop preferences contain only UI preferences and last-selected ids.
2. Remove Electron `safeStorage` credential ownership and flat Runtime-settings writes.
3. Expose a narrow IPC/preload configuration API with write-only API-key mutations.
4. Prove keys do not appear in renderer-readable results, logs, thrown errors, snapshots, or preference files.
5. Run `npm test -- --run src/main/preferences.test.ts src/main/index.test.ts src/preload/index.test.ts` from `desktop`.
6. Run `git diff --check`, inspect the staged diff, and commit as `refactor(desktop): delegate credentials to runtime`.

## Task 9: Build the Desktop Models settings experience

**Files:**
- Modify: `desktop/src/renderer/App.tsx`
- Modify: `desktop/src/renderer/styles.css`
- Modify: `desktop/src/renderer/App.test.tsx`
- Modify: `desktop/e2e/desktop.spec.ts`

**Steps:**
1. Write failing renderer tests for profile/model listing, active selection, credential status, validation errors, and revision conflicts.
2. Implement a Models settings section centered on built-in OpenAI-compatible profiles with custom compatible profiles as an advanced path.
3. Make API-key fields write-only: blank means unchanged, explicit replace/delete actions are separate, and saved values are never rehydrated.
4. Add main/judge selectors whose updates affect only the next Turn and show selected versus active ids while a Turn is running.
5. Preserve responsive layout, keyboard access, focus visibility, and reduced-motion behavior.
6. Add E2E coverage for configure, select, run, switch-during-run, and reopen flows.
7. Run `npm test -- --run src/renderer/App.test.tsx` from `desktop`.
8. Run `npm run test:e2e` from `desktop`.
9. Run `git diff --check`, inspect the staged diff, and commit as `feat(desktop): add model configuration settings`.

## Task 10: Complete adversarial validation and delivery

**Files:**
- Modify as required by failures only.

**Steps:**
1. Run `python -m pytest -q`.
2. Run `python -m ruff check .`.
3. Run `python -m mypy noval`.
4. Run `npm run typecheck`, `npm test`, `npm run build`, and `npm run test:e2e` from `desktop`.
5. Run targeted adversarial credential-persistence, replay-mismatch, schema-hard-break, and close/admission/configuration race tests again.
6. Run `git diff --check` and inspect the full branch diff against its base.
7. Scan the diff and generated artifacts for credential-like content and provider-private replay data.
8. Update related documentation and changelog only where the delivered behavior requires it.
9. Commit any validation fixes in small, scoped commits.
10. Push the branch, update the related Issue with scope/validation/remaining work, and update or open the pull request.
11. Wait for `CI gate` and `Analyze Python`, resolve all review conversations, then merge and synchronize the Issue when acceptance criteria are fully satisfied.
