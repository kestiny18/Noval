# Changelog

This file records user-visible Noval milestones. The project remains pre-1.0,
so public contracts may continue to evolve.

## [Unreleased]

### Added

- Optional JSON-safe `GoalContract` values with explicit objective, scope,
  authority notes, named acceptance criteria, source binding, and evidence
  freshness requirements.
- Safe `ActionReceipt` records for every tool attempt, exposing execution risk,
  outcome, timestamps, argument keys, and a digest of already-redacted output
  without retaining argument values or raw tool content.
- Criterion-bound `VerificationResult` and `CompletionReport` contracts,
  idle-only host verification APIs, evidence-aware public terminal status, and
  live verification/completion event payloads.
- Explicit `hook:<id>` Stop Hook verification mapping and offline goal/evidence
  Eval cases for missing, passing, failed, unknown, and stale evidence.
- ADR-0005 plus English-first Application API and Hook evidence documentation
  with first-class Simplified Chinese entry points.

### Changed

- Advanced the recoverable task sidecar to schema v2 while retaining read
  compatibility with schema-v1 semantic snapshots and leaving canonical
  Session schema v2 unchanged.
- Kept the semantic completion judge as a separately labeled visible-reply
  assessment. For explicit goals it can no longer upgrade or override missing,
  stale, unknown, or failed contracted evidence.
- Extended Application API schema v1 additively: existing goal-less requests
  remain valid and retain their lightweight behavior.

## [0.11.0] - 2026-07-20

### Changed

- Adopted the strong-model, thin-harness doctrine: the model chooses task
  strategy while Noval owns authority, execution, durable state, and configured
  validation boundaries.
- Replaced the coding-specific default system prompt with the domain-neutral
  `principle-guided-v2` operating contract: decision principles remain
  non-sequential, external content is evidence rather than authority, and
  computation is available as a general reliability method without making the
  core coding-specific.
- Made English the canonical public documentation language with first-class
  Simplified Chinese entry points and preserved the v0.1-v0.10 Chinese design
  ledger as historical context.
- Standardized source comments, runtime prompts, CLI output, diagnostics,
  examples, tests, and Eval assets on clear English contracts.

### Added

- Root `.gitignore` plus `.llmignore` discovery filtering for built-in listing,
  globbing, grep, and filename suggestions, with traversal-time directory
  pruning and explicit-read behavior preserved.
- Public philosophy, canonical design summary, ADR index, and ADR-0004 for
  principle-guided, invariant-enforced autonomy.
- Structured GitHub Issue Forms, pull-request template, security policy,
  Dependabot configuration, CodeQL workflow, package-build smoke checks, and a
  non-publishing release verification workflow.
- A stable aggregate `CI gate` and a protected-main pull-request delivery
  policy for repository changes.

## [0.10.0] - 2026-07-19

### Added

- Headless/Application API with `NovalRuntime`, isolated `AgentSession`
  handles, JSON-safe options/results/events/errors, per-session persistence
  selection, and native dependency factories.
- Concurrent execution across Sessions with immediate same-session
  `session_busy`, fail-closed serializable permission handlers, live lifecycle
  events, cooperative cancellation, and owned-subprocess termination.
- Cross-platform advisory writer leases for persistent Sessions, reported as
  retryable `session_locked` conflicts and released on close.
- Append-only request provenance journals and `inspect_request`, covering
  canonical messages, tool schemas, checkpoint source, provider identity, and
  adapter-owned credential-free request rendering without opaque thinking.
- Golden API v1 contracts and parallel isolation coverage for workdirs,
  messages, permissions, Hooks, Skills, MCP, events, usage, failures, and
  process state.

### Changed

- The CLI is now a host adapter over the Application API and no longer
  assembles `Agent` dependencies or changes process cwd.
- Runtime logs carry Session, Turn, and Request correlation ids. CLI enables
  runtime logging explicitly; embedded runtimes leave host logging unchanged
  by default.

### Fixed

- Request journals recursively redact credentials at the persistence boundary
  and deduplicate repeated messages, tool schemas, and adapter payload objects
  without losing complete request reconstruction.
- Session close, permission updates, and turn startup now use one atomic
  lifecycle transition, preventing turns from starting against a closed Store
  or released writer lease.
- Provider request start, completion, and failure logs now carry their request
  correlation id without recording message, response, or exception contents.

