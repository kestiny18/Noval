# Noval 设计记录（DESIGN / ADR）

本文件记录 Noval 内核的**思考过程与关键决策的「为什么」**。AGENTS.md 放结论性约束，本文件放推理和权衡，供日后回溯。

---

## 项目缘起

从一个最基本的 agent 循环（OpenAI 兼容 API + 一个 `read_file` 工具 + 手写工具分发）出发，目标是演进成一个**通用的 agent 小核心**，与具体应用场景解耦。

最初版本暴露出的问题，催生了下面这些设计决策：
- api_key 硬编码在源码里（安全）。
- `result is None` 同时表达「没找到工具」和「工具返回 None」（语义重载 bug）。
- 内层工具循环 `while True` 无上限（可能无限调用、烧钱）。
- `json.loads` 与工具执行无异常保护（模型给错参数就崩）。
- 每轮 `print(整个 response)`，正文被日志淹没（可观测性差）。

---

## 起点实验：手动模拟一遍 tool calling

在写任何工具分发之前，先用纯 Prompt 把 tool calling 手搓了一遍：约定模型用 `get_weather(<地点>)` 发起「调用」，**由我本人扮演工具后端**，把结果（「微风小雨」）喂回去，模型再消费这个返回值产出最终答复。模型完全照做——这**不是 bug，是一次刻意的机制拆解**：把平时被 API 黑盒掉的过程亲手走一遍。

价值在于它第一次把 tool calling 的三个角色摊开：

| 角色 | 实验里是谁 | 真机里是谁 |
|---|---|---|
| 决定调用 + 发出调用 + 解读结果 | 模型 | 模型（这层永远是模型的活） |
| **真正执行、把结果递回去** | **我（人肉后端）** | **`executor.py`** |
| 调用与结果如何来回传递（协议） | 普通聊天文本 | `tool_calls` / `role:tool` / `tool_call_id` |

由此引出贯穿整个内核的主线：**Noval 要做的，就是把那个「人肉执行器」工业化成 `executor.py`。** 从人肉到管道，截断、超时、确认门、可观测性，无非都是「人肉时下意识在做、换成代码后必须显式处理」的横切关注点。

这个实验顺带钉死一条边界：光靠提示词约定的「工具」始终是**模拟**，只有真正进了 `tools` 列表才会触发真机的 `tool_calls`——后续问 `agent.py` 那次，read_file 返回 `finish_reason='tool_calls'` 真的开火，正是对照。

---

## 决策 1：工具执行框架是「感官接口」，是整个内核的地基

这是最重要的判断，有两层：

**第一层（代码组织）**：工具一多，如果每个都采用不同的返回类型、异常策略、超时和输出方式，接口会很快失去一致性。统一框架让「加工具」退化成「填一个函数」。这是标准后端抽象。

**第二层（更关键，直接决定 agent 智不智能）**：模型只能透过工具返回结果感知世界。框架定义了「模型能看到什么、看到的质量如何」，而模型的下一步决策完全建立在此之上：
- **错误格式统一且信息丰富 → 模型能自我纠错。** `"Error"` 让模型抓瞎；`"Error: file 'config.yml' not found, did you mean 'config.yaml'?"` 让模型立刻修正。框架统一错误处理 = 统一提升所有工具的「可纠错性」。
- **输出截断策略 → 决定模型会不会被淹没。** 把 5000 行文件全塞回去，注意力被冲散、烧钱、可能撑爆窗口。统一截断 + 「还有 N 行，可用更精确方式缩小范围」的提示，反而引导模型用更聪明的方式探索。
- **超时与确认门 → 决定 agent 安不安全、卡不卡死。** 这些横切关注点散落在各工具里必然有遗漏。

**结论**：框架是模型与真实世界之间那层「感官接口」，它的质量上限就是 agent 的能力上限。所以它是地基——不是怕乱（那只是后果），而是因为后续每一个工具、每一分聪明程度都站在它之上。

---

## 决策 2：工具结果用结构化的 ToolResult，不用裸字符串

「模型看到什么」和「系统记录什么」必须分开。模型只看 `content`；框架需要 `is_error / truncated / meta(耗时、原始长度…)` 来做日志、截断决策、未来的重试。这是后续可观测性的前提。

---

## 决策 3：错误处理两层分工

- **框架兜通用失败**：JSON 解析失败、参数缺失、工具抛异常、超时——每个工具都一样，统一处理。
- **工具兜领域错误**：`"did you mean config.yaml?"` 这种信息只有工具自己知道，框架不可能产生。

因此工具契约定为：**成功 return 原始内容，失败 raise ToolError(好消息)**，框架 catch 一切并统一包装。原始版本里 read_file 自己 `try/except 返回 "Error reading file"` 的写法反而要去掉——让异常交给框架处理；只在能给更聪明提示时才主动 raise。

---

## 决策 4：timeout 的诚实校正（重要的现实约束）

**无法安全地给进程内的纯 Python 函数加超时。** Python 不能强杀线程；`signal.SIGALRM` 在 Windows 上不存在。所以：
- **子进程类工具**（run_bash、外部命令）：用 `subprocess.run(timeout=N)`，超时能真正杀掉。✅
- **纯 Python 工具**（read_file 等）：默认信任其够快。真要防御得丢进 `ProcessPoolExecutor`，复杂度高一个量级，最小核心不上。

**设计取向**：超时是**工具的属性**，不是框架的全局承诺。不在文档里写「框架统一超时」然后对一半工具其实是假的——拒绝 cargo-cult。

---

## 决策 5：确认门 = 会话权限与框架的接缝

每个工具只声明 `Risk`（READ / WRITE / DANGEROUS）这一客观事实；是否拦截由会话级 `PermissionController` 决定。ASK 是新会话默认：READ / WRITE 直接执行，DANGEROUS 交互确认；FULL_ACCESS 跳过确认。模式和“本会话总是允许”的工具属于可变会话状态，写入 sidecar 并随恢复直接生效，不进入全局 `settings.json`。确认逻辑仍是执行层统一的横切关注点，绝不写成散落在工具里的 `if input("确定?")`。

---

## 决策 6：Schema 自动生成（对得起验收标准）

