# ADR-0007: Build the First-party Desktop as an Isolated Host

## Status

Accepted for implementation on 2026-07-21. The encrypted Provider-credential
ownership clause is superseded by ADR-0010; the remaining Desktop process and
security boundary stays accepted.

## Context

Noval's Application API and safe observation boundary are ready for a
first-party desktop consumer. The desktop must provide a professional Windows
experience without turning Electron into a second execution kernel or adding
desktop-specific branches to the Python agent loop.

## Decision

The repository will contain an isolated `desktop/` product implemented with
Electron, TypeScript, React, and an embedded Python sidecar.

- Renderer owns display and user interaction only.
- Preload exposes a narrow, typed IPC capability surface.
- Electron Main owns windows, local desktop preferences, encrypted credentials,
  IPC authorization, and sidecar lifecycle.
- The embedded sidecar is the only process that instantiates `NovalRuntime`.
- The sidecar consumes only the public Application API and exchanges versioned
  JSON Lines envelopes over stdio.
- Production builds never discover or fall back to a system Python runtime.
- Noval Core retains ownership of tools, permissions, Sessions, Hooks,
  confinement, sandboxing, redaction, verification, and completion.
- Only visible text and safe lifecycle events cross the observation boundary;
  opaque Provider reasoning never does.

Desktop preferences use Electron's platform `userData` directory. Core data
continues to use `~/.noval`. Electron never reads Core configuration or Session
files directly: the sidecar projects effective configuration, persisted
projects, and Sessions through the public Application API. Desktop preferences
retain presentation state, the stable explicit-project order, hidden projects,
and optional encrypted Provider overrides. Chromium session data remains in the
platform-local Electron location.

The initial product is a professional minimum loop: combine the stable
Desktop-local project list with projects discovered from Core Session storage
while selecting one active workspace at a time, create a Session lazily on the
first submitted message, resume existing Sessions, stream visible output,
observe tool lifecycle,
approve actions, cancel a turn, recover from sidecar failure, select Provider
and model, and inspect completion evidence. It is not an IDE and does not add a
terminal, editor, Git UI, Eval UI, multi-window support, cloud sync, or agent
orchestration.

## Permission semantics

The UI exposes allow once, allow this tool for the Session, deny, and the
existing Session-scoped `FULL_ACCESS` mode. Permission state is restored exactly
as persisted by Core. Desktop never silently upgrades or resets it.
`FULL_ACCESS` skips prompts only; it does not expand intent or bypass
confinement, sandboxing, Hooks, redaction, or validation.

## Distribution and privacy

The first release is an unsigned Windows x64 preview. It uses an embedded,
directory-style Python sidecar and has no remote telemetry, crash upload, or
diagnostic-export surface. Signing and automatic updates are later
release-layer decisions.

Desktop versions use `release_prefix.git_commit_count`, for example
`26.722.108`. The release prefix uses `YY.(month * 100 + day)` but is updated
explicitly only when a new stable preview is published; ordinary builds retain
the current prefix while the commit-count suffix grows. The App ID is
`io.github.kestiny18.noval.desktop` and the product name is `Noval`.

## Consequences

The Core stays independently installable and testable, while the desktop can
evolve at product speed. The cost is a cross-language protocol, a bundled
runtime, and explicit compatibility testing. Those costs are preferable to
embedding Node concerns in Core or relying on a user's Python environment.

## Alternatives considered

- A separate repository was rejected because synchronized protocol and release
  changes would add avoidable coordination cost at this stage.
- An in-process Python bridge was rejected because it weakens isolation and
  complicates packaging and crash recovery.
- Local HTTP was rejected for the preview because it introduces port,
  authentication, and listener lifecycle concerns without product value.
- System Python fallback was rejected because it makes dependency and runtime
  behavior non-reproducible.
