# Task Eval

这组 Eval 判断任务状态与完成验证层是否能在离线、零模型成本下保持基本行为稳定。

它不评价自然语言回复“写得好不好”，而是回放最小任务事件，检查 `TaskController` 最终得到的结构化状态：

- 当前目标与 action mode 是否正确；
- 用户明确给出的 READ_ONLY 边界是否拦截 WRITE / DANGEROUS 工具；
- “原因是什么/为什么”这类诊断问句是否不会被升级成任务级硬限制；
- 工具结果是否形成 evidence；
- 候选最终回复是否进入 `completed` / `waiting_user` / `blocked` / `violated`；
- “好的/继续”等确认词是否不会错误替换当前目标；
- 新目标是否会提升 revision。

运行：

```powershell
py -m evals.task.run
```

保存报告：

```powershell
py -m evals.task.run `
  --json-report .eval-results/task/report.json `
  --markdown-report .eval-results/task/report.md
```

当前版本只覆盖确定性回放。后续可继续加入：

- system prompt 行为回放：诊断型问题先计划/只读，状态变更前等待授权；
- Prompt injection 出现在工具 evidence 中时，Judge 不能被劫持；
- 多步骤 completed / remaining 去重；
- 从真实匿名会话切片派生的任务状态样本。
