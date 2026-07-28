# Provider and Model Configuration Phase 1 Design

## Status

Approved for implementation. Product-surface refinement approved on
2026-07-28.

This design supersedes the Provider portion of
`2026-07-24-desktop-settings-design.md`. ADR-0010 is the normative decision
record. This document supplies the implementation-level contract.

## Goal

Phase 1 replaces Noval's flat single-Provider configuration with a deterministic
model-configuration system:

- Noval ships one trusted DeepSeek OpenAI-compatible Provider Profile.
- The Runtime contract remains capable of representing multiple Connections
  and Configured Models without exposing that machinery in the Phase 1
  Desktop.
- A persistent Session selects one Configured Model for its next Turn.
- A selection made during an active Turn applies only to the following Turn.
- Every Turn captures immutable agent and completion-judge bindings.
- Runtime configuration updates do not restart the Sidecar or mutate an active
  Turn.
- Phase 1 may persist API keys in the user-local Runtime settings file, but no
  secret may enter public DTOs, Session state, events, logs, traces, journals,
  diagnostics, or generated representations.

Phase 1 deliberately proves one protocol and one real Provider across multiple
models and immutable Turn bindings. It does not claim that API-shape
compatibility alone proves another Provider, and it does not expose Anthropic
configuration or cross-Adapter Session switching.

## Phase boundary

### Included

- one built-in DeepSeek OpenAI-compatible Provider Profile;
- generic Runtime, Application API, and CLI support for multiple Connections
  and Configured Models;
- write-only API-key configuration;
- immutable configuration snapshots;
- Session model selection and restoration;
- immutable per-Turn agent and judge bindings;
- Connection transport reuse;
- same-Adapter replay isolation;
- Application API v2, Desktop Sidecar protocol v2, Session schema v3, and
  settings schema v2;
- a focused Desktop Models setting for the shipped Provider and write-only API
  key;
- Session-scoped model and permission controls in the conversation composer;
- persistent Desktop language and sidebar-width preferences;
- CLI list and Session-selection operations.

### Deferred

- Anthropic Provider profiles and Custom Anthropic Connections;
- additional built-in OpenAI-compatible Provider Profiles until each passes the
  real Adapter contract;
- Custom Connections and advanced Connection/Configured Model CRUD in the
  first-party Desktop;
- an Adapter selector in Desktop;
- cross-Adapter Session switching and canonical-block representability;
- Anthropic thinking/redacted-thinking routing changes;
- connection probes and remote model catalogs;
- capability promises for streaming, tools, vision, thinking, or structured
  output;
- user-authored reusable Provider profiles;
- dynamic Adapter registration;
- automatic fallback to another Provider, Connection, model, or protocol;
- settings or Session migration;
- Electron `safeStorage`, an OS keyring, or Host credential resolvers;
- cross-process settings watching and notification;
- the OpenAI Responses Adapter.

The existing Anthropic Adapter and its regression tests remain in the
repository. Phase 1 does not delete or expose it through the new configuration
product.

## Terms and ownership

### Provider profile

A trusted Runtime-owned template for a known service:

```text
profile id
display label
Adapter
base URL
credential environment variable
selectable models
default model
hidden judge model
```

Profiles contain no credentials and are immutable packaged data.

### Connection

A user-configured endpoint and credential source. A built-in Connection copies
the trusted transport fields from its Profile and may not override them. A
Custom Connection supplies its own HTTPS endpoint and uses the fixed
`openai-compatible` Adapter in Phase 1.

### Configured Model

A stable, user-selectable id that references a Connection and one Provider
model name.

### Model binding

The safe immutable transport identity captured for one Turn:

```text
configured model id
connection id
profile id, when built-in
Adapter
normalized base URL
Provider model name
transport revision
credential source kind
```

The resolved credential is held separately in a redacted in-memory secret
container. It is never serialized or included in `repr`.

### Turn execution

A Turn-local object containing:

```text
configuration snapshot
agent model binding
judge model binding
agent client
judge client
```

