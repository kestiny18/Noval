# Hooks and completion evidence

[简体中文](hooks.zh-CN.md) · [Application API](application-api.md)

Project Hooks remain lifecycle policy and validation extensions. Noval reads
only `<workdir>/.noval/hooks.json`, preserves declaration order, executes
commands through `ProcessRuntime`, and applies dangerous-action approval to the
Hook id plus configuration fingerprint.

## Lifecycle roles

| Event | Role | Completion evidence? |
|---|---|---|
| `PreToolUse` | Block a tool before execution | No |
| `PostToolUse` | Attach diagnostics after execution | No |
| `Stop` | Validate a candidate final response and request repair | Only when explicitly mapped |

This separation matters. A Pre/Post Hook can enforce policy or report a local
diagnostic, but its success does not prove a user's acceptance criterion.

## Map a Stop Hook to a criterion

The Hook configuration does not need a new schema:

```json
{
  "version": 1,
  "hooks": {
    "Stop": [
      {
        "id": "test-suite",
        "match": {"afterTools": ["write_file", "edit_file", "run_bash"]},
        "command": "python",
        "args": ["-m", "pytest", "-q"],
        "timeout": 300
      }
    ]
  }
}
```

The host opts into the evidence mapping through the goal:

```python
AcceptanceCriterion(
    criterion_id="tests",
    description="The project test suite passes.",
    verification_source="hook:test-suite",
)
```

Each matching Stop execution creates criterion-bound verification:

- `allow` becomes `passed`;
- `deny` becomes `failed`; and
- `context` becomes `unknown`.

The result references safe receipts for executed tools from the current turn.
It does not persist Hook stdout/stderr or tool output. If the Hook is filtered
out, denied by the user before execution, missing, or no longer configured, the
criterion cannot silently pass.

## Precedence and repair

A denied/context Stop Hook still follows the existing repair loop: the candidate
reply is hidden, bounded redacted feedback returns to the model, and validation
may run again after a relevant repair action. The latest matching verification
controls the criterion. Repeating the same failure without new tool activity
stops honestly.

The semantic judge runs independently and cannot turn failed, unknown, missing,
or stale Hook evidence into completion.

## Security boundary

- Hooks cannot override system policy, user intent, permissions, confinement,
  sandboxing, or redaction.
- Hook commands are `DANGEROUS` and use `ProcessRuntime`.
- Approval is bound to Hook id and configuration hash.
- Hook diagnostics are redacted and truncated before model or Session context.
- Hook evidence is derived task state, never canonical Session truth.
