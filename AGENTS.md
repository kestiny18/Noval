# Noval — 通用 Agent 小核心

一个**与具体场景解耦**的通用 agent 核心（不局限于 coding）。目标是一个能长期演进、工程化可用的小内核，而不是一次性原型。

详细的「为什么这么设计」见 [DESIGN.md](DESIGN.md)。本文件只放**必须遵守的约束**。

---

## 一句话核心理念

> 工具执行框架是模型与真实世界之间的「感官接口」。模型只能透过工具的返回结果感知世界，因此**框架的质量上限 = agent 的能力上限**。每一个横切关注点（错误、截断、超时、确认、日志）都要在执行层统一处理，而不是散落在各个工具里。

## 三条不可破坏的接缝（架构底线）

1. **Provider 抽象** (`client.py`)：循环代码永远不直接依赖 OpenAI/DeepSeek SDK。一律走 `LLMClient` 接口，具体厂商是可替换的适配器。
2. **工具注册表** (`tools.py`)：加工具 = 写一个带类型注解的函数 + `@tool` 装饰器。绝不在循环里手写 if/else 分发。
3. **执行与循环分离** (`executor.py` ↔ `agent.py`)：`agent.py` 只负责「编排对话」，单次工具调用的全部细节由 `executor.py` 的管道负责。

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
- **项目记忆**：启动时读 workdir 的 `AGENTS.md`（开放标准，回退 `CLAUDE.md`），用 `<project_instructions>` 包安全边界后注入 system prompt；**只读不写**。system 顺序按稳定性：人设 → 环境 → 项目记忆（见 DESIGN 决策 14）。
- **可观测性**：禁止 `print(整个 response)`。每次工具调用记结构化 trace（tool / args / 耗时 / is_error / truncated）。
- **可测试性**：`LLMClient` 必须能被 mock，使整条 agent 循环可在不联网、不烧钱的情况下测试。
- **循环安全**：agent 循环必须有 `max_steps` 上限，达到上限优雅停止。
- **密钥**：永不硬编码 api_key，一律从环境变量 / 配置读取。

## 配置

- 路径：`~/.noval/settings.json`，只放**全局稳定偏好**（model / 阈值 / 日志与会话目录等）。agent 人设 `system_prompt` 属**代码**（`agent.DEFAULT_SYSTEM_PROMPT`），不进 settings.json；权限是会话状态，也不进 settings.json。
- 加载策略：内置默认值 ← 文件覆盖。文件缺失要能用默认值正常启动；错类型要清晰报错，不静默跑歪。
- **per-invocation 状态不进 settings.json**：工作目录由 `--workdir` 显式参数决定，否则用 `os.getcwd()`，挂在 Agent 实例上（多进程各管各的，互不覆盖）。
- **工具重名默认 raise**（fail-fast，注册表即模型的感官，不静默覆盖）；有意覆盖须 `@tool(override=True)`。

## 验收标准（判断框架好坏的唯一尺子）

> 加第 10 个工具时，你**只需写那个工具的核心逻辑**，完全不用再操心错误、超时、截断、确认、日志、schema。

## 目录结构（刻意保持最小，勿过早拆分）

```
noval/
  config.py     # 读 ~/.noval/settings.json + 默认值合并
  client.py     # LLMClient 接口 + DeepSeek/OpenAI 适配器   [接缝1]
  tools.py      # 框架：ToolResult/ToolError/Context/@tool 注册表   [接缝2]
  builtins.py   # 内置工具实现（read/write/edit/bash/ls/grep/glob）
  executor.py   # 执行管道（含 Context 注入）                 [接缝3]
  permissions.py # 会话级权限状态与唯一决策入口
  agent.py      # 对话循环(含 max_steps) + CLI 入口
```

- `tools.py` 是框架，`builtins.py` 是工具实现，二者分离（`__init__.py` 导入 builtins 触发注册）。
- 工具数到 ~8 个之前不要把 `builtins.py` 再拆成一文件一工具。
- **Context 注入**：工具首参声明 `ctx: Context` 即可拿到 workdir + read-tracker，该参数不进 schema。
- **文件工具状态机**：改前须先 read、检测外部改动；三工具共用 `_resolve` 保证路径 key 一致（见 DESIGN.md 决策 10/11）。
