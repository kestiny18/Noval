# Noval Eval assets

[简体中文](README.zh-CN.md)

`evals/` contains executable evaluation assets collected while developing
Noval. The production package never depends on Eval code or an `eval_mode`
branch.

```text
evals/
  context/           # checkpoint structure, semantics, continuation, recovery
  task/              # semantic ledger + explicit goal/evidence contract replay
  private/           # ignored, sanitized evidence workspace
    manifest.jsonl
    evidence/
```

Public cases must be minimal, sanitized, and reproducible. Raw Sessions, logs,
screenshots, terminal output, customer code, credentials, and personal data stay
outside Git or under the ignored private evidence directory.

When deriving a regression case:

1. Start from a trace with a failure, correction, and verified outcome.
2. Isolate one behavior boundary.
3. Replace project names, paths, domains, identifiers, and credentials.
4. Express expected and forbidden behavior rather than a fixed prose answer.
5. Review the sanitized case before committing it.

`.gitignore` is not a security boundary. Inspect every staged Eval asset.

Future domains such as `behavior/`, `persistence/`, and `environment/` should be
siblings of `context/` and `task/`, never imports of production runtime code.
