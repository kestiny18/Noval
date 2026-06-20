# 贡献指南

欢迎参与 Noval。这是一个刻意保持最小的通用 agent 内核，贡献前请先读两份核心文档：

- [AGENTS.md](AGENTS.md) —— 不可破坏的约束（三条接缝、工具契约、验收标准）
- [DESIGN.md](DESIGN.md) —— 每个决策的「为什么」

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
- 改了原则性约束，请同步更新 AGENTS.md / DESIGN.md。
- 提交信息说清「做了什么 + 为什么」。

## 报 Issue

带上复现步骤、期望行为、实际行为，以及 Python 版本 / 操作系统。
