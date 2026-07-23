# Desktop incompatible Session handling

## Evidence

The supplied validation data contains one canonical schema-v1 Session and one
schema-v2 Session. Noval Core already lists the v1 file with
`compatible=false` and `session_schema_version=1`, then correctly refuses to
open it without modifying the source file.

The Desktop protocol type did not declare those two fields. The Renderer
therefore presented the incompatible item as an ordinary task and invoked
`session.resume`, turning an expected compatibility boundary into a prominent
runtime error.

## Decision

Desktop consumes the compatibility state that Core already owns:

- keep incompatible Sessions visible so historical data does not appear lost;
- render them as subdued, disabled rows with a compatibility explanation;
- prevent `session.resume` both at the button and handler boundary;
- continue to open compatible schema-v2 Sessions normally;
- never parse, migrate, rewrite, or delete canonical Session files in Electron.

Hiding incompatible Sessions would reduce noise but make preserved history look
missing. Catching the resume error would still perform a known-invalid request.
Automatic migration is outside Desktop ownership and conflicts with the current
schema-v2 safety contract.

## Verification

Renderer coverage checks that a schema-v1 row is disabled, explains the
incompatibility, never calls `resumeSession`, and raises no alert. Electron E2E
uses canonical schema-v1 and schema-v2 fixtures to verify the Core-to-Sidecar-to-
Renderer path. Existing type, unit, sidecar, and E2E suites remain green.
