# Context checkpoint Eval

[简体中文](README.zh-CN.md)

These evaluations test whether an Agent can understand state and continue work
from a checkpoint summary plus the recent raw tail. They score state facts,
forbidden claims, source boundaries, protocol integrity, and recovery safety
rather than comparing one fixed summary string.

## Boundary

- `noval/context.py` owns runtime compaction and recovery.
- `evals/context/` is an external consumer of that runtime.
- Runtime and Eval share only the production compaction-message builder.
- Removing `evals/` does not change Noval behavior.

## Commands

Validate the bundled assets without a model call:

```powershell
py -m evals.context.run
```

Generate candidates with the model configured in `~/.noval/settings.json`:

```powershell
py -m evals.context.run --generate `
  --output .eval-results/context/candidates.jsonl `
  --json-report .eval-results/context/report.json `
  --markdown-report .eval-results/context/report.md
```

Replay existing summaries without calling a model:

```powershell
py -m evals.context.run `
  --summaries .eval-results/context/candidates.jsonl `
  --json-report .eval-results/context/replay.json
```

Exercise cold recovery and controlled synthetic tool action:

```powershell
py -m evals.context.recovery `
  --summaries .eval-results/context/candidates.jsonl `
  --stage all
```

Exercise in-conversation compaction and continuation:

```powershell
py -m evals.context.continuation
```

Repeat security-sensitive cases to avoid treating one random pass as evidence:

```powershell
py -m evals.context.continuation `
  --case secret_canary_redaction `
  --repeat 5 `
  --output-dir .eval-results/context/stability-secret
```

Use another model as an auxiliary semantic judge while deterministic checks
remain authoritative:

```powershell
py -m evals.context.judge `
  --summaries .eval-results/context/candidates-v2.jsonl `
  --model your-judge-model
```

## Interpretation limits

The first evaluation layer checks section order, source sequence, tool protocol
boundaries, secret canaries, required/forbidden facts, compression metrics, and
controlled recovery behavior. It does not yet include enough anonymized real
Sessions and threshold replay to claim complete long-task recovery quality.

Results under `.eval-results/` are ignored. Review and anonymize any baseline
before intentionally adding it to the repository.
