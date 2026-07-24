# Desktop Markdown Rendering

## Goal

Render canonical visible transcript text as readable Markdown while preserving
the Desktop's role as a display and interaction host. Keep conversation
scrolling available without showing a persistent scrollbar.

## Chosen design

The Renderer uses `react-markdown` with GitHub-flavored Markdown support for
headings, emphasis, lists, tables, blockquotes, links, inline code, and code
blocks. The same component renders persisted user/assistant text and live
visible-text streams so the final canonical transcript does not change visual
semantics after a turn completes.

Raw HTML parsing is disabled. Only HTTP, HTTPS, and mail links receive an
anchor; other link targets remain visible text. There is no editor mode,
document mutation, syntax execution, or access to Node, filesystem, Provider
responses, or opaque reasoning state.

The conversation container retains `overflow: auto` from the existing layout.
CSS hides its scrollbar across Chromium, Firefox, and legacy Microsoft syntax;
mouse wheel, touchpad, keyboard, and programmatic scrolling remain unchanged.

## Verification

- Component tests cover headings, emphasis, lists, tables, raw HTML rejection,
  and unsafe-link rejection.
- Renderer and Electron E2E suites guard the existing Session lifecycle.
- A production build confirms the Markdown dependency is bundled in the
  Renderer only.
