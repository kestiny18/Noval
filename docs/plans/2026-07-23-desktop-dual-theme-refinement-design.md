# Desktop Dual-theme Refinement

## Goal

Bring the Desktop closer to a restrained, modern coding workspace in both
light and dark system themes without changing its product architecture or
adding speculative controls.

## Chosen design

The two supplied HTML references are treated as a visual specification. Their
shared hierarchy becomes semantic CSS tokens for canvas, sidebar, surfaces,
text, borders, hover state, active state, and elevation. The Renderer follows
the operating-system color preference through `prefers-color-scheme`; no theme
preference is persisted yet.

An empty Session has one focal sentence:
`我们应该在 <project> 中构建什么？`. The project name is the only emphasized
element. The decorative icon, supporting paragraph, suggestion cards, and the
three proposed composer tags are deliberately omitted.

The project tree, Session lifecycle, permission controls, and Python sidecar
protocol remain unchanged. This is a Renderer-only visual refinement.

## Verification

- Renderer tests cover the no-project message and project-aware empty Session.
- Electron E2E covers the project headline and absence of tag chips.
- Light and dark Electron screenshots are inspected for hierarchy, contrast,
  focus treatment, and composer placement.