验收标准是「加第 10 个工具只写核心逻辑」。最彻底的兑现方式是用装饰器从函数**类型注解 + docstring** 自动推导 JSON schema：

```python
@tool(risk=Risk.READ)
def read_file(path: str) -> str:
    """读取指定路径的文件内容。"""
    return Path(path).read_text(encoding="utf-8")
```

代价是装饰器内部有反射「魔法」。权衡后选择自动生成——省事的收益远大于一点点内部复杂度，且这复杂度被封装在框架里，工具作者无感。

---

## 决策 7：Provider 接口现在就抽

目标是通用 agent，换模型/厂商是迟早的事。在循环写死之前先留 `LLMClient` 接口、只实现一个 DeepSeek 适配器——这条接缝早留几乎零成本，晚留要动循环核心。

---

## 决策 8：可观测性 + 可测试性 = 「工业级」的另一半

- **可观测性**：结构化 trace 取代 `print(整个 response)`。debug agent 的本质是「模型为什么做了那个决策」，而决策取决于它上一步看到了什么，必须能回放。
- **可测试性**：`LLMClient` 抽象后，测试塞一个「返回预设 tool_call 脚本」的 mock，整条循环可离线、零成本测试。这是原型走向工程化的分水岭。

---

## 决策 9：重名 fail-fast + 工作目录是 per-invocation

**工具重名默认 raise，不 last-wins。** 注册发生在加载期（非运行期），所以重名是「启动时确定性报错」而非「运行中崩溃」。注册表定义模型可见的工具接口；last-wins 会让 `read_file` 的实际实现取决于 import 顺序，形成难以察觉的错误。有意覆盖（插件替换内置）须显式 `@tool(override=True)`：歧义永不静默。

**工作目录不进 settings.json，由本次启动决定。** workdir 是 per-invocation 状态：在项目 A 启动就该是 A。塞进全局共享的 `settings.json` 表达不了「同时跑 N 个进程、各指各的项目」，且一旦可写还会引入文件级竞态。归属正解：per-invocation 的状态来自 per-invocation 的来源——

```
--workdir 显式参数  →  否则 os.getcwd()（在哪启动就是哪）
```

解析成绝对路径挂在 `Agent` 实例（内存）上，启动时 `os.chdir` 一次，让文件工具与将来 `run_bash` 的子进程 cwd 都落在此。类比 git：`~/.gitconfig` 放全局偏好，「当前在哪个仓库」从 cwd 来，从不写进 gitconfig。

---

## 决策 10：上下文注入（Context）—— 工具与环境之间的显式接缝

文件/搜索/shell 工具都需要 workdir（解析相对路径、子进程 cwd、未来的 path-jail）。我们没用「全局 cwd」（那等于把刚否决掉的隐式全局状态请回来），而是引入 `Context`：

- `@tool` 识别「首参声明为 `ctx: Context`」的工具，置 `wants_context=True`，**该参数不进 schema**（模型看不到）。
- executor 给这类工具把 `context` 作为首个位置参注入；纯函数工具照旧无感。
- `Context` 携带 `workdir` + `read_state`（read-tracker），由 Agent 每会话构造一次。

这是「横切关注点显式化」的又一处落地：工具要的环境是**传进去的**，不是从全局摸的。

## 决策 11：文件工具共享状态机

成熟 coding agent 的文件工具通常依赖一套共享状态约束：**安全性不只来自各工具的局部逻辑，更来自它们共同维护的文件状态机**。Noval 基于这一原则独立实现了以下机制（见 `builtins.py`）：

