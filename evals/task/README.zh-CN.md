# Task Eval

> [English](README.md) | 简体中文

这组 Eval 判断任务完成判定层的极简契约是否稳定。

它不评价主模型是否“该怎么做”，也不模拟工具边界。当前任务层只负责：

- 记录最近三个不重复的用户输入；
- 在主模型给出最终可见回复后，把这些输入和最终回复交给 judge；
- 持久化 judge 的结构化 verdict。

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

当前版本离线回放 synthetic judge verdict，覆盖：

- 最近三个不重复用户输入；
- `completed` / `incomplete` / `waiting_user` / `blocked` / `uncertain`；
- judge 输入只包含 recent user inputs 与 assistant final reply。
- judge 只能评价最终可见回复中的证据，不得断言隐藏的工具操作实际执行或未执行。

真实模型 Eval 后续只需要替换 synthetic judge 为实际 `judge_model`，不应重新引入额外的任务解析、工具拦截或证据判定层。
