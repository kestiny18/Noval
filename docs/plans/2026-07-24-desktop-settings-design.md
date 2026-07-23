# Noval Desktop Settings Design

## Goal

Replace the provider modal with a focused, single-page Desktop settings
experience inspired by the supplied Codex references while exposing only
capabilities Noval currently owns.

## Information architecture

The Settings surface replaces the project shell temporarily and has three
sections:

1. **General** — effective Provider, primary model, judge model, base URL,
   securely stored API key replacement, and Desktop/Core/protocol versions.
2. **Profile** — a read-only local profile derived from the current project,
   Session, Runtime, and version state. It does not invent an account, billing,
   usage heatmap, or cloud identity.
3. **Appearance** — System, Light, and Dark themes plus Comfortable and Compact
   interface density.

The Back action restores the existing project shell without recreating or
resetting project state.

## Ownership and persistence

Provider configuration continues to represent the effective Noval Runtime
configuration. Saving it restarts the Python sidecar through the existing Main
process boundary; credentials remain encrypted with Electron `safeStorage` and
never return to the Renderer.

Theme and density are Desktop-only preferences stored in Electron's
`desktop-settings.json`. They do not enter `~/.noval/settings.json`, canonical
Sessions, the sidecar protocol, or the Noval kernel.

## Interaction and accessibility

- Settings sections use a keyboard-accessible navigation list with
  `aria-current`.
- Theme choices and density choices expose pressed state.
- Appearance changes apply immediately and persist across application restarts.
- Form controls retain explicit labels and visible focus states.
- Light and dark themes share the existing Noval semantic tokens.
- Errors remain visible inside the Settings surface instead of closing it.

## Verification

- Renderer tests cover all three sections, navigation, Runtime values,
  appearance persistence calls, document theme/density state, and Back
  behavior.
- Electron E2E covers real settings navigation, local preference persistence,
  and restoration after relaunch.
- Manual screenshots cover General and Profile in light mode and Appearance in
  dark compact mode.