- **read-tracker**：`read_file` 把 `{mtime, content, is_partial}` 写进 `ctx.read_state`。
- **改前须先 read**：`write_file`/`edit_file` 改已存在文件前，要求该文件被**完整** read 过（局部读 `is_partial=True` 不算）。
- **staleness 检测**：磁盘 mtime > 上次 read 的 mtime → 拒绝并要求重读。
- **Windows mtime 误报回退**：云同步或安全软件可能修改 mtime 而不改变内容；对 full read 回退**比对内容**，避免误报。
- **写盘后回写 read_state**：让紧接着的 edit 不被自己误判 stale。
- **共用同一个 `_resolve`**：三个工具路径归一化一致，read_state 的 key 才对得上（尤其 Win 的 `/` vs `\`）。

其余借鉴的可纠错细节：read 带行号 + 空文件警告 + not-found「did you mean」；edit 唯一匹配或 `replace_all`、`old==new` 拒绝；glob/grep 按 mtime 排序、排除 `.git`、结果相对化省 token、截断给翻页提示。

**明确不纳入核心**：LSP/诊断、git diff、file-history 备份、技能发现、analytics、smart-quote 归一化、图片/PDF/notebook、UNC 防护。这些属于产品层能力，不是通用内核当前阶段的职责。
**本轮 defer**（记 TODO）：grep 的 `-A/-B/-C` 上下文行 / `multiline` / `type`；用 ripgrep 加速（接口已对齐，可无痛替换）；read 的 dedup 桩；path-jail 边界。

**结果**：横切关注点集中在框架层和共享状态机后，每个具体工具可以保持精简，同时继承一致的错误、安全和状态约束。

## 决策 12：风险在「命令」里，不在「工具」上（真实任务驱动）

拿 Noval 跑了一次真实排障（几十万行 catalina.out 找服务中断根因），暴露三个问题，都已修复：

1. **确认疲劳**：一次任务手敲了约 40 次 `y`，九成是 `grep`/`sed`/`cat` 这种只读命令。根因——`Risk` 挂在**工具**上太粗：`grep` 和 `rm -rf` 都走 run_bash 却共享 DANGEROUS。
   - **修复**：工具可选 `risk_assessor(args) -> Risk`，按**本次参数**动态评估风险。run_bash 解析命令：纯只读管道(grep/sed/cat/head/wc/ls/find…) → 降级为 READ → 自动放行；任何写重定向/`$(...)`/`sed -i`/非白名单程序 → DANGEROUS（宁可多问）。
   - **修复**：确认门改三态 —— 允许一次 / **本会话总是允许该工具**（记在 `PermissionState.approved_tools`）/ 拒绝。剩下的危险命令一次 `a` 即可不再打扰；恢复同一会话时授权继续有效。

2. **耗时指标说谎**：trace 里一条 grep `dur=393s`，其实是把「等用户点 y」算进了执行时间。
   - **修复**：执行计时只包确认门之后的真正执行；批准等待单列 `approval_wait_ms`。可观测性必须诚实。

3. **read_file 啃不动大文件**：旧实现 size 守卫在读任何片段前就 raise，几十万行日志只能全程用 `run_bash sed -n` 替代。
   - **修复**：带 offset/limit 时**流式只读那个行窗口**（不载入整文件），size 守卫只对「整文件读」生效。

**沉淀的判断**：风险粒度要匹配风险的真实所在。把它做成「工具按参数自评 + 框架统一执行确认门」，既保住了「确认逻辑不散落在工具里」，又让 run_bash 这种「一个工具承载千种命令」的情况能精确分级。另一条：**真实任务是最好的设计验证器**——这三个改进点，跑一次真问题就全冒出来了。

**第二轮真实跑测的修正**：风险启发式不能太糙。`ls 2>&1` 里的 `2>&1` 是 fd 复制、`2>/dev/null` 是丢黑洞，都**不是写文件**，旧版把 `>` 一刀切判危险会误弹。正解：先剥掉「安全重定向」(fd 复制 / /dev/null)，剩下的 `>` 才算真写文件。只读白名单也要覆盖 ops 常用命令（zcat/zgrep/md5sum/base64/jq…）。教训：**自动放行的启发式，错判一次（多弹）只是烦，但反过来漏判（把写命令放行）会出事，所以永远向「保守=多问」倾斜，再靠真实命令逐步精修白名单。**

**第三轮（git 探索）**：真实任务里模型常 `cd /path && git log/show/diff`，旧版因 `cd`、`git` 都不在白名单而弹窗。两点精修：①`cd` 无害，加进白名单（链里真危险的命令仍被另判）。②`git` 是**双刃**——`commit/push/reset/clean` 会改状态，不能整体放行；改成**子命令级**判定：只精确放行 `log/show/diff/status/blame/ls-files/...` 等只读子命令，并能跳过 `git -C /path log` 这类全局 flag 取到真子命令。为此把风险判定从「取每段首词」升级为「逐段分类」(`_is_readonly_segment`)，这样才看得到 git 的子命令。安全方向不变：拿不准就 DANGEROUS。

## 决策 13：启动时探测环境，注入 system prompt

真实任务中，模型可能先尝试 Windows 路径，再通过失败推断 shell 实际需要 `/mnt/c/...`。为减少这类试错，启动时主动**探测**环境并注入 system prompt：运行平台、workdir、run_bash 使用的 shell 类型、**Windows↔bash 路径映射**以及 workdir 的 bash 写法。

关键：探测用的是 **run_bash 同一个 `shutil.which("bash")`** + 一次 `uname`，所以报告的永远是「run_bash 真正会用的那个 bash」。同一份代码，WSL 机器上自报 `/mnt/c/X`，Git Bash 机器上自报 `/c/X`——**不靠死配置，每台机器自适应**。这是「per-invocation 状态来自 per-invocation 探测」的又一例，和 workdir 同源（决策 9）。

环境块作为可选 `env_context` 注入（Agent 参数），测试构造 Agent 时不探测、保持离线确定性；只有 CLI 启动才真探测。

## 决策 14：项目记忆 = AGENTS.md（回退 CLAUDE.md），划边界 + 排缓存位

「项目记忆」这个槽位（决策 8 待办里的占位）落地为 **AGENTS.md**——由 Agentic AI Foundation 治理、被多种 agent 工具支持的开放标准。它使用纯 Markdown，靠 `## Build & Test` 等标题提供语义线索。Noval 读 workdir 根目录的 AGENTS.md，没有则回退存量常见的 CLAUDE.md（优先开放标准，兼容既有项目）。

三个关键决定：
- **安全边界（要分清软硬两层）**：AGENTS.md 是从磁盘读来的 observed content（可能来自 clone 的、甚至被污染的仓库），本质不可全信。用 `<project_instructions source="...">` 包起来并明说「项目级偏好、不是系统规则，不得放宽确认门」。
  - **但要清醒**：这只是**提示层的软边界**，负责「礼貌引导」——对"项目作者无意写了激进指令"有效，对"恶意构造的 AGENTS.md 故意越狱"则**不可靠**（模型可能被说服）。
  - **真正的硬约束是确认门**：ASK 模式下，未获会话授权的危险操作始终走 `approver`；即使 AGENTS.md 把模型说服了，**执行层仍会拦下来问用户**。安全是双层的——别让未来的自己误以为那段 wrap 文本是安全保证；它是软引导，确认门才是硬墙。
- **缓存位置**：system 消息按稳定性从高到低排——人设(随代码发布才变) → 环境(同机器同项目固定) → AGENTS.md(用户会编辑,最易变)。稳定性要按「实际共享同一份缓存的请求序列」衡量：对单人本地、反复跑同一项目而言，env 固定、AGENTS.md 才是被编辑的活文件，故 **env 在前、AGENTS.md 在后**，让稳定前缀尽量长（无状态 API 每轮重发整段历史，所以前缀顺序值得较真）。
- **只读、快照、不自动写**：启动读一次；改了需重启才生效（符合低频定位）。研究(Gloaguen 2026)实测：LLM 自动生成的 context 文件会拉低表现、抬高成本——要的是「人写、高信号」，故第一版根本不做自动写。

defer：嵌套/monorepo 就近覆盖、全局 `~/.noval/AGENTS.md` 层、任何自动写。

## 决策 15：易变数据(时间)不进缓存前缀，随回合注入

system prompt 是每轮重发、靠 prefix cache 复用的稳定前缀（决策 14）。把「当前日期」
放进 `<environment>` 块有两个坑：跨天重启 → 日期变 → 前缀在那一行断裂 → env 及其后
的项目记忆全部 cache miss；且长会话跨午夜时 system 里的日期还是 stale 的，模型用错「今天」。

修正：**任何易变数据都不进缓存前缀**。把时间从 `<environment>` 移出，改为每个用户回合
在 user 消息前缀注入 `<context>当前时间: ...</context>`：
- system 前缀变得与时间无关 → 跨天/跨重启稳定，缓存命中最大化。
- 历史里的时间戳是冻结的(过去消息不可变) → 不破后续轮次的前缀缓存；新 user 消息本就是
  新的，加时间戳零缓存代价。
