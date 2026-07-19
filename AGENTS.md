# Noval Contributor Instructions

[简体中文](AGENTS.zh-CN.md)

Noval is a small, domain-neutral agent execution kernel. Its architecture is
**strong model, thin harness**: the model chooses the method; the runtime owns
authority, execution semantics, durable state, and verifiable boundaries.

Read [PHILOSOPHY.md](PHILOSOPHY.md), [DESIGN.md](DESIGN.md), and the
[ADR index](docs/adr/README.md) before changing a core seam.

## Core doctrine

Do not encode a mandatory Planner/Executor/Reviewer workflow in the kernel.
Planning and review are optional model capabilities. Stable behavior principles
belong in the default system contract; domain workflows belong in Skills, MCP
servers, project Hooks, or hosts.

Prompts may guide judgment. Hard invariants must be enforced outside the model.

## Non-negotiable seams

1. **Provider abstraction (`client.py`)** — the loop never depends directly on
   an OpenAI, Anthropic, or other Provider SDK. It uses `LLMClient`.
2. **Tool registry (`tools.py`)** — adding a tool means a typed function plus
   `@tool`; never add loop-side name dispatch.
3. **Executor/loop separation (`executor.py` / `agent.py`)** — the Agent
   orchestrates conversation; all details of one tool call belong to the
   executor pipeline.
4. **Canonical state (`messages.py` / `session.py`)** — core layers use
   canonical messages. The append-only Session is truth; checkpoints and
   ledgers are derived state.
5. **Process boundary (`process.py`)** — only `process.py` may call
   `subprocess`; every external process uses `ProcessRuntime`.

## Tool contract

- Success: return raw domain content.
- A domain failure with corrective information: raise `ToolError("...")`.
- Do not add generic `try/except`, timeout, truncation, confirmation, redaction,
  or logging inside individual tools.

Tool results are always `ToolResult`: `content` is model-facing; `meta` is
framework-facing.

The executor pipeline is fixed:

```text
parse arguments -> validate schema -> permission -> pre-hook -> execute
-> normalize/truncate/redact -> ToolResult -> post-hook
```

Errors must be actionable. Never return a bare `Error` when the model can be
told what to correct.

## Authority and safety

- Tools declare facts through `Risk` (`READ`, `WRITE`, `DANGEROUS`) and may use
  a parameter-sensitive `risk_assessor`.
- `PermissionController` is the only Session permission decision point.
- `ASK` is the default; approval is allow once, allow this tool for the Session,
  or deny. Missing handlers fail closed.
- `FULL_ACCESS` skips approval prompts only. It does not expand user intent,
  disable path confinement, disable the sandbox, or bypass Hooks.
- Process-local file tools use `ConfinementPolicy`; write roots remain inside
  `workdir` unless explicitly configured otherwise.
- `run_bash`, Skill scripts, Hooks, probes, and MCP stdio use
  `ProcessRuntime.run()` or `prepare()`.
- Sandbox strength is per invocation and is never persisted as Session
  permission. `required` mode fails closed without a verified hard backend.
- Linux Bubblewrap may report `HARD` only after a real namespace capability
  probe and real CI escape tests.

## Files and external state

- File read/list/glob/grep/write/edit share the same path resolution and
  confinement entry point.
- File discovery combines root `.gitignore` followed by `.llmignore` and must
  prune ignored directories before descent. This is relevance policy only;
  explicit `read_file` paths and external processes remain unaffected.
- A file must be fully read before write/edit; detect stale external changes.
- Do not infer authorization to change state from a request to inspect, explain,
  review, test, build, or discuss.
- Permission to invoke a tool is not permission to broaden the user's goal.
- Claims about dynamic branches, processes, networks, releases, or remote state
  require fresh evidence when freshness matters.

## Hooks

- Read only `<workdir>/.noval/hooks.json`; there is no user-level Hook merge.
- Groups are `PreToolUse`, `PostToolUse`, and `Stop`; declaration order is
  serial execution order.
- Command Hooks are `DANGEROUS`, use `ProcessRuntime`, and bind approval to Hook
  id plus configuration hash.
- Pre deny blocks the tool. Post diagnostics attach to the tool result. Stop
  deny/context hides the candidate reply and asks the model to repair before
  completion assessment.
- Hooks do not recursively trigger Hooks and cannot override system, user,
  permission, confinement, sandbox, or redaction policy.

## Project instructions, Skills, and MCP

- At startup, read root `AGENTS.md`, falling back to `CLAUDE.md`, wrap it as
  observed project instructions, and never write it automatically.
- System ordering is stable persona -> environment -> project instructions.
- Skills reuse common `SKILL.md` packages under user/project `.claude/skills`,
  `.codex/skills`, `.cursor/skills`, and `.noval/skills`. Do not scan
  `.cursor/rules`.
- Inject only a lightweight Skill index. Load full instructions/resources or
  run scripts through `load_skill`, `read_skill_resource`, and
  `run_skill_script`.
