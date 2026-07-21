# ADR-0007: Build the First-party Desktop as an Isolated Host

## Status

Accepted for implementation on 2026-07-21.

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
continues to use `~/.noval`. Neither side reads the other's configuration
files. Chromium session data remains in the platform-local Electron location.

The initial product is a professional minimum loop: select one workspace,
create and resume Sessions, stream visible output, observe tool lifecycle,
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
directory-style Python sidecar and has no remote telemetry or crash upload.
Safe local logs and user-initiated diagnostic export are allowed. Signing and
automatic updates are later release-layer decisions.

Desktop versions use `YY.(month * 100 + day).git_commit_count`, for example
`26.721.87`. The App ID is `io.github.kestiny18.noval.desktop` and the product
name is `Noval`.

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