It is created before Turn admission becomes visible and remains unchanged
through all tool-loop steps, compaction requests, retries, and completion
judging.

### Ownership

| Concern | Owner |
|---|---|
| Profiles and supported configuration Adapters | Runtime |
| Configuration validation and persistence | Runtime configuration store |
| Stored credential resolution | Runtime configuration store |
| Selected Configured Model | `AgentSession` |
| Active Turn bindings | `TurnExecution` |
| SDK transport pool | `NovalRuntime` |
| Provider request encoding | `LLMClient` Adapter |
| Settings forms and presentation | Desktop |
| Appearance preferences | Desktop only |

## Built-in profiles

Phase 1 packages exactly one OpenAI-compatible Profile:

| Profile id | Base URL | Environment | Selectable models | Default | Hidden judge |
|---|---|---|---|---|---|
| `deepseek` | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` | `deepseek-v4-pro`, `deepseek-v4-flash` | `deepseek-v4-pro` | `deepseek-v4-flash` |

The catalog is a release input, not an evergreen promise. Every shipped Profile
must pass Noval's non-streaming, streaming, and tool-call Adapter contract suite
with a maintainer-provided credential. A Profile that cannot pass is removed
before release. Phase 1 intentionally fixes the shipped Profile count at one so
the product contract matches the real evidence available for this release.

Qwen, Moonshot, Zhipu, OpenAI, Google, MiniMax, or another compatible endpoint
may be added only after its exact models pass the same suite. Similar wire shape
is not evidence of equivalent Provider behavior. Anthropic-format endpoints are
deferred.

Changing a built-in endpoint is a Noval release because it changes a trusted
credential destination.

### Public Profile projection

Built-in Profile DTOs omit editable transport fields:

```json
{
  "schema_version": 2,
  "id": "deepseek",
  "label": "DeepSeek",
  "kind": "builtin",
  "models": [
    {
      "id": "deepseek-v4-pro",
      "label": "DeepSeek V4 Pro",
      "recommended": true
    }
  ],
  "default_model": "deepseek-v4-pro"
}
```

The synthetic Custom Profile communicates that the Adapter is fixed:

```json
{
  "schema_version": 2,
  "id": "custom",
  "label": "Custom",
  "kind": "custom",
  "adapter": "openai-compatible",
  "requires_base_url": true
}
```

## Settings schema v2

`~/.noval/settings.json` remains the stable global-preference file:

```json
{
  "schema_version": 2,
  "models": {
    "connections": [
      {
        "id": "connection-deepseek-default",
        "revision": 1,
        "label": "DeepSeek",
        "profile_id": "deepseek",
        "adapter": "openai-compatible",
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "api_key_env": "DEEPSEEK_API_KEY"
      }
    ],
    "configured": [
      {
        "id": "model-deepseek-v4-pro-default",
        "label": "DeepSeek V4 Pro",
        "connection_id": "connection-deepseek-default",
        "model": "deepseek-v4-pro"
      }
    ],
    "default_model_id": "model-deepseek-v4-pro-default"
  },
  "max_steps": 40,
  "max_tool_output_chars": 8000,
  "persist_sessions": true
}
```

Runtime uses UUID4 for user-created ids. Packaged defaults use documented
reserved ids. Ids are immutable, opaque, and contain no Provider, model,
account, or credential data.

`Connection.revision` changes whenever its Adapter, endpoint, credential,
environment binding, or transport-affecting options change. It is safe metadata,
not a credential fingerprint.

### Hard transition

- no settings file: load packaged schema-v2 defaults;
- `schema_version: 2`: validate and load;
- missing, old, or unsupported schema: reject before creating a Provider client;
- removed flat Provider keys: reject with the exact settings path and recovery
  instructions;
- reads never migrate, rewrite, reinterpret, or delete the old file.

Noval is an internal pre-release product, so this is an intentional one-step
break. Desktop detects `unsupported_settings_schema`, stops automatic Sidecar
restart, and offers to open the configuration directory. A future reset action
must first rename the old file to a backup and require explicit user
confirmation.

### Validation invariants

Runtime rejects a candidate unless:

- every id and label is non-empty and bounded;
- ids are unique in their namespace;
- the default id references an existing Configured Model;
- every Configured Model references an existing Connection;
- a built-in Connection exactly matches the packaged Adapter, endpoint, and
  environment binding;
- a built-in Configured Model is declared by its Profile;
- a Custom Connection uses `openai-compatible`;
- a Custom base URL is absolute HTTPS, or HTTP only for a loopback host;
- a base URL has no username, password, fragment, or query;
- stored API keys are omitted or non-empty after trimming;
- environment-variable names are omitted or valid portable identifiers;
- a Connection has a stored key or environment binding before a Turn may use
  it;
- unrelated settings retain their existing type and range validation.

Configuration may remain queryable when a credential is unavailable. The
failure occurs when Runtime resolves a Turn binding.

## Credential contract

Phase 1 resolves a credential in this order:

```text
non-empty Connection.api_key
→ populated Connection.api_key_env
→ credential_unavailable
```

This is an explicit Phase 1 trade-off:

- the API key is stored as plaintext in the user-local settings file;
- Desktop must describe it as stored locally, not encrypted or "securely
  stored";
- the settings directory relies on the owning user's profile permissions;
- POSIX files are created with owner-only mode;
- temporary files use the same protection and are removed by atomic replace;
- no automatic secret-bearing backup is created.

Secrets are prohibited from:

- configuration query responses;
- object `repr` and exception messages;
- Session JSONL, metadata, checkpoints, and task sidecars;
- events and public errors;
- logs, traces, usage records, and request journals;
- diagnostics and test snapshots;
- Desktop preferences and Renderer responses.

Credential mutation uses write-only patch semantics:

```text
api_key omitted       → preserve
api_key non-empty     → replace and increment Connection revision
clear_api_key: true   → remove and increment Connection revision
empty api_key string  → invalid request
```

`api_key` and `clear_api_key` are mutually exclusive. Sensitive request DTOs use
redacted `repr`; protocol and Runtime logging record only argument keys.

Environment-backed credentials use a Runtime-local credential epoch. This
prevents persisted opaque replay state from being sent after a process restart
under an environment value that may now represent another account.

## Configuration store and concurrency

`ModelConfigurationStore` owns:

- immutable validated snapshots;
- the settings path;
- the in-process mutation lock;
- a short-lived cross-process writer lease;
- atomic persistence;
- Connection revision changes;
- redacted change notifications.

Mutation is one transaction:

```text
acquire in-process mutation lock
→ acquire settings writer lease
→ reload and validate latest on-disk settings
→ derive the complete candidate
→ validate the complete candidate
→ write a protected temporary file
→ flush and fsync
→ atomically replace settings.json
→ swap the immutable in-memory snapshot
→ release locks
→ publish a redacted configuration-changed event
```

A validation or write failure leaves both file and memory unchanged. A process
crash after file replacement but before the in-memory swap is safe because the
process is ending and the next Runtime loads the new complete file.

There is no Phase 1 file watcher. Another live Runtime observes an external
update only after explicit reload or restart.

## Application API v2

Phase 1 performs an intentional internal contract break. API v1 DTOs containing
flat Provider/model fields are not accepted as v2.

Required operations:

```python
runtime.list_provider_profiles()
runtime.get_model_configuration()
runtime.upsert_connection(request)
runtime.delete_connection(connection_id)
runtime.upsert_configured_model(request)
runtime.delete_configured_model(configured_model_id)
runtime.set_default_model(configured_model_id)
runtime.reload_model_configuration()
session.select_model(configured_model_id)
```

### Session DTO changes

`SessionOptions` removes:

```text
provider
model
judge_model
```

and adds:

```text
configured_model_id: optional
```

`SessionInfo` removes ambiguous `provider` and `model` fields and adds:

```text
selected_configured_model_id
active_turn_configured_model_id: optional
```

The selected id controls the next Turn. The active id is live observation only.
Actual completed request identity remains in assistant-message provenance and
the safe request journal.

### Deletion

- deleting a Connection referenced by a Configured Model fails;
- deleting the global default Configured Model fails;
- open and stored Session references are weak and are not scanned or rewritten;
- an active Turn retains its captured binding;
- a later Turn using a deleted id fails `model_configuration_missing`;
- the Session remains open so the user can select a replacement.

## Session schema v3

Only schema-v3 Sessions participate in discovery and restoration. Explicit
attempts to open v1 or v2 fail `unsupported_session_schema`; files remain
untouched.

The append-only JSONL header identifies the format and immutable creation facts:

```json
{
  "_meta": {
    "schema_version": 3,
    "session_id": "01...",
    "created_at": "...",
    "workdir": "..."
  }
}
```

The current selection is mutable operational state stored in the existing
metadata sidecar:

```json
{
  "application": {
    "schema_version": 2,
    "selected_configured_model_id": "model-deepseek-v4-pro-default",
    "revision": 1
  }
}
```

Canonical JSONL remains conversation truth. Model selection, title, and
permission state remain mutable Session metadata.

Selection order is:

```text
capture a validated configuration snapshot
→ acquire Session state lock
→ verify the Session is open
→ validate the selected id against the captured snapshot
→ atomically persist Session metadata
→ update the in-memory selection
→ create the event
→ release the lock
→ dispatch the event
```

Persistence failure does not change memory or emit success. Missing or corrupt
v3 application metadata fails explicitly and never falls back to the global
default.

## Turn binding and lock order

Turn startup captures the Runtime lifecycle state, configuration snapshot,
selected id, and both bindings before registering the active Turn.

The global lock order is:

```text
Runtime lifecycle lock
→ configuration snapshot lock
→ Session state lock
```

Configuration mutation never acquires a Session lock. Session selection does
not acquire the Runtime mutation lock; it validates against one immutable
snapshot.

`start_turn()` and `select_model()` linearize through the Session state lock:

```text
select wins → the new id is captured by the next Turn
start wins  → the active Turn retains the previous binding
```

`select_model()` remains allowed during an active Turn. It returns both selected
and active ids plus `effective_from`.

## Judge binding

Built-in Profiles choose a hidden judge model on the same Connection,
credential, endpoint, Adapter, and transport revision as the selected primary
model. Custom Connections use the selected primary model as judge.

Judge failure is an explicit completion-verification failure. Runtime never
chooses another judge or upgrades the completion result.

## Client and transport lifetime

Runtime caches Provider SDK transports, not model-bound `LLMClient` wrappers.
A transport cache key contains:

```text
connection id
Adapter
normalized base URL
transport revision
client-options fingerprint
purpose: agent | completion_judge
```

Each Turn binds the Provider model name to a lightweight `LLMClient` wrapper.
Session Context is never shared.

All Provider consumers receive the Turn client explicitly:

- Agent completion;
- ContextManager compaction;
- SemanticJudge completion assessment.

Metering and request-recording wrappers are Turn- and Session-bound and are not
shared through the transport cache.

When a Connection changes, matching cache entries become retired. Active Turns
retain references. The underlying transport closes after its last reference is
released. Runtime close releases all idle transports and fails while Turns are
active.

The native `ClientFactory` extension port is invoked per Turn with a
credential-free `ClientSpec`. Runtime does not cache third-party Factory
results in Phase 1.

## Opaque replay scope

Canonical messages retain Provider-private replay state as opaque content plus
safe routing metadata:

```text
Adapter
Connection id
Provider model
transport revision
Adapter schema version
```

Opaque replay is used only when the current binding exactly matches the recorded
scope.

- foreign scope plus optional replay: omit the opaque state and encode canonical
  semantic blocks;
- foreign scope plus protocol-required replay: fail
  `provider_replay_incompatible`;
- core compares routing metadata but never inspects opaque payloads.

Phase 1 tests same-Adapter isolation across Connections, models, credential
revisions, and endpoints, using DeepSeek for the real contract and a local fake
compatible endpoint for deterministic adversarial cases. Cross-Adapter
switching remains deferred.

## Desktop

Settings navigation is:

```text
Profile
Appearance
Models
Language
```

Models contains:

1. the shipped Provider identity;
2. credential availability;
3. one write-only API-key replace/clear field.

The Phase 1 form is deliberately:

```text
Provider: DeepSeek
API key
```

There is no Connection name, endpoint, environment-variable, Configured Model,
default-model, Custom Provider, or Adapter control in Desktop. Built-in
endpoint and Adapter values are not editable. Existing stored key values never
reach the Renderer. The underlying Runtime API remains generic for CLI and
future hosts.

The conversation composer owns two Session controls:

- a model selector that displays the durable selected id after restoration,
  may change while a Turn is active, and shows `Applies next turn` while active;
- an access selector backed by the existing Session permission mode.

Both controls display product language rather than internal ids. Neither is
duplicated in Settings.

The project sidebar and conversation area are separated by a draggable,
keyboard-operable separator. Desktop clamps and persists the sidebar width.

Language is a Desktop-only choice between Simplified Chinese and English. On
the first launch, a `zh` system locale selects Chinese and every other locale
selects English. An explicit user choice persists and wins on later launches.
All first-party visible copy and accessibility labels use the selected locale;
Provider/Core error messages remain safe source text in Phase 1.

Desktop Sidecar protocol v2 mirrors Application API v2. A schema failure is a
typed startup failure, not an automatic restart loop.

## CLI

Phase 1 exposes equivalent validation through Runtime operations:

```text
noval models list
noval connections list
noval --workdir <project-path> --resume <session-id> models select <configured-model-id>
```

Selection requires an explicit open/resumed Session context. Connection and
model CRUD remain Desktop- or settings-file-driven. Global options precede the
CLI subcommand, as shown above.

## Failure codes

| Code | Meaning |
|---|---|
| `unsupported_settings_schema` | settings file is not schema v2 |
| `invalid_json` / `invalid_settings` / `legacy_settings_fields` | settings syntax, type, or removed-field validation failed |
| `unknown_profile` | built-in Profile is not packaged |
| `unsupported_adapter` | configuration selected an unavailable Adapter |
| `invalid_base_url` | Custom endpoint failed validation |
| `builtin_profile_mismatch` / `unsupported_profile_model` | built-in Profile transport or model catalog was overridden |
| `duplicate_id` / `connection_not_found` | configuration reference invariant failed |
| `credential_unavailable` | no stored or environment credential is available |
| `configured_model_not_found` | selection request references no current model |
| `connection_in_use` / `default_model_in_use` | deletion would leave a strong configuration reference |
| `model_configuration_missing` | a weak Session reference cannot resolve |
| `configured_model_adapter_mismatch` | agent and judge bindings use different Adapters |
| `provider_replay_incompatible` | required opaque state cannot be safely replayed |
| `unsupported_session_schema` | Session is not schema v3 |
| `session_state_invalid` | v3 mutable application metadata is missing or corrupt |
| `configuration_conflict` / `connection_conflict` | optimistic revision validation failed |
| `settings_busy` | settings writer lease could not be acquired |

Provider failures remain normalized as `ProviderError`. No failure changes
Adapter, Connection, or model automatically.

## Delivery order

1. Land this design, ADR-0010, and the implementation plan.
2. Add configuration types, Profiles, validation, protected atomic persistence,
   immutable snapshots, and write-only credentials.
3. Add API v2 configuration DTOs and Runtime operations.
4. Add Session v3 metadata selection and selection concurrency tests.
5. Add replay scope, transport pooling, `TurnExecution`, and explicit
   Agent/Context/Judge client injection.
6. Upgrade Sidecar protocol v2 and CLI.
7. Build the focused Desktop credential setting and Session composer controls.
8. Add persisted language and resizable-sidebar preferences.
9. Validate the vertical slice with DeepSeek plus a Custom fake
   OpenAI-compatible endpoint.
10. Run DeepSeek through the real Adapter suite and keep every other candidate
    Profile out of the shipped catalog.
11. Update canonical docs, examples, fixtures, and release notes.

Each coherent step is validated and committed separately. The public contract
flip lands through one feature branch and pull request; `main` never contains a
half-migrated contract.

## Verification

### Configuration

- missing settings produces valid schema-v2 defaults;
- old and malformed schemas fail with exact recovery information;
- Profile transport fields cannot be overridden;
- Custom HTTP is accepted only for loopback;
- concurrent mutations cannot lose updates;
- write failure leaves file and snapshot unchanged;
- cross-process writer contention fails explicitly;
- unrelated preferences survive mutation;
- stored, replaced, cleared, and environment credentials have distinct tests;
- secrets are absent from DTOs, repr, exceptions, Session files, metadata,
  events, logs, traces, usage, journals, diagnostics, and snapshots.

### Session and Turn

- only schema-v3 Sessions are discoverable and restorable;
- selection survives restart;
- missing/corrupt application metadata fails explicitly;
- selection write failure does not alter memory or emit success;
- selection during a Turn affects only the following Turn;
- start/select and close/select races have deterministic outcomes;
- deleting a selected model does not rewrite the Session;
- a missing selection reference keeps the Session repairable but blocks Turn
  start;
- agent, compaction, and judge use the same immutable Turn bindings.

### Client and replay

- transports reuse only matching Connection/revision/purpose identities;
- Session Context and wrappers are never shared;
- active Turns survive Connection updates;
- retired transports close after their last Turn;
- same-Adapter foreign opaque state is never sent;
- optional replay is omitted and required replay fails explicitly.

### Desktop, CLI, and contracts

- API v2 and Sidecar v2 reject v1 DTOs/envelopes;
- Models is the third Settings item and exposes only DeepSeek plus a write-only
  API-key control;
- Renderer never receives an existing key;
- the composer model selector restores the durable selection;
- the composer permission selector uses Session permission state;
- active selection displays `Applies next turn`;
- first launch resolves Chinese only for a `zh` system locale, while an
  explicit Chinese or English choice survives restart;
- the sidebar separator supports pointer and keyboard adjustment, clamps the
  width, and restores it after restart;
- a schema startup error stops the recovery loop;
- CLI list/select shares Runtime validation;
- JSON fixtures cover all new DTOs and events;
- Python tests, Desktop unit tests, Electron E2E, package build, compileall, and
  `git diff --check` pass.

## Acceptance criteria

Phase 1 is complete only when:

1. settings v2, Session v3, Application API v2, and Sidecar v2 are the only
   accepted current contracts;
2. a clean installation produces valid defaults;
3. the shipped catalog contains only the real-contract-verified DeepSeek
   Profile while the Runtime schema remains capable of multiple Connections
   and models;
4. Desktop exposes only DeepSeek credential configuration and does not expose
   advanced Connection or Configured Model CRUD;
5. Session selection is durable and active-Turn-safe;
6. every Turn has immutable agent and judge bindings;
7. agent, compaction, and judge use the captured Turn clients;
8. same-Adapter replay state cannot cross its safe scope;
9. no credential crosses a prohibited persistence or observation boundary;
10. configuration mutation is atomic and conflict-safe;
11. no failure silently changes Adapter, Connection, model, or judge;
12. Desktop composer and CLI expose the same Session model-selection
    semantics, while the composer permission control uses the existing Session
    permission API;
13. language and sidebar width are Desktop-owned, accessible, and persistent;
14. every shipped Profile passes the real Adapter contract suite or is removed;
15. ADRs and canonical documentation describe the delivered behavior.
