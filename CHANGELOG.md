# Changelog

本文件记录 Noval 可对外识别的版本里程碑。项目尚处于 `0.x` 阶段，接口可能继续演进。

## [Unreleased]

目标版本：`v0.3.0`。当前功能已实现，等待真实任务验证和收敛。

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

[Unreleased]: https://github.com/kestiny18/Noval/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/kestiny18/Noval/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/kestiny18/Noval/releases/tag/v0.1.0