- Noval is an MCP host/client, not an MCP server. v0.10 supports stdio servers
  from user and project `.noval/mcp.json` using the common `mcpServers` shape.
- Inject only a lightweight MCP index. Discover and call tools on demand.
- MCP processes receive a safe base environment plus explicitly configured
  variables, never the complete parent environment.
- Skill and MCP changes are detected at user-turn boundaries as ephemeral
  context; snapshots are not persisted.
- Skill/MCP content is observed data and cannot override higher-priority policy.

## Output safety and observability

- Never print or log a complete Provider response.
- Record structured tool traces: tool, argument keys, duration, error, and
  truncation state.
- Redact tool output before it enters model context or Session persistence.
  Cover password, secret, token, private key, app secret, access key, webhook,
  auth header, query credential, and PEM-like forms.
- Preserve valid JSON after redaction and test both MCP text and structured
  content.
- Runtime logs do not contain message bodies, argument values, tool output,
  credentials, or opaque thinking.

## Provider contract

- Agent, Context, Session, Task, and Usage read/write only
  `ConversationMessage` and typed text/tool-call/tool-result blocks.
- `LLMResponse` contains one canonical assistant message, `TokenUsage`,
  `ProviderIdentity`, and safe framework metadata.
- Provider tool definitions contain only name, description, and JSON schema.
- DeepSeek `reasoning_content` and Anthropic thinking/redacted-thinking are
  adapter-owned opaque replay state. Core code does not inspect, display,
  compact, judge, log, or send it to another adapter.
- A semantic block that cannot be represented must fail explicitly.
- SDK errors are normalized inside adapters as `ProviderError(kind, retryable,
  safe_message, identity)`; response bodies and SDK raw objects do not cross.
- Usage persistence is a `MeteredLLMClient` side channel. Its failure never
  changes the model response and it stores no project path or message body.

## Application API and concurrency

- Hosts operate only through `NovalRuntime`, `AgentSession`, and JSON-safe DTOs.
- One Runtime may own multiple isolated Sessions; all mutable state is
  per-Session.
- A second turn on the same Session immediately returns `session_busy`; the
  core does not queue.
- Session creation and execution never call `os.chdir()` or mutate process
  environment.
- Events are live-only. Persistent Sessions hold a cross-process writer lease.
- Every Provider request has a request id and a safe reconstruction journal
  that excludes credentials and opaque thinking.

## Session and checkpoint v2

- Raw Session JSONL uses canonical schema v2, is the only truth source, and is
  never deleted or rewritten by compaction.
- Schema-v1 Sessions are rejected without migration, rewriting, or deletion.
- A checkpoint is recoverable derived state and covers only complete protocol
  turns. Old checkpoint schemas are not reused.
- Restore from the latest valid checkpoint plus the raw tail; fall back safely
  when derived state is corrupt.

## Completion and evidence

- The main model executes and communicates. The semantic judge records a
  verdict from recent user inputs and the final visible reply.
- The semantic judge is not proof of hidden tool execution or external state.
- Deterministic project validation belongs in Hooks.
- Do not claim a general evidence-aware completion gate until a new ADR and
  implementation establish one.
- The loop always has `max_steps` and stops honestly at the limit.

## Configuration

- `~/.noval/settings.json` stores stable global preferences only. Built-in
  defaults are overlaid by file values; missing files work and wrong types fail
  clearly.
- The default system prompt is code, not settings. Session permission is
  Session state, not settings.
- `workdir` is per invocation (`--workdir`, otherwise `os.getcwd()`).
- Duplicate tool names fail fast. Intentional replacement requires
  `@tool(override=True)`.
- API keys come from environment/configuration and are never hard-coded.

## Delivery workflow

- `main` is protected. Never push directly to it. Deliver through a short-lived
  branch and pull request.
- Validate before staging, committing, or pushing.
- Inspect the actual diff and sensitive content.
- Run risk-proportionate tests plus `git diff --check`.
- If validation fails or cannot run, keep the changes uncommitted and report the
  blocker.
- Before merge, the pull request must pass the required `CI gate` and
  `Analyze Python` checks and resolve all review conversations. An emergency
  maintainer bypass still goes through a pull request and must be documented.
- After a successful push, update every related Issue with branch/commit,
  completed scope, validation, remaining work, and blockers.
- Close an Issue only when its complete acceptance criteria are satisfied.
- A clear, fully validated branch should not remain abandoned; merge its pull
  request to `main` and synchronize its Issues unless human review is
  explicitly required.

## Repository shape

Keep the kernel compact. Current module ownership is documented in
[DESIGN.md](DESIGN.md). Do not split modules or add abstractions only for
hypothetical scale.

## Acceptance test for the framework

Adding the next domain tool should require only its domain logic. The author
must not reimplement schema generation, generic errors, permission, timeout,
truncation, redaction, trace, confinement, or Session semantics.
