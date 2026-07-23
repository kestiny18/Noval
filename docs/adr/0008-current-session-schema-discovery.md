# ADR-0008: Discover Only the Current Canonical Session Schema

## Status

Accepted on 2026-07-24.

## Context

Noval's canonical Session format is schema v2. An earlier internal schema-v1
experiment used a different message envelope and has no external compatibility
commitment.

Core previously listed schema-v1 files as incompatible records. That state was
then projected through `SessionInfo`, the Sidecar, and Desktop, forcing every
host to carry presentation and interaction behavior for files the Runtime
could never resume.

Renumbering the current format to v1 would make old and current files share one
version identifier while using different structures. It would require content
guessing or a destructive reset and would weaken the canonical-state boundary.

## Decision

- Canonical Session schema v2 remains the current format.
- Project and Session discovery include only files whose header declares the
  current schema.
- Unsupported Session files remain untouched and are not projected through the
  Application API.
- An explicit attempt to open an unsupported Session continues to fail closed
  without migration, rewriting, or deletion.
- `SessionMeta` and `SessionInfo` no longer expose compatibility fields because
  inventories contain only resumable canonical Sessions.
- CLI and Desktop do not implement legacy compatibility presentation.

Application API schema v1, Desktop Sidecar protocol v1, checkpoint schema v2,
task-event schema v2, and request-journal schema v2 are independent contracts
and do not change.

## Consequences

Hosts consume a simpler invariant: every listed Session is supported by the
current Runtime. Projects containing only unsupported files disappear from
inventories, but their files remain available for deliberate offline inspection
or deletion.

Internal users who no longer need experimental schema-v1 data may delete it.
Noval does not add automatic migration for a format without a compatibility
commitment.

## Alternatives considered

- Listing disabled legacy items was rejected because it leaks unsupported
  storage history into every host contract.
- Renumbering canonical schema v2 to v1 was rejected because it creates a
  same-version, different-shape collision with existing experimental files.
- Automatic migration was rejected because it adds risk and permanent
  complexity for internal-only data.
