# Goal, evidence, and completion Eval

[简体中文](README.zh-CN.md)

This offline Eval keeps both completion modes stable without prescribing how
the main model works:

- legacy turns retain the semantic ledger over recent user inputs and the final
  visible reply;
- explicit goals require current criterion-level verification and keep the
  semantic assessment separate.

Run the deterministic synthetic replay:

```powershell
py -m evals.task.run
```

Save reports:

```powershell
py -m evals.task.run `
  --json-report .eval-results/task/report.json `
  --markdown-report .eval-results/task/report.md
```

The public cases cover:

- legacy `completed`, `incomplete`, `waiting_user`, `blocked`, and `uncertain`
  semantic verdicts;
- missing evidence that semantic confidence cannot upgrade;
- all-current passing evidence;
- failed and unknown Stop Hook evidence; and
- evidence that becomes stale as the deterministic clock advances.

The runner uses synthetic judge verdicts and a fixed clock. It requires no
network access, Provider credentials, or live model calls.