- 每轮刷新「现在」→ 长会话跨午夜也正确（顺带修了 stale bug）。

原则：缓存前缀只放「这台机器这个项目内不变」的东西；「现在」属于这一回合。这与 workdir/
环境/system_prompt 的归属判断同源——东西放在和它生命周期匹配的地方。

## 决策 16：max_steps 提默认 + 触顶时让模型总结现场

真实任务(编译三个 Maven 模块)撞了 max_steps=25：模型当时**还在推进**(正查 parent pom 的
jdk 版本)，却被一句固定的"已停止"生硬截断——25 步攒下的现场信息(JAVA_HOME 在哪、
settings.xml 在哪、卡在 WSL×Windows 工具链)全被丢弃。两点修正：
- **默认 25 → 40**：build/调试类任务本就费步数(探 java→探 maven→找配置→试编译→调 env)。
- **触顶不再返回固定句**：追加一条"不能再用工具了，请总结①已查明事实②卡点③下一步"的提示，
  用**无工具**的一次 complete 强制产出文本，把"白撞的 N 步"变成一份可用的现场报告交还用户。

注意：**没做持久 shell**(每次 run_bash 仍是独立 `bash -c`)。那次真正的墙是「WSL bash 跑
Windows 侧带空格的 Java/Maven」。问题部分来自外部工具链，不应全部归因于框架；而模型已学会把 export 和
命令打包进一次调用，非持久不是卡死的根。持久 shell 是大工程(长驻子进程+输出分帧+逐命令超时)，
留作 backlog，等「跑构建/开发流程」确定为目标场景再做——不为非主因的问题动核心。

## 决策 17：一次外部评审驱动的加固

请另一位 reviewer 通读了代码，挑出几处值得改的（都已落地）：

- **去掉悬空的 `Tool.timeout` 字段**：它定义了但 executor 从不消费，会误导人以为"框架统一超时"。
  真相是 run_bash 用**自己的函数参数 + `subprocess.run(timeout=)`** 真超时（所以进程不会卡死），
  纯函数无法安全强杀（决策 4）。框架级 timeout 字段属"假承诺"，删掉——超时是工具的属性，不是框架的。
- **Ctrl+C 中断"任务"而非"会话"**：`KeyboardInterrupt` 是 `BaseException`，run_cli 的
  `except Exception` 接不住，跑工具时按 Ctrl+C 会掀翻会话。改为 `Agent.send` 内部 catch，
  并**补齐未回填的 tool 响应**（`_answer_pending_tool_calls`）——否则下一轮会因「有 tool_call
  没 tool 响应」被 API 拒绝。这也呼应了"一轮多 tool_call 必须全回填"的协议要求（已补测试）。
- **`config.load` 兜 `JSONDecodeError`**：settings.json 漏个逗号不该是难看的 traceback，
  统一成清晰的 `SystemExit`。
- **澄清安全是双层的**（见决策 14）：`<project_instructions>` 是软引导，确认门才是硬墙。

教训：好的评审不挑"能不能跑"，挑"哪个字段在说谎、哪条异常会穿透、哪层边界被误当成保证"。

**第二批评审**：
- **grep 静默漏掉非 UTF-8 文本**：旧版 `read_text("utf-8")` 对 gbk/latin-1 文本会抛异常被 except
  跳过——"我明明知道有匹配，grep 怎么没找到"。修法：先用 `_is_binary` 跳过真二进制(含 NUL)，
  其余用 `errors="replace"` 读，让非 UTF-8 文本也能搜（对 Windows/gbk 环境尤其重要）。至此全仓
  读文件的编码处理一致（都 `errors="replace"`）。
- **同秒连续 edit 不是 bug**：edit 后回写 read_state，第二次 `_require_fresh_read` 时 mtime 未超
  记录、内容也匹配，故不会误判（已有 `test_edit_then_edit_again_no_false_stale` 覆盖）。更窄的洞
  （外部进程同秒改 + 粗精度 mtime 没前进 → 漏检）确实存在，但 strict `>` 是常见取舍；
  改 `>=` 会让每次 edit 都读全文比对、废掉 mtime 优化，不划算。**保持现状。**

## 决策 18：会话持久化 = 第四条接缝，append-only 日志 + 派生态/持久态分离

内核能跑真实任务后，下一个缺口是**会话退出即蒸发**：长任务断了无法续、跑完无法回看。要做「项目维度的会话持久化」，且必须工业级——抗崩溃、能从 JSON 演进到 DB、存储逻辑绝不散进循环。整套设计围绕一条主线：**把「什么是可重建的派生态、什么是必须落盘的持久态」划清楚**，其余形状都从这条推出来。

### 18.1 存储抽象现在就抽（同决策 7 的判断）

「初期 JSON、后期 DB」在我们的架构语汇里就是教科书级的**接口 + 可替换适配器**，和 Provider 抽象（决策 7）同构。所以立**第四条接缝**：

```python
class SessionStore(Protocol):
    def append(self, msg: dict) -> None: ...      # 追加一条消息(信封由实现包)
    def load(self) -> list[dict]: ...             # 读回 msg 序列(已剥信封)
# 实现：JsonlSessionStore(现在) / SqliteSessionStore(将来)
# 项目级列举单独走 list_sessions(base_dir, workdir)，给 --resume 选择器。
```

`agent.py` **永不直接 `json.dump`**；`Agent` 像注入 `client`/`approver` 一样注入 `store`。换 DB = 写新适配器，循环与 Agent 一行不动。这条接缝早留几乎零成本，晚留要动循环。

### 18.2 派生态 vs 持久态（最关键的边界）

`self.messages` 有四类消息，但**不是都该存**：

- **system**（人设 + env + 项目记忆）是**派生态**：env 探测、workdir、AGENTS.md 都可能跨会话变化，存了反而会把过期环境灌回去；它还是稳定缓存前缀（决策 14/15）。→ **不持久化，恢复时按当前环境重建。**
- **对话轮次**（user / assistant含`tool_calls` / tool）是**持久态**：**一条都不能少**。