## [0.9.0] - 2026-07-17

### Added

- Provider-neutral `ConversationMessage` model with typed text, tool-call, and
  tool-result blocks, plus adapter-owned opaque replay state and assistant
  provenance.
- Anthropic Messages adapter as the optional `noval[anthropic]` extra, covering
  text, system prompts, client tools, multiple tool calls, tool errors, usage,
  and thinking/redacted-thinking replay.
- Normalized `ProviderIdentity` and safe `ProviderError` metadata across
  adapters, without exposing raw SDK responses to the core.
- Static architecture and adapter-equivalence tests that keep Provider wire
  keys out of Agent, Context, Session, Task, and Usage.

### Changed

- `LLMResponse` now carries one canonical assistant message instead of parallel
  content/tool-call/wire-message representations; Provider tool definitions are
  reduced to name, description, and JSON schema.
- Session and context checkpoint persistence use schema v2 canonical data. v1
  Sessions are listed as incompatible and rejected without migration or
  mutation; old checkpoints are not reused.

- Build, compile, test, lint, and format-check requests no longer imply permission to
  edit source files, dependency versions, lockfiles, build configuration, or
  project settings; failures are diagnosed read-only until the user explicitly
  authorizes a repair.
- The completion judge now describes missing proof as absent from the visible
  final response instead of making claims about tool executions it cannot see.

### Fixed

- `run_bash` now reports non-zero exits as tool errors, allowing error-matching
  PostToolUse Hooks and the agent loop to observe command failures correctly.
- Bash-compatible shell backends enable `pipefail`, preventing failed build or
  test commands from being hidden by a successful trailing pipeline stage.
- Domain `ToolError` output now passes through the executor's central
  redaction and head-and-tail truncation boundary.

## [0.8.1] - 2026-07-13

### Changed

- Stop Hooks now evaluate every candidate final response; `afterTools` remains
  the explicit filter for projects that only validate after selected tools ran.

### Fixed

- Reject non-finite Hook timeouts such as `NaN` and `Infinity` during config
  validation instead of deferring failure to process execution.

## [0.8.0] - 2026-07-13

### Added

- Project-scoped lifecycle Hooks configured through grouped
  `<workdir>/.noval/hooks.json` `PreToolUse`, `PostToolUse`, and `Stop` arrays.
- CommandHook execution through the existing permission controller and
  `ProcessRuntime`, with deterministic ordering, timeout, redaction, truncation,
  config-fingerprint approvals, and exit-code or structured JSON outcomes.
- Stop validation feedback that can withhold a candidate final response, return
  diagnostics to the model for repair, and prevent unchanged failure loops.

### Changed

- The executor now exposes a guarded pre-execution callback after target-tool
  approval and before invocation, and records whether the tool actually ran.

## [0.7.0] - 2026-07-10

### Added

- Versioned roadmap for post-`0.5.0` work, including the safety hotfix line,
  path-jail/confinement, unified subprocess execution, hooks, Provider-neutral
  canonical messages, and `v1.0.0` embedding criteria.
- Provider request timeout and retry settings (`request_timeout_seconds`,
  `request_max_retries`) so a hung model API call does not block the agent loop
  indefinitely.
- MCP client MVP: discover user/project `mcpServers` config from
  `~/.noval/mcp.json` and `<workdir>/.noval/mcp.json`, inject only a lightweight MCP
  server index, refresh config changes at turn boundaries, and expose guarded
  `list_mcp_servers` / `list_mcp_tools` / `call_mcp_tool` tools for stdio MCP
  servers.
- Central tool-output redaction before model/session persistence for common
  credential shapes such as password, secret, token, privateKey, appSecret,
  accessKey and webhook values.
- MCP tool-call output normalization: JSON text content is parsed into
  structured content so the model does not receive JSON wrapped inside a JSON
  string.
- Path-jail v1 for in-process file tools via `ConfinementPolicy`, with default
  read/write roots at `workdir` and an explicit expanded-read policy for
  embedders.
- Unified `ProcessRuntime` for shell commands, Skill scripts, environment
  probes, and MCP stdio preparation, with an injectable sandbox backend,
  explicit capability reporting, and honest `NoSandbox` fallback.
- Per-invocation `--sandbox auto|required|off` policy. Required mode fails
  closed before configuration or session state is loaded when no hard backend
  is available.
