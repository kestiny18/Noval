# Noval Desktop Settings Design

## Goal

Provide a focused Desktop settings experience inspired by the supplied Codex
references while exposing only capabilities Noval currently owns. Provider and
model configuration is superseded by
`2026-07-24-provider-model-configuration-design.md`.

## Information architecture

The Settings surface replaces the project shell temporarily and has four
sections:

1. **General** — application and Runtime information plus general behavior.
2. **Models** — connections, configured models, and write-only credential
   inputs as
   defined by the Phase 1 Provider and model configuration design.
3. **Profile** — a read-only local profile derived from the current project,
   Session, Runtime, and version state. It does not invent an account, billing,
   usage heatmap, or cloud identity.
4. **Appearance** — System, Light, and Dark themes plus Comfortable and Compact
   interface density.

The Back action restores the existing project shell without recreating or
resetting project state.

## Ownership and persistence

Model configuration is Runtime-owned and uses settings schema v2, Application
API v2, and the OpenAI-compatible Phase 1 contract defined by
`2026-07-24-provider-model-configuration-design.md`. Phase 1 stores Connection
API keys as plaintext in the user-local Runtime settings file. Existing keys
never return to the Renderer, Desktop does not describe them as encrypted, and
model configuration updates through Runtime APIs without requiring a Sidecar
restart.

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

- Renderer tests cover all four sections, navigation, Runtime values,
  appearance persistence calls, document theme/density state, and Back
  behavior.
- Electron E2E covers real settings navigation, local preference persistence,
  and restoration after relaunch.
- Manual screenshots cover General and Profile in light mode and Appearance in
  dark compact mode.
