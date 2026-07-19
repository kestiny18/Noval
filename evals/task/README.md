# Task completion-ledger Eval

[简体中文](README.zh-CN.md)

This Eval keeps the semantic task ledger's deliberately small contract stable.
It does not prescribe how the main model should work and does not simulate tool
authority.

The task layer:

- remembers the last three unique user inputs;
- sends those inputs and the final visible reply to the judge;
- persists a structured verdict.

Run the offline synthetic replay:

```powershell
py -m evals.task.run
```

Save reports:

```powershell
py -m evals.task.run `
  --json-report .eval-results/task/report.json `
  --markdown-report .eval-results/task/report.md
```

Cases cover `completed`, `incomplete`, `waiting_user`, `blocked`, and
`uncertain`. The judge may assess only evidence visible in the final reply; it
must not claim that hidden tool operations did or did not occur.

This is a semantic ledger, not proof of external-state completion. Evidence-aware
completion requires a separate architecture contract.
