# Changelog

本文件记录 Noval 可对外识别的版本里程碑。项目尚处于 `0.x` 阶段，接口可能继续演进。

## [Unreleased]

### Added

- Task state and completion verification MVP: append-only task ledger, read-only
  action guard, completion verifier, independent `judge_model`, and usage
  purpose tracking for judge calls.

- 持久化增量上下文压缩：按 Token 预算生成 checkpoint，恢复时复用摘要与原始尾部，并保留完整 Session 作为唯一真相源。
- 仓库级 context Eval 脊柱：最小语义/对抗用例、结构与敏感信息硬检查、离线重放及 Markdown/JSON 报告。

### Changed

- 上下文压缩 prompt v2 明确禁止凭据原值进入 checkpoint，并要求保留用户否决/暂停决策且不得恢复为待办。

## [0.4.0] - 2026-07-01

运行治理与可观测性版本。权限、日志、DeepSeek thinking 和 Token 用量均已在真实 CLI 会话中验证。

### Added

- 默认写入 `~/.noval/logs/YYYY-MM-DD/` 的脱敏运行日志，支持目录与保留期配置。
- Git 提交前预检、测试、单次提交和 hash 回报的最小 agent 行为约束。
- 会话级 ASK / FULL_ACCESS 权限模式与 `/permissions` 即时控制命令。
- `/reasoning` 状态命令与每回合 reasoning token、模型耗时、工具调用指标。
- 按日、跨项目汇总的 Token 用量事件存储与 `/usage` 命令，支持缓存、reasoning 和模型维度。

### Changed

- Windows 上 `run_bash` 优先选择 Git for Windows Bash，WSL 作为后备；环境提示与实际执行共享启动时固化的同一个后端。
- 工具调用 trace 只记录参数名，不再记录参数值；常见凭据形态在文件日志中二次脱敏。
- 降低 `httpx` / `openai` 请求流水的日志级别，保持 CLI 输出聚焦任务本身。
- 权限模式与 `[a]` 工具授权改存 session sidecar，恢复时直接生效；移除全局 `auto_approve`。

### Fixed

- DeepSeek 思考模式的工具调用轮保留并回传 `reasoning_content`，支持多工具子轮和会话恢复。

## [0.3.0] - 2026-06-29

会话持久化与恢复版本。已通过多 workdir、中文路径、大历史、任务中断和进程强杀等真实场景验证。

### Added

- `SessionStore` 接缝与 JSONL append-only 会话存储。
- 按 workdir hash 隔离项目会话，支持 sidecar 标题。
- CLI `--resume` 选择或指定历史会话。
- CLI 回合标签、留白和多行对齐；交互式终端轻量着色，重定向时保持纯文本。
- 恢复时重建 system 上下文，并修复悬空 tool call。
- `persist_sessions` / `sessions_dir` 配置。

### Fixed

- CLI 将 ANSI 提示符输出与 `input()` 读取分离，避免 readline 对控制序列的兼容差异。
- shell 环境探测与 `run_bash` 使用非交互 stdin，避免子进程改变调用终端的输入模式。

### Security

- 会话 id 限制为安全文件名字符，避免路径逃逸。
- 会话文件尽量设置为仅当前用户可读写；Windows 权限仍由系统 ACL 决定。
- 会话内容当前为明文，文档明确提示其可能包含敏感信息。

## [0.2.0] - 2026-06-23

多工具与真实任务驱动的收敛版本。

### Added

- `read_file`、`write_file`、`edit_file`、`list_directory`、`glob`、`grep`、`run_bash`。
- `Context` 注入与跨工具共享的 read-tracker。
- 文件修改前先读、外部改动检测和 Windows mtime 回退。
- 按命令动态评估 `run_bash` 风险，支持三态确认。
- 启动环境探测和 Windows / WSL 路径映射。
- `AGENTS.md` 项目记忆，回退支持 `CLAUDE.md`。
- 当前时间随用户回合注入，保持 system 缓存前缀稳定。
- `max_steps` 触顶后的现场总结。

### Changed

- Provider 专属字段不再泄漏进下一轮消息历史。
- `grep` 可搜索 GBK / Latin-1 等非 UTF-8 文本。
- 模型调用异常和 Ctrl+C 不再掀翻整个会话。

## [0.1.0] - 2026-06-20

第一个进入 git 历史的最小可运行内核。

### Added

- `LLMClient` Provider 抽象和 OpenAI-compatible 适配器。
- `@tool` 注册表与自动 JSON Schema。
- 统一 `ToolResult` / `ToolError`。
- 工具执行管道：参数解析、校验、确认、异常和输出截断。
- 带 `max_steps` 的 Agent 循环与 CLI。
- `MockClient` 和离线测试。

[Unreleased]: https://github.com/kestiny18/Noval/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/kestiny18/Noval/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/kestiny18/Noval/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/kestiny18/Noval/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/kestiny18/Noval/releases/tag/v0.1.0
