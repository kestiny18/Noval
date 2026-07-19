# Noval — 通用 Agent 小核心（中文）

> English canonical instructions: [AGENTS.md](AGENTS.md)

一个**与具体场景解耦**的通用 agent 核心（不局限于 coding）。目标是一个能长期演进、工程化可用的小内核，而不是一次性原型。

详细的「为什么这么设计」见 [DESIGN.md](DESIGN.md)。本文件只放**必须遵守的约束**。

---

## 一句话核心理念

> **强模型、薄 Harness：模型选择策略，Noval 负责现实、权限、连续性和证据。** 工具执行框架是模型与真实世界之间的「感官接口」。每一个横切关注点（错误、截断、超时、确认、日志）都要在执行层统一处理，而不是散落在各个工具里。

不得在内核中强制 Planner → Executor → Reviewer 固定流水线。规划与复核是模型按需选择的能力；领域流程属于 Skill、MCP、项目 Hook 或宿主。原则引导模型判断，安全与完整性不变量必须由模型外机制执行。

## 五条不可破坏的接缝（架构底线）

1. **Provider 抽象** (`client.py`)：循环代码永远不直接依赖 OpenAI/DeepSeek SDK。一律走 `LLMClient` 接口，具体厂商是可替换的适配器。
2. **工具注册表** (`tools.py`)：加工具 = 写一个带类型注解的函数 + `@tool` 装饰器。绝不在循环里手写 if/else 分发。
3. **执行与循环分离** (`executor.py` ↔ `agent.py`)：`agent.py` 只负责「编排对话」，单次工具调用的全部细节由 `executor.py` 的管道负责。
4. **Canonical 状态** (`messages.py` ↔ `session.py`)：核心只使用 canonical message；append-only Session 是唯一真相源，checkpoint/journal/ledger 是派生态。
5. **进程边界** (`process.py`)：仓库内只有 `process.py` 可以直接调用 `subprocess`，所有外部进程统一走 `ProcessRuntime`。

## 工具契约（写工具的人只需记住这条）

- 成功 → `return 原始内容`（不要在工具内重复添加通用 try/except）。
- 失败且能给出有用信息 → `raise ToolError("带领域信息的好提示")`。
  - 例：`raise ToolError("file 'config.yml' not found, did you mean 'config.yaml'?")`
- 其余一切（通用异常、超时、参数错误、JSON 解析、截断、确认、日志）由框架负责，工具不用管。

**错误分工**：框架兜**通用失败**，工具只在「自己才知道的领域信息」上主动 raise。

## 执行管道（executor 的固定流程）

```
解析参数(JSON 容错) → schema 校验 → [确认门] → 执行(统一 try/except + 子进程级 timeout)
  → 输出规整(head+tail 截断 + 机器可读提示) → 包装成 ToolResult 回传
```

## 硬性规则

