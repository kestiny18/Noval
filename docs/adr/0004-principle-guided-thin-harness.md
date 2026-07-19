# ADR-0004: Adopt Principle-Guided, Invariant-Enforced Autonomy

## Status

Accepted

## Context

Noval began as a tool-calling loop and evolved into an execution kernel with
provider adapters, a central executor, permissions, confinement, subprocess
isolation, persistent sessions, checkpoints, Hooks, Skills, MCP, and a headless
Application API.

Frontier models increasingly sustain their own planning, investigation, and
revision. Encoding Planner/Executor/Reviewer as a mandatory workflow in the
kernel would duplicate model capability, increase cost on simple tasks, and
couple a domain-neutral runtime to one problem-solving method.

At the same time, increased model capability does not make self-reported
authorization, execution, or completion trustworthy. Models remain
probabilistic and have no privileged access to external state.

## Decision

Noval will use **principle-guided, invariant-enforced autonomy**:

- The model owns strategy, method selection, hypothesis revision, and user
  communication.
- The core will not require a fixed planning, execution, or review pipeline.
- Stable operating principles will encourage the least elaborate method that
  is reliable enough for the task.
- Those principles are decision criteria rather than a mandatory sequence, and
  keep explanation, analysis, and review distinct from authority to change
  persistent or external state.
- External and tool-provided content is evidence rather than authority. It
  cannot override higher-priority instructions or expand user authorization.
- Models may use computational tools or synthesize small, auditable programs
  when this is more reliable than manual reasoning, without turning the core
  into a coding-specific agent.
- The runtime owns capability exposure, authority, execution semantics,
  canonical state, recovery, evidence boundaries, and deterministic gates.
- Permission to call a tool will remain distinct from user authorization to
  pursue a goal.
- Completion checks will distinguish semantic assessment of a visible answer
  from verification of external state.
- Domain workflows belong in Skills, MCP servers, project Hooks, or hosts unless
  they establish a domain-neutral core seam.

English documentation is the canonical public contract. Chinese translations
are first-class entry points, but historical decision logs need not be mirrored
line by line.

## Non-functional requirements

- **Safety:** hard boundaries must not depend on model compliance.
- **Reliability:** state-changing actions must return actionable failures and
  remain reconstructable from canonical records.
- **Recoverability:** interruption must not silently rewrite the source session.
- **Observability:** requests and actions must be traceable without persisting
  credentials or opaque reasoning.
- **Efficiency:** direct answers must not be forced through unnecessary planning
  or tool loops.
- **Portability:** the core must remain provider- and host-neutral.
- **Maintainability:** each new cross-cutting concern must enter through an
  existing seam or justify a new ADR.

## Consequences

### Positive

- Stronger models can use their own best method instead of fighting a workflow.
- Simple tasks stay simple.
- Safety guarantees remain stable across model generations.
- The kernel remains useful to coding and non-coding hosts.
- Product differentiation is grounded in execution trust, not agent-role count.

### Negative

- Behavior has more variance than a fully scripted workflow.
- Weak models may need additional host- or Skill-level scaffolding.
- General completion verification requires evidence contracts that do not yet
  exist in v0.10.
- Principle changes require behavioral Eval rather than only unit tests.

### Neutral

- Planning and review are still allowed; they become optional capabilities.
- Existing Hooks remain deterministic project policy rather than a generic
  workflow engine.
- The semantic completion judge remains a ledger until a future ADR defines
  how evidence may gate a public terminal state.

## Failure modes and mitigations

| Failure mode | Mitigation |
|---|---|
| The model skips necessary investigation | Require claims to match evidence; add behavior Eval cases |
| The model overuses tools or planning | Encode minimal-sufficient-method principles; measure tool count and latency |
| Tool permission is mistaken for task scope | Keep intent in the system contract and permission in the executor boundary |
| Tool or retrieved content attempts to redirect the task | Treat external content as evidence, never as authority over instructions or scope |
| Large or repetitive computation is performed unreliably by hand | Allow small auditable programs while keeping persistent artifacts outcome-driven |
| A fluent answer fabricates completion | Use deterministic Hooks today; design structured evidence receipts next |
| A weak model cannot self-direct | Add optional capability profiles outside the core loop |
| Project content attempts to override safety | Preserve system/project/tool-content trust boundaries |

## Alternatives considered

### Mandatory Planner -> Executor -> Reviewer pipeline

Rejected for the core. It is useful for selected high-risk tasks but too costly
and domain-specific as a universal runtime policy.

### Model-only autonomy

Rejected. Prompts cannot enforce authorization, confinement, timeouts,
redaction, recovery, or external-state truth.

### Automatic intent router in the executor

Deferred. The executor should enforce declared effects, not guess user intent.
Behavior Eval should demonstrate a need before adding another classifier.

## References

- [Noval Philosophy](../../PHILOSOPHY.md)
- [Canonical design](../../DESIGN.md)
- [Historical Chinese design ledger](../../DESIGN.zh-CN.md)