踩点警示：若按直觉「只存 user 输入 + LLM 文本回复」，会丢掉所有 `tool` 消息和 `tool_calls`，恢复时拼出「有 tool_call 没 tool 响应」的**非法历史** → 下一轮被 API 拒。这正是决策 17 里 `_answer_pending_tool_calls` 防的同一个坑。**正解：持久化 = `self.messages` 去掉 system 之后的全部。**

### 18.3 文件形状：JSONL 追加日志，不是单 JSON 数组

| | 单 JSON 数组 | JSONL 追加日志 |
|---|---|---|
| 每次保存 | 重写全文 O(n)，一会话 O(n²) | 追加一行 O(1) |
| 进程中途崩溃 | **整文件截断 → 全会话丢** | 最多丢最后一行 |
| 迁移到 DB | 解析整个 blob | 一行 = 一条 row |

配合第 3 点「每次 send 后及时存」，单数组等于**每轮重写全文**——最容易写崩的路径。改成**每会话一个 `.jsonl`、一条消息一行、append-only**。粒度落在**消息级**而非轮次级：工具循环一轮能跑 40 步，崩在第 30 步时那 30 条已落盘，`--resume` 能从半截续；轮次级则整轮丢光。小文件（project.json / sidecar）仍用 JSON，但**写时临时文件 + `os.replace` 原子替换**，不原地覆盖。

### 18.4 信封格式：`{seq, ts, msg}`，时间不进消息体

每条消息的落盘时刻必须存（排序、UI 显示「3 天前」、延迟分析），但**不能塞进 OpenAI 消息 dict**——那样 `ts` 会跟着 replay 进 API，污染 wire 格式甚至被拒。所以**每行是个信封，wire 消息原封包在里面**：

```json
{"seq":7,"ts":"2026-06-23T15:30:12.123+08:00","msg":{"role":"user","content":"<context>...</context>\n\nhello"}}
```

- 加载即剥信封：`messages = [line["msg"] for line in lines]`，`ts`/`seq` 留在框架层，**replay 前剥掉**，模型永远看不到。
- `ts` 用**带时区 ISO8601**（`now().astimezone()`），跨时区/DST 不歧义。
- `seq` 单调序号，为未来压缩（`summary_of:[3,4,5]`）、DB 主键、半截写去重铺路，近乎免费。
- 首行同形信封带 `_meta`：`{"_meta":{"schema_version":1,"session_id":...,"created_at":...,"workdir":...,"model":...}}`，schema_version 就有家了。

**厘清「三种时间」**（这是 Q1/Q3 的统一答案）：① in-band 的 `<context>当前时间>` 烤进 content **给模型**（决策 15，恢复时原样 replay，冻结即真实，别 strip 别 re-inject）；② 信封 `ts` **给框架**，不进 API；③ provider 前缀缓存与持久化**正交**——磁盘永不直接进 API，它先读进 `self.messages` 再发；resume 后第一次请求必然 cache miss（厂商缓存 TTL 才几分钟），冷一次即可，无法也无需避免。

### 18.5 寻址与真相源：全局、hash workdir、无 index

存全局 `~/.noval/sessions/`（不污染 repo、不会误提交私密对话），目录名用 `hash(abs_workdir)`（裸路径太长/含非法字符/跨机冲突），真实路径写进目录内 `project.json` 供反查显示——**project.json 写一次、纯显示元数据，不沾任何可变态**。

**砍掉 index**：它是「跨会话共享的可变状态」，多进程同 workdir 时并发 read-modify-write 会丢更新/损坏（正是不能把它当权威的理由）。改为——**session `.jsonl` 是唯一真相源**，列举 = 扫目录 + `stat` mtime + 读每个文件首行 `_meta`。index 这种派生缓存不能当权威；会话多到扫描慢了再加缓存（避免过早优化，符合「保持最小」）。

### 18.6 标题：sidecar `.meta.json`，既不进 project.json 也不进日志

标题要可改（CLI 先不暴露、UI 一定要），但它是**可变态**，与 append-only 日志天生别扭。落地为**不可变事件日志 vs 可变会话属性分离**（event-sourcing）：

```
~/.noval/sessions/<workdir-hash>/
  project.json                    # {real_workdir, created_at} 写一次,纯显示
  20260623-153012-ab12.jsonl      # 不可变:首行 _meta,其余 {seq,ts,msg}
  20260623-153012-ab12.meta.json  # 可变:{title,pinned,archived,...} 懒创建
```

为什么标题**不进 project.json**：它每项目共享，多进程各改各会话的标题 → `title vs title` 跨进程竞态 = 砍掉的 index 复活；且爆炸半径变大（崩一次全项目标题没）、把 write-once 变 write-often。为什么**不进 `.jsonl`**：append-only 改名要回改已写内容。**sidecar 每会话独占、单进程持有 → 结构上不可能撞**，这是分文件的全部价值。标题缺省从首条 user 消息派生（剥 `<context>` + 截断，不落盘）；`last_active` 取 `.jsonl` 的 mtime，不另存。`pinned`/`archived`/`tags` 将来都进 sidecar，不污染日志。

### 18.7 加载流程复用既有不变量

```
重建 system(当前 env/workdir/AGENTS.md) → 逐行取 msg 拼回 → 跑悬空 tool_call 补全 → 就绪
```

「每个 `tool_call` 必有 `tool` 响应」这条不变量，不只在 Ctrl+C 时维护——**加载时也要跑一遍 `_answer_pending_tool_calls`**（决策 17），因为消息级 append + 中途崩溃可能留下悬空的 assistant。复用现有不变量即可，不再引入另一套拼装逻辑。

### 18.8 鲁棒性细节（工业级必需）

- **schema_version**：一定会改格式，没版本号 = 老文件变读不了的地雷，事后补不上。
- **坏行容忍**：单行解析失败 → skip + warn（不废掉整会话）；崩溃留下的半截尾行同理（呼应全仓 `errors="replace"` 风格）。
- **写失败不掀翻 agent**：磁盘满/没权限 → log 降级，绝不让 `send()` 崩（同「模型异常不掀翻会话」哲学，决策 17）。store 每个写操作包 try/except。
- **懒创建**：第一条成功消息落盘时才建文件，进来啥也没说就退出 → 不留空会话。
- **明文与权限**：对话可能含用户粘贴的密钥/文件内容，明文存盘；至少设 `0600`，并在文档明示——v1 不加密是刻意取舍，但要清醒。

