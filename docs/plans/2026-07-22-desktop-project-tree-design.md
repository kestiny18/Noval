# Noval Desktop Project Tree UI Design

## Goal

Replace the preview's gated dark welcome flow with one calm, light, persistent
workspace: a project and Session tree on the left, and the active conversation
on the right.

## Product model

- A project is a local workspace directory registered in Electron preferences.
- Projects are Desktop navigation state, not Noval Core configuration.
- Sessions remain Core-owned and keep their original workdir and permission
  state.
- The Sidecar may list persisted Sessions for a project without changing the
  active workspace. Selecting a project changes only where new Sessions are
  created.
- Removing a project removes it from Desktop navigation only. It never deletes
  files or Sessions.

## Interaction

- The shell is always visible, including when no project has been added.
- The Projects heading exposes add and sort actions.
- Project hover exposes project actions and new Session.
- Each expanded project shows five Sessions initially and reveals five more on
  request.
- No central create button or separate welcome page exists.
- Settings remains at the lower-left edge. The product does not expose a
  diagnostic-export action.
- Conversation, permissions, evidence, recovery, and Provider settings keep
  their existing runtime contracts.

## Visual system

The direction is refined utilitarian: a pale blue-gray navigation rail, a warm
white work surface, graphite type, thin neutral borders, restrained shadows,
and compact hover controls. Manrope remains the interface typeface. Keyboard
focus is explicit, reduced-motion preferences are honored, and status is never
communicated by color alone.

## Versioning

`desktop/release-version.json` stores the explicitly chosen stable release
prefix such as `26.722`. Ordinary commits change only the Git commit-count
suffix. A future stable publishing decision updates the prefix to that release
date, for example `26.728`.
