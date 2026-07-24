# Application API

> [English](application-api.md) | 简体中文 · [ADR-0005](adr/0005-goal-evidence-completion-contract.md) · [ADR-0006](adr/0006-desktop-consumer-observation-boundary.md) · [ADR-0008](adr/0008-current-session-schema-discovery.md) · [ADR-0010](adr/0010-runtime-owned-model-configuration.md)

Noval 的 Application API 把“循环为何停止”和“任务是否完成”分开：

- `TurnResult.stop_reason` 描述 Agent 循环停止原因；
- `TurnResult.status` 描述公开的回合/任务状态；
- `TurnResult.completion` 在存在显式目标时给出逐项验收证据。

模型可以结束回复，但必要证据仍然缺失；Provider 也可能在目标已验证后发生故障。因此这两个维度不能混为一谈。

## Desktop 消费者观察接口

同一套 headless API 已提供 Desktop 或终端宿主所需的基础能力，而无需访问 Agent 内部对象：

- `runtime.configuration()` 和 `runtime.get_model_configuration()` 返回 settings v2 的无凭据视图，包含 Profile、Connection、Configured Model、revision 和凭据可用状态，但永不返回凭据值；配置修改通过有类型的 Connection、Configured Model 和默认模型方法完成；
- Session 在可变 application metadata 中保存 Configured Model id；`session.select_model(configured_model_id)` 由 Runtime 选择同一 Connection 的 agent/judge 组合，即使在 Turn 运行中调用也只影响下一 Turn，当前 Turn 的绑定保持不变；若全局配置后来删除了 Session 弱引用的模型，Session 仍可打开并重新选择，修复前 Turn admission 明确失败为 `model_configuration_missing`；
- `runtime.list_persisted_projects()` 从当前 schema 的 canonical Session 存储派生稳定的项目清单，旧实验格式不会投影给宿主；宿主无需自行解析 `~/.noval/sessions` 或自定义 `sessions_dir`；
- `session.transcript()` 使用从 1 开始的稳定序号和游标分页读取历史；它不暴露 system message、Provider replay state、provenance 或工具参数值，工具调用只提供参数名；
- `session.rename()` 只能在 Session 空闲时修改有界标题，并写入可变 metadata sidecar，不会改写 canonical JSONL；
- 支持流式能力的 client 会依次发出 `model.started`、零个或多个 `model.output.delta`，最后发出 `model.completed`；失败的部分流会发出 `model.output.aborted`，但不会写入 canonical Session；
- `session.replay_events()` 可从每个打开 Session 的有界内存窗口补读事件；`gap_detected=True` 表示旧事件已淘汰，宿主应先从 transcript 重建持久 UI 状态。

只实现 `complete()` 的旧 client 继续兼容，只是不会产生文本 delta。Provider thinking/reasoning block 属于 adapter 私有的 opaque replay state，不会被显示、记录到日志、交给 Judge、放入 transcript 或作为事件发出；完成后的 metrics 可以包含 reasoning token 数量。事件仅存在于当前 live process，Session 关闭或进程重启后不会恢复。

## 定义显式目标

```python
from noval import AcceptanceCriterion, GoalContract, TurnRequest

goal = GoalContract(
    goal_id="release-0.12.0",
    objective="Publish v0.12.0 after all required checks pass.",
    scope=("current repository", "release metadata"),
    authority=("deliver through a pull request",),
    acceptance_criteria=(
        AcceptanceCriterion(
            criterion_id="ci",
            description="Required CI checks pass.",
            verification_source="host:github-checks",
            max_age_seconds=3600,
        ),
        AcceptanceCriterion(
            criterion_id="project-tests",
            description="The configured project test Hook passes.",
            verification_source="hook:test-suite",
        ),
    ),
)

result = session.run_turn(TurnRequest(
    text="Prepare the release.",
    client_request_id="release-action-42",
    goal=goal,
))
```

目标契约是宿主提供的观察数据。`scope` 和 `authority` 用于保持用户意图，但不会授予工具权限、扩大路径范围、绕过 Hooks 或改变沙箱策略。

