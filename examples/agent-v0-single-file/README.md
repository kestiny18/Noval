# Agent v0: single-file prototype

[简体中文](README.zh-CN.md)

This directory preserves Noval's earliest handwritten Agent prototype. It is a
historical learning artifact, not the current architecture.

- `run_v0` is a minimal chat loop.
- `run_v1` adds OpenAI-compatible tool calling.

Install `openai`, set a DeepSeek API key, and run the file:

```powershell
$env:DEEPSEEK_API_KEY="your-key"
python examples/agent-v0-single-file/agent.py
```

The production implementation lives under `noval/` and adds the registry,
executor, authority, isolation, canonical state, recovery, and Application API
boundaries that this prototype intentionally lacks.
