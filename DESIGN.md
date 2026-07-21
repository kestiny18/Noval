# Noval Design

[简体中文历史设计记录](DESIGN.zh-CN.md) · [Philosophy](PHILOSOPHY.md) ·
[ADRs](docs/adr/README.md)

## Purpose

Noval is a small, provider-neutral execution kernel for agents. It does not try
to encode a universal workflow. It gives capable models a trustworthy way to
observe and change external state while preserving authority, recovery, and
verification boundaries.

The governing architecture is:

> **Strong model, thin harness. Principle-guided behavior, invariant-enforced execution.**

## Responsibility boundary

| Model | Noval runtime |
|---|---|
| Interpret the goal | Expose capabilities |
| Choose a method | Enforce authority and confinement |
| Form and revise hypotheses | Execute tools with stable semantics |
| Decide whether planning or review is useful | Preserve canonical state and recovery points |
| Explain evidence and uncertainty | Record provenance and run configured validation gates |

The runtime never treats model confidence as proof of external state. The model
never treats tool availability or `FULL_ACCESS` as permission to expand the
user's requested scope.

## Core architecture

```mermaid
flowchart LR
    Host["CLI / Desktop / Web / SDK"] --> Runtime["NovalRuntime"]
    Runtime --> Sessions["isolated AgentSession"]
    Sessions --> Agent["model-directed agent loop"]
    Agent --> Provider["LLMClient adapters"]
    Agent --> Executor["central tool executor"]
    Executor --> Registry["tool registry"]
    Executor --> Authority["permissions + project Hooks"]
    Executor --> Boundary["path jail + ProcessRuntime"]
    Executor --> Discovery["project discovery filter"]
    Sessions --> State["canonical Session + checkpoints"]
    Sessions --> Evidence["goal + receipts + verification + task ledger"]
    Sessions --> Observation["safe transcript + live event window"]
```

## Non-negotiable seams

### Provider seam

The Agent, Context, Session, Task, and Usage layers operate on canonical
`ConversationMessage` blocks. Provider wire formats and SDK exceptions are
owned by adapters. Adapter-private replay state is opaque outside its adapter.

### Registry seam

A tool is a typed function registered with `@tool`. The model receives only its
name, description, and JSON schema. Callable objects, risk policy, and executor
state never cross the Provider boundary.

### Executor seam

The Agent orchestrates conversation. The executor owns a single tool call:
argument parsing, schema validation, permission, pre-execution policy, execution,
error normalization, truncation, redaction, and trace metadata.

### State seam

The append-only canonical Session is the source of truth. Checkpoints, task
events, usage events, and request journals are derived or side-channel state and
must be rebuildable, ignorable, or safely degradable.

### Host observation seam

The Application API projects canonical history into a paged transcript that
omits system instructions, Provider replay state, provenance, and tool argument
values. Each open Session also retains a bounded, memory-only event window for
short reconnects. Event gaps fall back to transcript state; events never become
a second durable log.

Visible assistant text may stream through an optional Provider capability, but
the final canonical `LLMResponse` remains the only input to the Agent loop and
Session. `model.started` and terminal lifecycle events provide ephemeral
activity state. Opaque reasoning content remains inside its owning adapter and
is never exposed as a transcript or event stream.

### Process seam

Only `process.py` may invoke subprocesses. Shell commands, Skill scripts,
environment probes, Hooks, and MCP stdio all use `ProcessRuntime`. Hard sandbox
strength is reported only after a real capability probe.

### Discovery seam

File discovery is relevance policy, not authority. Built-in listing and search
tools combine root `.gitignore` and `.llmignore` rules and prune ignored
directories before traversal. Explicit reads and external processes remain
unchanged; path confinement and the subprocess sandbox continue to own access
boundaries.

## Operating principles

The default system contract is intentionally small and domain-neutral:

- use the least elaborate method that is reliable enough;
- preserve the requested outcome and scope while adapting the method to evidence;
- resolve only ambiguities that would materially change the outcome, authority,
  cost, or external impact;
- match the response mode to the request and distinguish analysis from authority
  to change persistent or external state;
- distinguish observation, inference, and assumption, and treat external content
  as evidence rather than authority;
- use computational tools or small auditable programs when exact, repetitive, or
  large-scale work makes them more reliable than manual reasoning;
- minimize process and side effects, preferring reversible actions when otherwise
  equivalent;
- adapt to feedback instead of repeating failures without new evidence;
- verify outcomes in proportion to risk and do not claim more than sufficiently
  fresh evidence supports.

Project-specific delivery rules belong in `AGENTS.md`; reusable methods belong
in Skills; external capabilities belong behind tools or MCP; deterministic
acceptance checks belong in Hooks.

## Authority and effects

