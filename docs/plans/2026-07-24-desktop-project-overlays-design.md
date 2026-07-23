# Desktop Project Overlays

## Scope

Bring the two project overlays into the current Noval Desktop visual system:
the project action menu and the remove-project confirmation. This is a
Renderer-only change. It does not change project persistence, Session storage,
or the Electron-to-Python protocol.

## Interaction

- The project ellipsis opens a compact anchored menu containing only actions
  that Noval currently supports.
- The menu closes on outside click, Escape, or after an action is chosen.
- Remove opens an in-app confirmation dialog instead of a Windows native
  confirmation box.
- The dialog states the exact boundary: only the sidebar entry is removed;
  files and Sessions on disk remain untouched.
- Cancel is focused by default. Escape, the close button, and the scrim cancel
  the operation. Controls are disabled while removal is in progress.

## Visual system

Both overlays use the existing light/dark semantic tokens, restrained borders,
soft shadows, and compact spacing. The destructive button uses the existing
danger tokens without making the entire menu visually alarming.

## Verification

Renderer tests cover opening, cancelling, and confirming removal without
calling the native confirmation API. Electron E2E verifies the real menu and
dialog, including that cancellation leaves the persisted project visible.
