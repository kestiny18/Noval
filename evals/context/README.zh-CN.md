# Context checkpoint Eval

> [English](README.md) | 简体中文

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

让同一个 Agent 在当前对话中触发首次压缩，并比较 checkpoint 摘要与压缩后的继续回答：

```powershell
py -m evals.context.continuation
```

安全或稳定性问题应重复采样，避免把一次随机通过当成结论：

```powershell
py -m evals.context.continuation `
  --case secret_canary_redaction `
  --repeat 5 `
  --output-dir .eval-results/context/stability-secret
```

该路径会保留一个最近原始回合，强制较早完整回合进入 checkpoint，并验证实际覆盖边界；
它与 `recovery.py` 的冷启动恢复路径相互独立。

使用不同模型做辅助语义 Judge（确定性硬检查仍优先）：

```powershell
py -m evals.context.judge `
  --summaries .eval-results/context/candidates-v2.jsonl `
  --model deepseek-v4-flash
```

当前 Judge 与摘要模型使用同一 DeepSeek Provider/API Key，只是模型不同；报告会明确记录这一限制。

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
LLM Judge、人工复核仍属于后续层。`recovery.py` 覆盖 checkpoint 冷恢复后的理解与受控
工具行动，`continuation.py` 覆盖同一 Agent 在对话内压缩后的继续回答；但在加入真实会话
切片和阈值回放前，不能把当前分数解释为完整恢复能力。
