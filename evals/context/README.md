# Context checkpoint Eval

这组 Eval 判断：只拿 checkpoint 摘要与最近原始尾部时，Agent 是否仍能理解状态并继续工作。
它不要求摘要匹配一份固定文本，而是检查状态事实、禁止状态、来源范围和恢复安全性。

## 架构边界

- `noval/context.py` 仍只负责运行时压缩与恢复。
- `evals/context/` 是运行时之外的调用者，生产代码不包含 `eval_mode` 分支。
- 运行时与 Eval 只共享 `build_compaction_messages()`，保证评测调用的就是当前生产 prompt。
- 删除整个 `evals/` 目录不会影响 Noval 运行。

## 命令

零成本校验用例资产，包括连续 seq 和完整 tool-call 协议：

```powershell
py -m evals.context.run
```

使用 `~/.noval/settings.json` 配置的模型生成摘要并保存报告：

```powershell
py -m evals.context.run --generate `
  --output .eval-results/context/candidates.jsonl `
  --json-report .eval-results/context/report.json `
  --markdown-report .eval-results/context/report.md
```

不调用模型，重放已有候选摘要：

```powershell
py -m evals.context.run `
  --summaries .eval-results/context/candidates.jsonl `
  --json-report .eval-results/context/replay.json
```

使用候选摘要经真实 checkpoint 文件冷恢复，再评估状态理解与受控工具行动：

```powershell
py -m evals.context.recovery `
  --summaries .eval-results/context/candidates.jsonl `
  --stage all
```

行动 Eval 只注册进程内合成工具，用于观察动态事实是否重查、已完成写入是否被重复执行；
不会读取分支、进程、网络或修改真实文件。

`.eval-results/` 默认不提交。需要形成版本基线时，应人工复核、匿名化，再把选定报告复制到未来的
`evals/context/baselines/`。

## 第一版评分边界

确定性检查覆盖：

- 八个章节的数量与顺序
- 来源范围之外的 `seq`
- 拆断或孤立的 tool-call 协议
- 合成 secret canary 泄露
- 每个用例声明的状态事实与禁止状态
- 摘要长度、压缩率和分项加权得分

状态事实目前使用透明的正则证据做 smoke check，允许多种措辞，但不能代替完整语义判断。
LLM Judge、人工复核和对话内压缩后继续仍属于后续层。`recovery.py` 已覆盖 checkpoint
冷恢复后的理解与受控工具行动，但不能把当前分数解释为完整恢复能力。
