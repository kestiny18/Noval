# Noval Desktop Settings Design

## Goal

Provide a focused Desktop settings experience inspired by the supplied Codex
references while exposing only capabilities Noval currently owns. Internal
model configuration is defined by
`2026-07-24-provider-model-configuration-design.md`.

## Information architecture

The Settings surface replaces the project shell temporarily and has three
sections in this order:

1. **General** — application preferences, including the Simplified Chinese and
   English language choice. It does not expose host, process, protocol, account,
   billing, or cloud-identity implementation details.
2. **Appearance** — System, Light, and Dark themes plus Comfortable and Compact
   interface density.
3. **Models** — one unified DeepSeek configuration card containing credential
   availability and a write-only API key replace/clear control. Provider,
   Connection, endpoint, environment-variable, Configured Model, default-model,
   and Adapter concepts are not exposed.

The Back action restores the existing project shell without recreating or
resetting project state.

## Ownership and persistence

Model configuration uses settings schema v2, Application API v2, and the
OpenAI-compatible Phase 1 contract defined by
`2026-07-24-provider-model-configuration-design.md`. Phase 1 stores Connection
API keys as plaintext in the user-local settings file. Existing keys never
return to the Renderer, Desktop does not describe them as encrypted, and
configuration updates apply without requiring an application restart.

Theme, density, language, and project-sidebar width are Desktop-only
preferences stored in Electron's `desktop-settings.json`. They do not enter
`~/.noval/settings.json`, canonical Sessions, the sidecar protocol, or the
Noval kernel. The first launch resolves language from Electron's system locale:
`zh` selects Simplified Chinese and every other locale selects English. Once a
user chooses a language, that explicit choice persists.

Model identity and permission mode are Session concerns. They appear in the
conversation composer and are not duplicated in Settings.

## Interaction and accessibility

- Settings sections use a keyboard-accessible navigation list with
  `aria-current`.
- Theme choices and density choices expose pressed state.
- Appearance changes apply immediately and persist across application restarts.
- Language changes apply immediately, translate first-party visible copy and
  accessible labels, and persist across restarts.
- The project sidebar separator supports pointer drag, Left/Right arrow keys,
  visible focus, clamping, and persisted restoration.
- Composer model and permission controls use application-owned anchored menus,
  remain usable by keyboard, close on Escape or outside interaction, and expose
  their current state with explicit labels.
- Selecting Full Access applies immediately and shows a non-blocking,
  time-limited toast with an Undo action. It does not open a native confirmation
  dialog. The copy states that other safety limits remain in effect.
- Form controls retain explicit labels and visible focus states.
- Light and dark themes share the existing Noval semantic tokens.
- Errors remain visible inside the Settings surface instead of closing it.

## Verification

- Renderer tests cover all three sections, their exact order, the unified
  DeepSeek credential form, absence of implementation details,
  appearance/language/sidebar persistence calls, document
  theme/density/language state, composer menus and toast Undo, and Back behavior.
- Electron E2E covers real settings navigation, credential save, language and
  sidebar persistence, Session model/permission controls, and restoration after
  relaunch.
- Manual screenshots cover Chinese and English composer/settings states plus
  the resized sidebar in light and dark modes.