### 18.9 恢复 UX

默认开新会话；`--resume` 进入恢复，项目多会话则 `list_sessions()` 喂选择器（标题 / 最后活跃 / 消息数）。

**原则收尾**：还是那条同源判断（决策 9/15）——**东西放在和它生命周期匹配的地方**。不可变事件进 append 日志、可变属性进 sidecar、可重建的派生态（system）压根不存、易变的「现在」随回合走。各归各位，架构就不会乱。

## 决策 19：Shell 后端与运行日志都属于一次启动的框架状态

Windows 的 `bash` 可能同时指向 Git Bash 和 WSL。若环境探测与工具执行各自调用 `which`，system prompt 描述的路径约定就可能与真正执行不一致。现在启动时只解析一次 `ShellBackend`：Windows 优先 Git for Windows Bash、再回退 PATH 中的 WSL；同一个不可变对象同时注入环境提示与 `Context`，`run_bash` 不再自行重新选择。

运行日志与会话持久化也必须分层：会话日志为了恢复而保存完整消息，运行日志只为排障，默认只落工具名、参数名、耗时、错误和截断状态，并对常见凭据形态二次脱敏。文件按 `日期/session/pid` 隔离，避免多进程共享轮转文件；保留期清理只认框架自有的 `YYYY-MM-DD` 目录。

## 决策 20：权限是会话能力，不是全局风险白名单

旧 `auto_approve` 把“工具有多危险”和“用户给当前会话多少权限”揉成一个全局数组，生命周期与职责都错位。现在拆成两层：工具及 `risk_assessor` 只报告 `Risk`；`PermissionController` 根据 `PermissionState(mode, approved_tools)` 作唯一决策。Executor 只询问控制器，CLI 只通过控制器切换状态，SessionStore 只负责 sidecar 快照，任何一层都不复制策略分支。

权限 sidecar 使用 `{"permissions":{"mode":"ask|full_access","approved_tools":[...]}}`。新会话默认 ASK；恢复时直接使用上次状态。`[a]` 保留，语义是“同一持久会话始终允许该工具”，因此也随恢复保留；FULL_ACCESS 与工具授权彼此独立，切回 ASK 后原授权仍在，只有 `/permissions reset` 才同时清空。sidecar 更新采用读-合并-原子替换，标题与权限不会互相覆盖；新会话仍懒创建，没有消息就没有可恢复会话。

## 决策 21：思考正文是 Provider 回放状态，用户只看结构化指标

DeepSeek `deepseek-v4-pro` 默认开启 thinking。普通 assistant 回复的 `reasoning_content` 无需进入后续上下文；但 assistant 发起工具调用时，该字段是继续工具链所必需的协议状态，后续请求必须回传。适配器因此仍按白名单重建 `assistant_message`，只在 `tool_calls` 非空时附带 reasoning；禁止退回 `model_dump()` 把 annotations/audio 等无关字段一并灌进历史。

`assistant_message` 的契约是“Provider 构造的、可安全回放的历史消息”。Agent 与 SessionStore 都不解释 reasoning，只原样编排和持久化，所以单工具、多工具与 `--resume` 走同一条路径，无需给循环增加 DeepSeek 分支。原始思考不展示、不进运行日志；`LLMResponse.meta` 只放 `thinking_enabled / duration_ms` 等框架指标，Provider 返回的 prompt / completion / cache / reasoning token 统一进入 `TokenUsage`。Agent 汇总一个用户回合内的 reasoning 与耗时，CLI 展示 token、耗时和工具调用数。动态 spinner 暂不实现，避免重新引入 Windows 终端控制序列兼容问题。

## 决策 22：Token 统计记录不可变事件，不维护共享日计数器

Token 用量属于 Provider 响应事实，由适配器归一化为 `TokenUsage`；Agent 循环不负责计费或持久化。`MeteredLLMClient` 装饰任意 `LLMClient`，在成功响应后把事件交给 `JsonlUsageStore`，写入失败只记录 warning，绝不吞掉模型响应。这样新增 Provider 时只需填充统一结构，循环与 CLI 都不感知厂商字段。

存储按 `日期/session/pid` 拆成 append-only JSONL，不让多个进程竞争一个可变汇总文件；`/usage` 查询时扫描当天事件，先给出跨项目、跨会话的全局总量，只有存在多个模型才展开模型维度。事件不保存 workdir、消息或工具内容。缓存明细保留“Provider 未返回”和“返回 0”的差异，reasoning 是 completion 的组成部分，不重复加到 total。

## 决策 23：原始会话与 active context 分层，压缩结果是持久化派生态

Provider 的最大 context window 是物理容量，不是应当持续填满的工作目标。Noval 使用独立的 `context_budget_tokens` 控制 active context：70% 触发压缩、45% 为目标、85% 为硬保护；比例先作为代码策略固化，避免把未经验证的旋钮全部暴露为配置。默认预算 256K，可按 Provider 与真实 Eval 调整。

原始 `<session>.jsonl` 继续逐条保存 user / assistant / tool 消息，是唯一真相源。压缩器只读取带 `seq/ts/msg` 的 `SessionRecord`，在完整最终 assistant 回复处选择边界，并把结构化摘要追加到 `context/<session>.jsonl`。checkpoint 记录来源 seq 区间、上一个 checkpoint、来源哈希、时间、模型和 prompt version；摘要不写回原始消息，也不提升为 system 权限。

压缩摘要是新的持久化明文副本，因此不能照抄来源中的凭据原值；只保留“存在某类凭据及其处理状态”，值统一脱敏。用户明确拒绝、暂停或决定不做的事项属于高优先级状态，必须带具体对象保留，且不得重新进入未完成任务，除非后续消息明确重启。

恢复时重建当前 system/environment/project memory，再加载最新有效 checkpoint 和 `through_seq` 之后的原始尾部。恢复本身不调用模型；下一次压缩只输入上一个摘要与新增记录，旧原文不再重复总结。checkpoint 坏行、来源不匹配或写入中断只影响派生态：读取时回退前一个有效 checkpoint，必要时回到完整原始历史。

