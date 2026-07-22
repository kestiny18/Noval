# Desktop and Core State Integration

## Goal

Make the Desktop a faithful consumer of Noval Runtime configuration and
canonical Session persistence. Clicking **New task** must not create either a
disk record or a sidebar Session until the user submits a first message.

## Chosen design

Noval Core remains the only owner of effective runtime configuration, persisted
project discovery, and Session history. The Application API adds two read-only,
JSON-safe projections:

- `RuntimeConfiguration` returns Provider, model, judge model, base URL, and a
  credential-presence boolean. It never returns a key or environment value.
- `PersistedProjectInfo` derives projects from canonical Session directories
  and returns workdir, creation time, Session count, and availability.

The Python sidecar exposes these projections through versioned protocol
methods. Electron Main merges available Core projects with folders explicitly
added in Desktop. Desktop-only preferences preserve insertion order, the active
project, and explicit removals. Selecting a project changes only its active
flag; it never moves the project in the list. The Renderer performs no sorting.

The Renderer represents **New task** as an in-memory draft bound to a project.
It calls `session.create` only while submitting the first non-empty message.
After the turn attempt it reloads that project's Session list from Core. If
Core did not persist the Session, the UI returns to the draft state and does not
invent a sidebar item. Existing Sessions and transcripts always come from Core.

## Alternatives rejected

- Letting Electron scan `~/.noval` was rejected because it duplicates storage
  semantics and couples TypeScript to the canonical Session layout.
- Persisting empty Desktop Sessions was rejected because it conflicts with the
  lazy `JsonlSessionStore` contract.
- Moving projects by recent activation or offering UI sort modes was rejected
  because it makes navigation unstable and creates a second ordering policy.

## Failure behavior

Unavailable historical workdirs remain observable through Core but are not
offered as active Desktop projects. Invalid Core settings fail through the
existing safe configuration error. A first-message failure refreshes the
canonical Session list; only a Session that Core actually persisted appears in
the tree.

## Verification

- Core contract round-trip tests cover the new safe DTOs.
- Session tests cover stable project discovery and lazy persistence.
- Sidecar tests cover safe configuration and project inventory methods.
- Renderer tests cover stable order, Core settings hydration, and no Session
  creation before first submit.
- Electron E2E seeds Core Session storage and verifies automatic project and
  Session discovery without Desktop project preferences.
