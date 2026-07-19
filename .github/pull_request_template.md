## Outcome

<!-- What user-visible or architectural outcome does this PR deliver? -->

## Why this belongs here

<!-- Explain why the change belongs in Noval core rather than a Skill, MCP server, Hook, or host. -->

## Scope

- In scope:
- Explicitly out of scope:

## Architecture and safety

- [ ] No Provider SDK wire format leaked into core layers.
- [ ] No tool-specific error, permission, timeout, truncation, redaction, or trace policy bypasses the executor.
- [ ] Tool permission is not treated as expanded user intent.
- [ ] External processes use `ProcessRuntime`.
- [ ] Significant contract or seam changes include an ADR.
- [ ] The change does not add workflow ceremony that a capable model can choose for itself.

## Evidence

<!-- List exact commands and results. Do not write "tests pass" without the command. -->

- [ ] `python -m pytest -q`
- [ ] `python -m compileall -q noval examples evals`
- [ ] `git diff --check`
- [ ] Changed files were reviewed for credentials and unrelated modifications.

## Documentation

- [ ] English canonical documentation is updated.
- [ ] Chinese user-facing entry points are updated when meaning changed.
- [ ] Related Issues are linked and remain open unless all acceptance criteria are complete.