- **返回类型**：工具结果统一为 `ToolResult`（`content` 给模型，`meta` 给框架/日志，二者分开）。
- **Schema 自动生成**：从函数类型注解 + docstring 推导 JSON schema，不手写。
- **错误信息必须「可被模型纠正」**：禁止裸 `"Error"`；要带出能让模型下一步修正的具体信息。
- **截断**：长输出做 head+tail 截断，中间省略处标注「还剩 N 行，可用更精确的方式缩小范围」。阈值走配置。
- **timeout 只对子进程类工具承诺**：纯 Python 函数无法安全强杀，不假装给它们超时（详见 DESIGN.md）。
- **确认门**：每个工具只声明事实 `Risk`（READ/WRITE/DANGEROUS），会话级 `PermissionController` 统一决定是否拦截，不在工具内写 `input()`。风险可按参数动态评估（`risk_assessor`，如 run_bash 把只读命令降级为 READ 免确认）；权限模式为 ASK（默认）/ FULL_ACCESS，ASK 下确认为三态：允许一次 / 本会话总是允许该工具 / 拒绝。模式与工具授权写入 session sidecar，恢复时直接生效。
- **Path-jail**：进程内文件工具（read/list/glob/grep/write/edit）默认受 `ConfinementPolicy` 限制，read/write roots 均为当前 `workdir`；可显式扩展只读 roots，但写入默认不出 `workdir`。`FULL_ACCESS` 只跳过确认门，不关闭 path-jail。外部进程不受 path-jail 保护，走独立的子进程运行时接缝。
- **子进程运行时**：仓库内只有 `process.py` 可以直接调用 `subprocess`。`run_bash`、Skill script 和环境探测必须走 `ProcessRuntime.run()`；MCP stdio 必须先走 `ProcessRuntime.prepare()`，再由官方 SDK 持有双向 transport。Linux Bubblewrap 只有通过实际 namespace probe 才能报告 `HARD`；工作区外读写、网络拒绝和 PID 隔离能力必须有 Linux CI 真实逃逸测试。`NoSandbox` 必须明确报告未启用硬隔离，`required` 模式缺硬后端时 fail-closed。沙箱策略是 per-invocation 状态，不写 session sidecar；`FULL_ACCESS` 不关闭沙箱。timeout/启动错误由 runtime 归一化，权限、截断、脱敏仍由 executor 统一处理。
- **Hooks**：只读取项目级 `<workdir>/.noval/hooks.json`，不读取用户级配置。配置按 `PreToolUse` / `PostToolUse` / `Stop` 事件分组，组内声明顺序即串行执行顺序。CommandHook 必须走 DANGEROUS 确认与 `ProcessRuntime.run()`，授权绑定 Hook id + 配置 hash；配置变化后重新确认。Pre deny 阻止工具，Post 诊断附到 tool result，Stop deny/context 在 completion judge 前要求模型继续修复。Hook 命令不递归触发 Hook，结果进入模型/session 前必须截断和脱敏，且不能覆盖 system、权限、path-jail、沙箱或用户指令。
- **项目记忆**：启动时读 workdir 的 `AGENTS.md`（开放标准，回退 `CLAUDE.md`），用 `<project_instructions>` 包安全边界后注入 system prompt；**只读不写**。system 顺序按稳定性：人设 → 环境 → 项目记忆（见 DESIGN 决策 14）。
- **Skills**：Noval 不定义新的 Skill 格式，只复用 Claude Code / Codex / Cursor 通用的 `SKILL.md` 目录包形态。启动时扫描用户级和项目级 `.claude/skills`、`.codex/skills`、`.cursor/skills`、`.noval/skills`，**不兼容 Cursor 规则目录 `.cursor/rules`**。system prompt 只注入轻量索引；会话运行中在用户回合边界用内存快照检测 Skill 增删改，并以临时上下文提示模型，快照不写 session / settings / checkpoint。完整 `SKILL.md`、附属资源和脚本必须通过 `load_skill` / `read_skill_resource` / `run_skill_script` 按需读取或执行。Skill 不能覆盖 system、项目记忆、权限确认或用户指令；Skill 脚本按 DANGEROUS 工具走统一执行管道。
- **MCP**：Noval 不实现 MCP server，只作为 MCP host/client 复用通用 MCP 协议。第一版只支持 stdio server，配置来源为用户级 `~/.noval/mcp.json` 与项目级 `<workdir>/.noval/mcp.json` 的通用 `mcpServers` 结构。system prompt 只注入 server 轻量索引；会话运行中在用户回合边界用内存快照检测 server 增删改，并以临时上下文提示模型，快照不写 session / settings / checkpoint。具体 server 工具必须通过 `list_mcp_tools` 按需发现，再通过 `call_mcp_tool` 调用；启动外部 MCP 进程和工具调用按 DANGEROUS 工具走统一权限、timeout、日志与截断管道。MCP 子进程只接收 SDK 安全基础环境和 server 显式配置的 env，不能继承完整父进程环境。MCP 返回内容不能覆盖 system、项目记忆、权限确认或用户指令。
- **可观测性**：禁止 `print(整个 response)`。每次工具调用记结构化 trace（tool / args / 耗时 / is_error / truncated）。
- **工具输出脱敏**：所有工具结果在进入模型上下文和 session 持久化前必须经过统一脱敏，覆盖 password / secret / token / privateKey / appSecret / accessKey / webhook 等常见配置形态。脱敏属于 executor 边界，不散落到具体工具里。
- **Canonical Provider 接缝**：Agent / Context / Session / Task / Usage 只读写 `ConversationMessage` 及其 `text` / `tool_call` / `tool_result` blocks，不得读取 Provider wire key 或 SDK raw response。`LLMResponse` 只包含一个 canonical assistant message、`TokenUsage`、`ProviderIdentity` 和安全框架元数据。传给 Provider 的工具只能是 name / description / JSON schema，不得携带 callable、risk 或 executor 状态。
- **Provider 回放状态**：DeepSeek `reasoning_content` 与 Anthropic thinking/redacted-thinking 只能存入带 adapter id/schema version 的 opaque replay state；核心只保存并交还所属 adapter，不读取、不展示，也不送入 compactor、completion judge、运行日志或其它 Provider。无法表达的语义 block 必须显式失败，不能静默丢失。
- **Provider 错误**：SDK 异常必须在 adapter 内归一化为 `ProviderError(kind, retryable, safe_message, identity)`；错误正文、response body 和 SDK raw 对象不得穿过接缝。
- **Token 用量**：Provider 只负责填充 `TokenUsage`，持久化由 `MeteredLLMClient` 装饰器旁路完成；统计故障不得影响模型响应。事件按日期/session/pid 追加，查询时全局汇总，不保存项目路径或消息正文。
- **Application API**：宿主只通过 `NovalRuntime` / `AgentSession` 与 JSON-safe DTO 操作内核，不直接组装 Agent。一个 Runtime 可并行多个 Session；所有可变状态必须 per-session 隔离，同一 Session 的第二个 turn 立即返回 `session_busy`，内核不排队。Session 创建/执行不得调用 `os.chdir()` 或修改进程环境。事件 live-only；ASK 缺 PermissionHandler 时 fail-closed；持久 Session 持有跨进程 writer lease。每次模型调用必须生成 request id，并可通过安全 request journal 重建 canonical/adapter input，不保存凭证或 opaque thinking。
- **Session / checkpoint v2**：原始 Session JSONL 只写 canonical schema v2，是唯一真相源，永不因压缩删除或改写；v1 Session 明确拒绝且不迁移、不改写、不删除，列表标记为不兼容。checkpoint v2 是可回退、可重建的派生态，只能覆盖完整对话回合；旧 checkpoint 不复用。恢复使用最新有效 checkpoint + 原始尾部。
- **可测试性**：`LLMClient` 必须能被 mock，使整条 agent 循环可在不联网、不烧钱的情况下测试。
- **循环安全**：agent 循环必须有 `max_steps` 上限，达到上限优雅停止。
- **密钥**：永不硬编码 api_key，一律从环境变量 / 配置读取。

