# Agent v0 Single File

> [English](README.md) | 简体中文

这是 Noval 最早期的单文件 Agent 原型，用纯手工方式写成。

它包含两个入口：

- `run_v0`: 最小聊天循环
- `run_v1`: 加入 OpenAI-compatible tool calling 后的工具循环

运行前需要安装 `openai` 包，并设置 DeepSeek API Key：

```bash
export DEEPSEEK_API_KEY="sk-..."
```

Windows PowerShell：

```powershell
$env:DEEPSEEK_API_KEY="sk-..."
```

```bash
python agent.py
```
