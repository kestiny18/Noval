# Hooks 与完成证据

> [English](hooks.md) | 简体中文 · [Application API](application-api.zh-CN.md)

项目 Hooks 仍然是生命周期策略与验证扩展。Noval 只读取 `<workdir>/.noval/hooks.json`，保持声明顺序，通过 `ProcessRuntime` 执行命令，并把危险操作授权绑定到 Hook id 与配置指纹。

## 生命周期职责

| 事件 | 职责 | 能否成为完成证据 |
|---|---|---|
| `PreToolUse` | 在工具执行前阻止调用 | 否 |
| `PostToolUse` | 在执行后附加诊断 | 否 |
| `Stop` | 验证候选最终回复并要求修复 | 只有显式映射时可以 |

Pre/Post Hook 可以执行策略或报告局部诊断，但它们成功并不证明用户验收条件已经成立。

## 把 Stop Hook 映射到验收条件

Hook 配置格式不需要变化：

```json
{
  "version": 1,
  "hooks": {
    "Stop": [
      {
        "id": "test-suite",
        "match": {"afterTools": ["write_file", "edit_file", "run_bash"]},
        "command": "python",
        "args": ["-m", "pytest", "-q"],
        "timeout": 300
      }
    ]
  }
}
```

宿主通过目标契约显式建立证据映射：

```python
AcceptanceCriterion(
    criterion_id="tests",
    description="The project test suite passes.",
    verification_source="hook:test-suite",
)
```

每次匹配的 Stop 执行都会生成绑定到该验收条件的验证：

- `allow` 映射为 `passed`；
- `deny` 映射为 `failed`；
- `context` 映射为 `unknown`。

结果可以引用当前回合已执行工具的安全收据，但不会持久化 Hook stdout/stderr 或工具输出。如果 Hook 因匹配条件未运行、执行前被用户拒绝、缺失或已从配置删除，验收条件不会静默通过。

## 优先级与修复

deny/context Stop Hook 继续遵循原有修复循环：候选回复被隐藏，经过截断和脱敏的反馈返回模型，模型执行相关修复后可以再次验证。最新的匹配验证决定验收条件状态；没有新工具活动时重复同一失败会诚实停止。

语义 Judge 独立运行，不能把失败、未知、缺失或过期的 Hook 证据改写为完成。

## 安全边界

- Hooks 不能覆盖 system policy、用户意图、权限、路径限制、沙箱或脱敏；
- Hook 命令属于 `DANGEROUS`，必须通过 `ProcessRuntime`；
- 授权绑定 Hook id 与配置 hash；
- Hook 诊断进入模型或 Session 前必须脱敏并截断；
- Hook 证据属于派生 task 状态，不是 canonical Session 真相源。