Token 预算用可替换 `TokenEstimator` 估算消息与工具 schema；默认实现无 tokenizer 依赖，并用 Provider 返回的实际 `prompt_tokens` 校准当前进程。压缩失败时，软水位继续使用原上下文并记录 warning；超过硬水位则停止请求并给出可纠正错误，绝不静默丢消息或截断未完成的 tool-call 协议链。

## 决策 24：Eval 是运行时之外的消费者，不是第五条接缝

评测代码放在仓库级 `evals/`，通过现有 `LLMClient`、Session/checkpoint 和 trace 边界观察 Noval；生产包不引入 `eval_mode`、固定答案或针对用例的分支。为了保证评测与线上压缩使用同一份 prompt，`context.py` 只暴露无副作用的 `build_compaction_messages()` 纯函数，运行时与 Eval 共同调用。删除 `evals/` 后，Noval 的行为与依赖保持不变。

确定性结构、来源、协议与合成凭据检查可以进入普通 CI；真实模型生成、语义 Judge 和阈值回放保持手动执行并保存可复现元数据。摘要不按固定全文比对，而按必须保留、禁止声称的状态事实评估；最终能力标准是对话内压缩或冷恢复后能否继续做对事情，不只是 checkpoint 能否解析。

## 决策 25：任务完成判定只判定结果，不接管主模型

任务完成验证不再用一堆规则限制主模型怎么计划、怎么行动。主模型仍负责理解用户、调用工具、解释结果和交互；独立 `judge_model` 只在复杂任务结束点接收有限上下文：最近三个不重复用户输入、主模型最后一条可见回复，以及必要的任务标识，返回结构化 verdict。

这让任务层保持干净：它不推断行动范围、不维护执行计划、不拦截工具，也不把“是否做得正确”伪装成“是否完成”。如果用户只要求查询原因而主模型改了代码，这属于行动边界/权限/交互策略问题，不靠完成判定补锅；完成判定只回答“按当前可见结果，任务是否应继续”。

Judge 调用使用独立模型配置与独立 usage purpose。它不继承主模型 system prompt，不接收完整历史，也不写入主对话历史；判断记录可作为框架事件保留，便于回放和调试。

## 决策 26：Skill 复用成熟 `SKILL.md` 包，索引进 system，正文按需加载

Noval 的 Skill 目标不是制定新规则，而是让 Agent 能发现并正确使用已有生态里的程序性知识。MVP 兼容 Claude Code / Codex / Cursor 常见的目录包形态：目录内有 `SKILL.md` 入口，frontmatter 提供 `name` / `description`，正文和附属文件按需读取。为避免把不同产品的隐式规则混成一锅，当前明确只扫描 Cursor 的 Skill 包目录 `.cursor/skills`，不扫描 Cursor 规则目录 `.cursor/rules`。

启动时扫描用户级和项目级 `.claude/skills`、`.codex/skills`、`.cursor/skills`、`.noval/skills`，把轻量索引追加到 system prompt。这个选择牺牲了一点 system 前缀稳定性，但换来模型在第一轮就知道“有哪些技能可用”；完整正文不进 system，只有模型决定使用某个 Skill 时才调用 `load_skill`。会话运行中，Agent 在用户回合边界重新发现 Skill，并用内存态 `SkillSnapshot` 比较增删改；变化只作为本轮临时 request context 提示模型，不写入原始 session、settings 或 checkpoint。资源文件和脚本分别通过 `read_skill_resource`、`run_skill_script` 读取/执行，路径被限制在该 Skill 目录内。

Skill 是上下文和工具入口，不是权限后门。Skill 内容不能覆盖 system、项目记忆、权限确认或用户指令；Skill 脚本声明为 DANGEROUS 工具，继续走统一确认门、timeout、结构化 trace 和输出截断。这保持了原来的三条接缝：Skill 发现逻辑在 `skills.py`，运行入口仍是工具注册表和 executor 管道，agent 循环只负责把索引注入上下文。

## 决策 27：MCP 只做 client/host，server 工具按需发现与调用

MCP 与 Skill 一样属于“复用成熟生态”的能力，但它的位置不同：Skill 主要是程序性知识和工作流说明，MCP 是外部能力提供者，是工具层的一部分。Noval 不实现 MCP server，也不把 MCP 变成 Noval 私有格式；第一版只作为 MCP host/client 读取通用 `mcpServers` 配置，并支持 stdio server。

配置来源分两层：用户级 `~/.noval/mcp.json` 与项目级 `<workdir>/.mcp.json`。启动时只把 server 的轻量索引注入 system prompt，包含 id/name/source/transport/env key，不暴露 env 值，也不自动启动外部进程。模型需要具体能力时，必须先用 `list_mcp_tools(server=...)` 按需连接 server 读取工具列表，再用 `call_mcp_tool(...)` 执行。这样 system 前缀保持轻，外部进程启动也不会在用户未触发时发生。

会话运行中，Agent 在用户回合边界重新发现 MCP 配置，并用内存态 `McpSnapshot` 比较增删改；变化只作为本轮临时 request context 提示模型，不写入原始 session、settings 或 checkpoint。由于 `list_mcp_tools` 和 `call_mcp_tool` 都会启动外部进程或调用外部能力，它们声明为 DANGEROUS，继续走统一确认门、timeout、结构化 trace 和输出截断。MCP server/tool 描述和返回值都被视为外部数据，不能覆盖 system、项目记忆、权限确认或用户指令。若 MCP 的 text content 实际是 JSON，`call_mcp_tool` 会解析成结构化 content，避免把 JSON 字符串再包一层 JSON 喂给模型。

## 决策 28：工具输出进入模型前统一脱敏

真实工具输出经常包含配置文件、命令结果和外部系统响应，里面可能混有 password、secret、token、privateKey、appSecret、accessKey、webhook 等敏感值。不能要求每个工具作者都记得单独处理，也不能只在日志层脱敏——因为工具结果会进入模型上下文和原始 session。

因此脱敏放在 executor 的统一出口：工具返回原始内容，框架在截断和持久化前先做敏感形态替换，并在 `ToolResult.meta.redacted` 中记录本次是否发生脱敏。这样新增第 N 个工具仍自动继承安全边界，且脱敏策略不会散落在 MCP、shell、文件读取等具体工具里。

