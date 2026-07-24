# Desktop Tool Activity Presentation

## Goal

Reduce the visual weight of tool lifecycle events so conversation text remains
the primary reading surface while tool use stays observable.

## Chosen design

The Renderer converts canonical transcript entries into a display-only
timeline. Visible user and assistant text remains unchanged. Tool calls become
small activity rows with a low-contrast icon and a semantic label such as
`Ran a command`, `Inspected files`, or `Edited a file`.

Successful tool results are matched to their call id and folded into the call.
Adjacent successful calls of the same category are summarized, for example
`Ran 2 commands`. Failed results remain explicit and use the existing danger
color. An unmatched result is still shown as a generic tool activity so a
bounded transcript page never silently loses lifecycle information.

The timeline uses only the existing safe transcript DTO: tool name, call id,
argument-key names, and success state. It does not expose argument values, tool
output, credentials, Provider state, or opaque reasoning. No Core, Session,
Sidecar protocol, permission, or Eval behavior changes.

## Verification

- Renderer tests cover successful call/result matching and adjacent activity
  aggregation.
- Existing Markdown, completion evidence, Session lifecycle, and Electron E2E
  suites remain green.
- Light and dark visual inspection checks hierarchy, spacing, and failure
  contrast.