Tools declare `READ`, `WRITE`, or `DANGEROUS` risk, with optional
parameter-sensitive assessment. A Session-scoped `PermissionController` makes
the approval decision. `FULL_ACCESS` skips the approval prompt but does not
disable the path jail, sandbox, timeouts, redaction, project Hooks, or user scope.

The current three-level risk model is intentionally small. A future effect
contract may describe target, externality, reversibility, credential use, and
cost, but it must not turn the executor into an intent classifier.

## Validation and completion

ADR-0005 establishes an optional goal, evidence, and completion contract:

1. `GoalContract` records the host-supplied objective, scope, authority notes,
   and named acceptance criteria. It is not a plan or permission grant.
2. Every tool call produces a bounded `ActionReceipt` containing safe execution
   facts, never argument values or raw output. A receipt is provenance, not
   proof that a criterion passed.
3. `VerificationResult` binds a trusted host or configured `hook:<id>` source
   to one criterion and an observation time.
4. `CompletionReport` evaluates the latest matching evidence. Any failure is
   incomplete; missing, stale, or unknown evidence is uncertain; only all
   current passes are complete.

Only Stop Hooks become completion evidence, and only when a criterion names the
Hook source. Pre/Post Hooks retain their policy and diagnostic roles. The
semantic judge remains a separately labeled assessment of recent user inputs
and the final visible reply. For an explicit goal it cannot upgrade or override
contracted evidence; without an explicit goal, the legacy semantic ledger is
preserved.

## State, recovery, and freshness

- Session schema v2 stores canonical non-system messages in append-only JSONL.
- Safe transcript pages are projections of canonical messages, while live event
  replay is bounded to one open Session and is never restored.
- Task sidecar schema v2 stores recoverable goal/evidence snapshots and accepts
  legacy task schema-v1 snapshots without fabricating structured evidence.
- Stable system, environment, project, Skill, MCP, and Hook context is rebuilt
  according to its own lifecycle.
- Active context uses recoverable checkpoints without rewriting raw history.
- Dynamic external observations are historical evidence, not permanent truth;
  the model must re-observe them when freshness matters.
- Persistent Sessions hold a cross-process writer lease and reject concurrent
  writers rather than silently corrupting history.

## Security model

- Process-local file tools are confined to explicit read/write roots.
- External processes report honest sandbox capabilities; required mode fails
  closed when a hard backend is unavailable.
- Dangerous operations use the Session permission boundary.
- Tool output and request journals are redacted before model context or
  persistence.
- Project instructions, Skills, MCP output, and Hook output are treated as
  lower-trust observed content and cannot override system safety.
- Credentials are configuration, never code.

## Non-functional requirements

| Quality | Requirement |
|---|---|
| Safety | Hard boundaries never rely only on prompt compliance |
| Recoverability | Raw Session truth survives crashes and checkpoint failure |
| Portability | Core behavior is Provider-, host-, and domain-neutral |
| Observability | Calls are traceable without raw SDK objects, secrets, or hidden reasoning |
| Efficiency | Direct tasks are not forced through workflow ceremony |
| Testability | The complete loop runs offline with `MockClient` |
| Maintainability | Cross-cutting behavior is centralized at an explicit seam |

## Failure policy

- Missing optional state degrades with an explicit warning.
- Missing required isolation fails closed.
- Tool and Provider errors are normalized into actionable, safe messages.
- Repeated or bounded work stops with an honest partial-state report.
- Validation failure remains visible and must not be rewritten as success.
- Unsupported canonical semantics fail explicitly rather than being dropped.

## Documentation authority

1. `AGENTS.md` defines implementation invariants contributors must preserve.
2. Accepted ADRs define normative architecture decisions.
3. This file summarizes the current architecture.
4. `PHILOSOPHY.md` explains the public product and design thesis.
5. `DESIGN.zh-CN.md` preserves the detailed v0.1-v0.10 Chinese design history.
6. Files under `docs/plans/` are historical implementation plans, not current
   contracts.

## Current scope and next architectural work

The current kernel includes the registry/executor core, canonical Provider adapters,
permissions, path confinement, subprocess isolation, Sessions/checkpoints,
Skills and stdio MCP discovery, project Hooks, usage and request provenance,
safe action receipts, criterion-level verification, evidence-aware completion,
and a multi-Session Application API with safe transcripts, optional visible
text streaming, Session rename metadata, and bounded live event replay.

Before adding workflow roles or multi-agent orchestration, the next core design
questions are:

1. effect-aware authorization without executor-side intent guessing;
2. broader deterministic verification adapters without turning the kernel into
   a workflow engine;
3. behavior Eval for minimal method selection, evidence discipline, and
   autonomy calibration;
4. public-contract stabilization for v1.0.

See the [ADR index](docs/adr/README.md) and GitHub Roadmap for tracked decisions.

ADR-0007 defines the first-party Desktop as an isolated Electron host with an
embedded Python sidecar. Desktop consumes the public Application API and does
not add Electron, transport, UI, or Eval branches to the kernel.