## 施工顺序

1. `tools.py`：`ToolResult` / `ToolError` / `@tool` 注册表 —— 地基
2. `executor.py`：管道（try/except + schema 校验 + 截断，确认门与 timeout 随后）
3. `agent.py`：循环接 executor + max_steps
4. `permissions.py` + 确认门 + 风险级别
5. 可观测性（结构化 trace）+ mock client 最小测试
6. `session.py` + `Agent(store=...)` + CLI `--resume`：会话持久化第四接缝

---

## 能力演进路线

按“先建立可测量闭环，再扩大行动半径”的顺序推进。每一阶段都继续复用并扩展 Eval，不能因为进入下一阶段就停止回归。外部评审、对标顶级 agent 和公开基准都只作为输入：**批判性吸收**，不照单全收。

吸收原则：
- 已经在代码中复现的问题优先于路线愿望；安全与成本问题可插队。
- 触碰核心接缝（Provider / Registry / Executor / Session / Confinement）的能力优先于叶子功能。
- 能被测试、Eval 或回放验证的能力优先；无法验证的能力先留在 backlog。
- 公开 benchmark 是测量仪器，不是北极星；不得为了提分破坏“通用内核、场景解耦”的边界。
- 产品层能力（IDE、PR 自动化、浏览器、多模态 UI、市场生态）默认不进内核路线，除非它们先被抽象成通用接缝。

已完成的地基：
- [x] **评测脊柱（MVP）**：已覆盖 checkpoint 结构/状态事实、真实模型摘要、对话内继续、冷恢复理解、受控工具行动、重复采样与不同模型 Judge。持续项包括摘要生成后的确定性安全校验、3 个匿名真实会话切片和阈值回放；这些作为 Eval backlog 并行推进，不阻塞下一阶段，但发现硬失败时必须优先修复。
- [x] **任务完成验证（MVP）**：主模型负责执行、工具调用与用户交互；独立 `judge_model` 只接收最近三个不重复用户输入和主模型最后可见回复，返回结构化完成 verdict。任务层不推断行动范围、不维护执行计划、不拦截工具。
- [x] **Skill 加载运行（MVP）**：复用 Claude Code / Codex / Cursor 风格 `SKILL.md` 目录包；system 只注入轻量索引，完整正文、资源与脚本按需通过工具加载，脚本不绕过权限门。
- [x] **MCP client（MVP）**：复用通用 MCP server；system 只注入 server 轻量索引，stdio server 工具按需发现/调用，外部进程启动和 MCP tool 调用不绕过权限门。

版本化主线：

| 版本 | 主线 | 能力范围 | 出口标准 |
|---|---|---|---|
| `v0.5.x` | 安全热修线 | 修复 `run_bash` 换行/回车绕过确认门；补对抗性回归；降低脱敏误伤；为 Provider 请求加显式 timeout/必要重试 | 安全回归与现有测试全绿；不引入新架构面 |
| `v0.6.0` | 行动边界硬化 | 引入 `ConfinementPolicy`；path-jail v1 只接入 `_resolve`；文件工具按 read/write roots 判定；越界返回可纠正 `ToolError` | read/write/edit/glob/grep 继承同一边界；符号链接逃逸、新文件父目录、glob/grep 产物均有测试 |
| `v0.7.0` | 子进程沙箱接缝 | 把 `run_bash`、`run_skill_script`、MCP 外部进程收束到 `confined_run`；实现 `NoSandbox` 诚实降级与后端探测；后续接 Bubblewrap / 平台 backend | 仓库里没有散落的外部进程执行入口；timeout、trace、截断、脱敏和权限复用 executor 边界 |
| `v0.8.0` | Hooks 与验证闭环 | 生命周期事件 `PreToolUse` / `PostToolUse` / `Stop`；hook outcome 严格限定为 `allow` / `deny(reason)` / `context(text)`；CommandHook 复用 `confined_run`；PostToolUse 可跑 lint/test 并回喂诊断 | hook 不能覆盖 system、权限、脱敏、用户指令或 session 历史；失败隔离可观测；改后验证闭环可测 |
| `v0.9.0` | Provider 真中立 | 内部 canonical message；各 Provider adapter 做双向翻译；session/checkpoint 不再直接持有 OpenAI wire；路由决策可回放 | agent 循环、context、session 只依赖 canonical message；Provider 特有字段只存在 adapter 层 |
| `v1.0.0` | 可嵌入稳定内核 | headless API / SDK；精确重建第 N 步模型 request；Eval 成为发布门槛；Terminal-Bench 小切片作为客观回归信号 | 公共契约稳定；公开基准只做回归测量，不驱动内核变形 |

被版本门约束的长期能力：
- **长任务与记忆**：等 `v0.6`/`v0.7` 的硬边界与 `v0.8` 的验证闭环稳定后推进；所有记忆必须有来源、时效、冲突与删除边界。
- **模型路由**：等 `v0.9` canonical Provider 完成后推进；按任务、成本、延迟、上下文和风险选择模型，并保留可回放、可比较的路由决策。
- **多 Agent**：等单 Agent 的完成验证、恢复、权限、沙箱和 hooks 闭环稳定后再引入；要求共享预算、结果合并、冲突处理和独立复核。

阶段出口不是“代码已经写完”，而是对应能力已有确定性测试、代表性 Eval、失败回归样本和可观察指标。

## 待办 / 未决（后续再议）

- 会话持久化：核心已落地（JSONL store + Agent 接入 + CLI `--resume`），后续可补 UI/标题编辑/归档等体验层。
- 上下文压缩与 Eval 脊柱 MVP 已落地；继续补齐确定性安全后处理、真实切片和阈值回放。
- 系统提示词的管理与版本化。
- Provider canonical message 与多 provider 适配器（目前仅 DeepSeek / OpenAI-compatible wire）。
- 工具数 >8 后 `tools.py` 的拆分。
- **原地打转检测**：识别"连续 N 次完全相同的工具调用（同名 + 同参）"，比 max_steps 更早止损，
  并反馈给模型"你在重复"。max_steps 是总闸，这是更细的"卡在同一动作"识别（评审建议，优化项不急）。
- 持久 shell（决策 16：等"跑构建/开发流程"成为目标场景再做）。
