# 贡献指南

> [English contribution guide](CONTRIBUTING.md) | 简体中文

欢迎参与 Noval。这是一个面向强模型、刻意保持薄 Harness 的通用执行内核。模型选择方法，内核负责权限、执行、状态和验证边界。贡献前请先读：

- [PHILOSOPHY.zh-CN.md](PHILOSOPHY.zh-CN.md) —— 为什么采用强模型、薄 Harness
- [AGENTS.zh-CN.md](AGENTS.zh-CN.md) —— 不可破坏的实现约束
- [DESIGN.md](DESIGN.md) —— 当前规范架构
- [docs/adr/README.md](docs/adr/README.md) —— 规范 ADR 索引

## 本地开发

```bash
git clone https://github.com/kestiny18/Noval.git
cd Noval
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest -q
```

## 加一个工具

按设计，加工具 = 写一个带类型注解的函数 + 一行 `@tool`，错误/截断/确认/日志/schema 全自动继承：

```python
from noval.tools import tool, Risk, ToolError

@tool(risk=Risk.WRITE, param_descriptions={"path": "目标路径"})
def write_file(path: str, content: str) -> str:
    """把内容写入文件，覆盖已有内容。"""
    ...
```

工具契约（只需记住这条）：
- 成功 → `return 原始内容`（别自己 try/except 兜底）
- 领域错误 → `raise ToolError("带领域信息的好提示")`
- 其余（通用异常、超时、截断、确认、日志）由框架负责

## 提 PR 前

- `pytest -q` 全绿，并为新行为补测试（用 `MockClient` 可离线测整条循环）。
- 改了公共契约、核心接缝或横切不变量，请新增或更新 ADR。
- 英文规范文档先更新；用户可见含义变化时同步中文入口。
- 提交信息说清「做了什么 + 为什么」。

## 报 Issue

带上复现步骤、期望行为、实际行为，以及 Python 版本 / 操作系统。
