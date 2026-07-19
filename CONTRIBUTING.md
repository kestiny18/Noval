# Contributing to Noval

[简体中文](CONTRIBUTING.zh-CN.md)

Thank you for helping build Noval. This repository values small, reviewable
changes that strengthen a domain-neutral execution kernel.

Before changing code, read:

- [PHILOSOPHY.md](PHILOSOPHY.md) — why Noval uses a strong-model, thin-harness architecture;
- [AGENTS.md](AGENTS.md) — implementation invariants;
- [DESIGN.md](DESIGN.md) — current architecture;
- [Architecture Decision Records](docs/adr/README.md) — normative decisions.

## Local development

```bash
git clone https://github.com/kestiny18/Noval.git
cd Noval
python -m venv .venv

# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate

python -m pip install -e ".[dev]"
python -m pytest -q
```

The normal suite is offline. Use `MockClient` to test complete model/tool loops
without credentials or network calls.

## Where a change belongs

Before adding a core abstraction, ask whether it establishes a domain-neutral
boundary for reality, authority, continuity, or evidence.

- Domain procedure or prompt: Skill.
- External capability: tool or MCP server.
- Project acceptance policy: Hook.
- UI, queueing, or transport: host application.
- Cross-cutting execution invariant: Noval core, with an ADR when significant.

Do not add mandatory Planner/Executor/Reviewer roles to solve one workflow.

## Adding a tool

```python
from noval.tools import Risk, ToolError, tool

@tool(risk=Risk.READ, param_descriptions={"path": "Target path"})
def inspect_file(path: str) -> str:
    """Inspect a domain-specific file."""
    ...
```

- Return raw domain content on success.
- Raise `ToolError` only for a corrective domain failure.
- Leave generic errors, permission, timeout, truncation, redaction, and logging
  to the executor.

## Pull requests

- `main` is protected: create a branch and pull request rather than pushing to
  the default branch directly.
- Keep one coherent outcome per PR.
- Add or update tests for behavior changes.
- Add an ADR for a new public contract, core seam, or cross-cutting invariant.
- Update English canonical documentation first; update the Chinese entry point
  when user-facing meaning changes.
- Run `python -m pytest -q`, `python -m compileall -q noval examples evals`, and
  `git diff --check`.
- Wait for the required `CI gate` and `Analyze Python` checks, and resolve every
  review conversation before merging.
- Report exact validation results. Do not claim tests that were not run.

## Issues

Use the structured templates. Include a minimal reproduction, expected and
actual behavior, operating system, Python version, and whether persistent
Sessions or external processes are involved.

Security vulnerabilities must follow [SECURITY.md](SECURITY.md), not a public
Issue.