相同内容的同一 `goal_id` 可幂等重复提交；用相同 id 静默改写活动契约会返回 `goal_contract_error`。提交不同 id 会替换活动目标，并从空收据、空验证状态开始。

## 契约对象

| 对象 | 含义 |
|---|---|
| `AcceptanceCriterion` | 具名验收条件，可指定验证来源和最大证据年龄 |
| `ActionReceipt` | 一次工具尝试的安全事实：调用/工具 id、风险、结果、时间、参数键和脱敏结果摘要 |
| `VerificationResult` | 可信来源针对一个目标验收条件给出的 passed/failed/unknown 观察 |
| `CriterionReport` | 单个验收条件当前的 passed/failed/missing/stale/unknown 状态 |
| `CompletionReport` | 显式目标的派生完成状态，以及单独记录的语义评估 |

收据不包含参数值或原始工具输出。验证结果可以引用收据，但收据本身永远不能让验收条件通过。

## 记录宿主验证

`record_verification()` 只能在 Session 空闲时调用，与权限修改共用同一并发边界。

```python
from datetime import datetime, timezone
from noval import EvidenceOutcome, VerificationResult

report = session.record_verification(VerificationResult(
    verification_id="github-checks-run-42",
    goal_id="release-0.12.0",
    criterion_id="ci",
    source="host:github-checks",
    outcome=EvidenceOutcome.PASSED,
    observed_at=datetime.now(timezone.utc).isoformat(),
    subject="pull request checks",
))

current = session.completion_report()
```

跨目标、未知验收条件、错误来源、未知收据或明显来自未来的验证会被拒绝。subject/summary 自由文本在写入 task sidecar 前统一脱敏；运行时事件只暴露有界验证元数据，不暴露这些自由文本。

## 完成优先级

显式目标会在每次查询时使用每个验收条件最新且来源匹配的结果：

1. 任一当前失败结果使目标为 `incomplete`；
2. 任一缺失、过期或未知结果使目标为 `uncertain`；
3. 只有所有验收条件都有当前通过结果，目标才是 `completed`。

语义 Judge 结果位于 `completion.semantic`，只评估可见回复，不能升级或覆盖契约证据。未提供显式目标时，Noval 保留旧的轻量语义账本行为。

运行时 `error` 始终让本回合成为 `failed`，即使独立完成报告说明目标此前已完成。其它停止原因下，显式完成状态成为公开 status，同时 `stop_reason` 仍可独立检查。

## Hooks 作为验证来源

验收条件可以声明 `verification_source="hook:<hook-id>"`。只有匹配的 Stop Hook 会生成证据：

| Stop Hook 结果 | 验证结果 |
|---|---|
| `allow` | `passed` |
| `deny` | `failed` |
| `context` | `unknown` |

PreToolUse 和 PostToolUse Hook 不能满足完成条件。详见 [Hooks 与完成证据](hooks.zh-CN.md)。

## 持久化、事件与兼容性

- canonical Session JSONL 使用 schema v3，仍是唯一对话真相源；模型选择保存在可变 application metadata 中，不进入 JSONL header；
- 目标/证据快照使用可恢复 task-sidecar schema v2；旧 schema-v1 语义快照仍可读取，但不会得到伪造的新证据；
- Application DTO 使用 API schema v2，Sidecar 使用 protocol v2。这是有意的硬升级；旧 settings、Session、API 和 protocol schema 会被拒绝，不执行迁移；
- `turn.started` 包含 `goal_id`，`tool.completed` 包含安全收据，`turn.completed`/`turn.failed` 包含收据和完成报告，宿主验证会发出 `verification.recorded`；
- `session.models_selected` 报告持久选择及仍在运行的旧绑定；`model.configuration_changed` 只包含新 revision 与默认 Configured Model id，宿主需要时重新读取无凭据配置视图；
- 损坏的 task-sidecar 尾记录会被跳过，不会改写 canonical Session 历史。
