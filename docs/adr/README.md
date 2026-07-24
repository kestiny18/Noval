# Architecture Decision Records

ADRs are Noval's normative record for decisions that change a public contract,
a core seam, or a cross-cutting invariant.

| ADR | Status | Decision |
|---|---|---|
| [0001](0001-application-api-boundary.md) | Accepted | Add a headless Application API boundary |
| [0002](0002-session-isolation-and-concurrency.md) | Accepted | Isolate mutable state per Session |
| [0003](0003-json-contracts-and-live-events.md) | Accepted | Use JSON-safe contracts and live-only events |
| [0004](0004-principle-guided-thin-harness.md) | Accepted | Adopt principle-guided, invariant-enforced autonomy |
| [0005](0005-goal-evidence-completion-contract.md) | Accepted | Establish goal, evidence, and completion contracts |
| [0006](0006-desktop-consumer-observation-boundary.md) | Accepted | Expose safe desktop-consumer observation contracts |
| [0007](0007-first-party-desktop-host.md) | Accepted; credential clause superseded by 0010 | Build the first-party desktop as an isolated Electron host |
| [0008](0008-current-session-schema-discovery.md) | Accepted; current version superseded by 0010 | Discover only the current canonical Session schema |
| [0009](0009-bidirectional-transcript-pagination.md) | Accepted | Add bounded reverse transcript pagination |
| [0010](0010-runtime-owned-model-configuration.md) | Accepted for implementation | Adopt Runtime-owned OpenAI-compatible model configuration |

The earlier Chinese decision ledger is preserved in
[DESIGN.zh-CN.md](../../DESIGN.zh-CN.md). New decisions should use an ADR rather
than extending that monolithic history.