- Linux Bubblewrap hard-sandbox backend with an actual namespace usability
  probe, explicit read/write mounts, fresh `/tmp` and PID namespace, optional
  network isolation, and dedicated CI escape tests.
- Per-invocation `--sandbox-network inherit|deny` policy for hard backends.

### Changed

- Project-level MCP config now lives at `<workdir>/.noval/mcp.json` instead of
  the repository root `.mcp.json`, matching the user-level `mcp.json` file name
  and keeping Noval project metadata under `.noval/`.
- Completion judge calls now run only after a turn actually used tools, avoiding
  unnecessary judge-model spend for direct conversational replies.
- Tool-output redaction keeps obvious source-code references visible, such as
  type annotations and environment-variable lookups, while still redacting
  concrete credential values.
- Subprocess tools now use explicit argv with `shell=False`; long-lived MCP
  transports use the runtime's preparation path while retaining the official
  SDK lifecycle.

### Fixed

- `run_bash` risk assessment now treats newline and carriage-return separators
  as command boundaries, preventing read-only command chains from hiding a later
  mutating command.
- Bubblewrap detection now reports the Ubuntu/AppArmor user-namespace profile
  prerequisite when loopback setup is denied; Linux CI loads the targeted
  distro profile instead of disabling AppArmor globally.

### Security

- File tools now reject paths that resolve outside their allowed read/write
  roots, including parent-directory escapes, absolute paths outside `workdir`,
  symlink escapes, new-file parent escapes, and `glob` / `grep` result escapes.
- MCP servers no longer receive the complete parent-process environment. They
  inherit the SDK safe baseline plus only explicitly configured server env.

## [0.5.0] - 2026-07-08

### Added

- 持久化增量上下文压缩：按 Token 预算生成 checkpoint，恢复时复用摘要与原始尾部，并保留完整 Session 作为唯一真相源。
- 仓库级 context Eval 脊柱：最小语义/对抗用例、结构与敏感信息硬检查、离线重放及 Markdown/JSON 报告。
- Task completion judge MVP: the main model executes and interacts with users,
  while an independent `judge_model` receives only the last three unique user
  inputs plus the final visible assistant reply and returns a structured
  completion verdict. Judge calls are tracked with a separate usage purpose.
- Task Eval assets for offline task-completion judge contract replay, covering
  recent input selection and `completed` / `incomplete` / `waiting_user` /
  `blocked` / `uncertain` verdict persistence.
- Skill loading/runtime MVP: discover Claude/Codex/Cursor-style `SKILL.md` directory
  packages from user and project skill roots, inject only a lightweight system
  index, and expose tools for loading Skill bodies, resources, and guarded
  scripts. Cursor `.cursor/skills` roots are supported; `.cursor/rules`
  directories are intentionally not scanned.
- Skill 运行态刷新：会话内新增、删除或修改 Skill 时，在用户回合边界用内存快照检测变化，并以临时上下文提示模型，不写入 session / settings / checkpoint。

### Changed

- 上下文压缩 prompt v2 明确禁止凭据原值进入 checkpoint，并要求保留用户否决/暂停决策且不得恢复为待办。
- Task completion judge prompt v3 明确区分 `current_user_input` 与 `context_user_inputs`，避免把最近三条输入误判为本轮全部待办。
- `list_skills` 输出改为紧凑分页结构，并支持 `query` / `source` 过滤和 `skill` 参数别名。

### Fixed

- `read_file` 改为行感知输出预算，避免 executor 通用截断导致模型误以为已读完整文件。
- 连续分片读完整个文件后，read-tracker 会升级为 full read，允许后续 `edit_file` 正常修改。
- 已 full read 的文件再次局部读取时，不再把文件状态降级为 partial。

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

[Unreleased]: https://github.com/kestiny18/Noval/compare/v0.11.0...HEAD
[0.11.0]: https://github.com/kestiny18/Noval/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/kestiny18/Noval/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/kestiny18/Noval/compare/v0.8.1...v0.9.0
[0.8.1]: https://github.com/kestiny18/Noval/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/kestiny18/Noval/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/kestiny18/Noval/compare/v0.5.0...v0.7.0
[0.5.0]: https://github.com/kestiny18/Noval/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/kestiny18/Noval/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/kestiny18/Noval/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/kestiny18/Noval/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/kestiny18/Noval/releases/tag/v0.1.0
