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
    Sessions --> Evidence["events + request provenance + task ledger"]
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

v0.10 tools declare `READ`, `WRITE`, or `DANGEROUS` risk, with optional
parameter-sensitive assessment. A Session-scoped `PermissionController` makes
the approval decision. `FULL_ACCESS` skips the approval prompt but does not
disable the path jail, sandbox, timeouts, redaction, project Hooks, or user scope.

The current three-level risk model is intentionally small. A future effect
contract may describe target, externality, reversibility, credential use, and
cost, but it must not turn the executor into an intent classifier.

## Validation and completion

Noval has two distinct mechanisms:

1. Project Hooks can deterministically block an action, attach diagnostics, or
   reject a candidate stop and send the model back to repair.
2. The semantic completion judge records a structured verdict from recent user
   inputs and the final visible reply.

The semantic judge does not observe hidden tool evidence and its verdict does
not prove that external state is correct. v0.10 therefore treats it as a task
ledger, not a universal completion gate. A future goal/evidence contract must be
defined by ADR before changing this boundary.

## State, recovery, and freshness

- Session schema v2 stores canonical non-system messages in append-only JSONL.
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

v0.10 includes the registry/executor core, canonical Provider adapters,
permissions, path confinement, subprocess isolation, Sessions/checkpoints,
Skills and stdio MCP discovery, project Hooks, usage and request provenance,
and a multi-Session Application API.

Before adding workflow roles or multi-agent orchestration, the next core design
questions are:

1. a domain-neutral goal, scope, and acceptance contract;
2. structured observations, action receipts, and verification evidence;
3. effect-aware authorization without executor-side intent guessing;
4. behavior Eval for minimal method selection, evidence discipline, and
   autonomy calibration;
5. public-contract stabilization for v1.0.

See the [ADR index](docs/adr/README.md) and GitHub Roadmap for tracked decisions.
