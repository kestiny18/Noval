# 目标、证据与完成契约 Eval

> [English](README.md) | 简体中文

这组离线 Eval 同时约束两种完成模式，但不规定主模型应采用什么工作流程：

- 未提供显式目标的旧调用，继续使用“最近用户输入 + 最终可见回复”的语义账本；
- 提供显式目标时，必须由当前且与验收条件匹配的验证证据决定完成状态，语义判断单独记录。

运行确定性合成回放：

```powershell
py -m evals.task.run
```

保存报告：

```powershell
py -m evals.task.run `
  --json-report .eval-results/task/report.json `
  --markdown-report .eval-results/task/report.md
```

公开用例覆盖：

- 旧模式下的 `completed`、`incomplete`、`waiting_user`、`blocked` 和 `uncertain`；
- semantic judge 不能用高置信度替代缺失证据；
- 所有当前证据通过；
- Stop Hook 失败或返回未知结果；
- 确定性时钟推进后证据过期。

运行器只使用合成 judge verdict 和固定时钟，不需要网络、Provider 凭据或真实模型调用。