## 配置

- 路径：`~/.noval/settings.json`，只放**全局稳定偏好**（model / 阈值 / 日志与会话目录等）。agent 人设 `system_prompt` 属**代码**（`agent.DEFAULT_SYSTEM_PROMPT`），不进 settings.json；权限是会话状态，也不进 settings.json。
- 加载策略：内置默认值 ← 文件覆盖。文件缺失要能用默认值正常启动；错类型要清晰报错，不静默跑歪。
- **per-invocation 状态不进 settings.json**：工作目录由 `--workdir` 显式参数决定，否则用 `os.getcwd()`，挂在 Agent 实例上（多进程各管各的，互不覆盖）。
- **工具重名默认 raise**（fail-fast，注册表即模型的感官，不静默覆盖）；有意覆盖须 `@tool(override=True)`。

## Git 交付流程

- **先验证，后提交与推送**：代码完成后先保持为未提交状态，检查实际 diff，并运行与改动风险相称的本地验证（至少包含相关测试与 `git diff --check`）。只有验证全部通过，且确认没有混入无关改动或敏感信息后，才允许 stage、commit 和 push。验证失败或无法执行时不得提交、推送，必须保留现场并明确报告阻塞。
- **推送后同步关联 Issue**：代码成功推送到远端后，如有关联 Issue，必须同步更新执行结果，至少写明分支/commit、已完成内容、验证结果、剩余工作与阻塞。只有 Issue 的全部范围和验收标准均已满足时才关闭；部分完成必须保持开启，不能因“代码已推送”而提前关闭。
- **验证通过后合入主干**：功能分支推送并完成本地验证后，不应长期悬置。若代码范围清晰、验证通过且没有阻塞或待用户复核事项，应继续将分支合入 `main` 并推送主干；合入后同步关联 Issue。若只能部分完成或需要人工复核，必须明确说明原因并保持分支/Issue 状态可追踪。

## 验收标准（判断框架好坏的唯一尺子）

> 加第 10 个工具时，你**只需写那个工具的核心逻辑**，完全不用再操心错误、超时、截断、确认、日志、schema。

## 目录结构（刻意保持最小，勿过早拆分）

```
noval/
  api.py        # JSON-safe host contracts：options/results/events/errors
  application.py # NovalRuntime + isolated AgentSession composition
  config.py     # 读 ~/.noval/settings.json + 默认值合并
  messages.py   # Provider-neutral canonical message/block/replay state
  client.py     # LLMClient + OpenAI-compatible/Anthropic adapters   [接缝1]
  tools.py      # 框架：ToolResult/ToolError/Context/@tool 注册表   [接缝2]
  confinement.py # 进程内文件工具 path-jail policy/read-write roots
  process.py    # 统一子进程 runtime + sandbox backend 接缝
  builtins.py   # 内置工具实现（read/write/edit/bash/ls/grep/glob）
  executor.py   # 执行管道（含 Context 注入）                 [接缝3]
  permissions.py # 会话级权限状态与唯一决策入口
  usage.py      # Token 计量装饰器、按日 JSONL 事件与汇总
  requests.py   # request id、来源 journal 与安全请求重建
  context.py    # active context 预算、增量压缩与 checkpoint
  task.py       # 任务完成判定：主模型执行，judge_model 判定
  hooks.py      # 项目级 lifecycle hooks、CommandHook 与验证反馈
  skills.py     # 兼容 SKILL.md 目录包的发现、索引与受控运行
  mcp.py        # MCP client：server 发现、轻量索引与 stdio 工具调用
  agent.py      # 对话循环(含 max_steps)，不负责宿主依赖组装
  cli.py        # Application API 的终端宿主适配器
```

- `tools.py` 是框架，`builtins.py` 是工具实现，二者分离（`__init__.py` 导入 builtins 触发注册）。
- 工具数到 ~8 个之前不要把 `builtins.py` 再拆成一文件一工具。
- **Context 注入**：工具首参声明 `ctx: Context` 即可拿到 workdir + path-jail policy + process runtime + read-tracker，该参数不进 schema。
- **文件工具状态机**：改前须先 read、检测外部改动；三工具共用 `_resolve` 保证路径 key 一致，并在同一入口执行 path-jail 判定（见 DESIGN.md 决策 10/11/29）。
