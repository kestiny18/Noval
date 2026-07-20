# Desktop Consumer Readiness Implementation Plan

**Goal:** Add the safe host-facing primitives needed by a future desktop UI
while preserving Noval's strong-model, thin-harness boundaries.

**Architecture:** Keep canonical Session history authoritative. Add safe DTO
projections and mutable title metadata at the Application API, a bounded
memory-only event window per live Session, and optional Provider-neutral text
streaming whose adapters still return the same final canonical response.

## Task 1: Record the architecture boundary

1. Add ADR-0006 and the detailed design.
2. Update the ADR index.
3. Validate documentation links and formatting.
4. Commit the architecture decision independently.

## Task 2: Add safe transcript reads

1. Add transcript DTOs with explicit JSON serialization.
2. Project canonical messages without system content, Provider replay state,
   provenance, or raw tool arguments.
3. Add equivalent live records for ephemeral Sessions.
4. Add bounded cursor pagination for persistent and ephemeral Sessions.
5. Prevent `TurnResult.to_dict()` from exposing opaque replay state.
6. Test JSON shapes, pagination, privacy, and recovery behavior.
7. Commit after focused tests pass.

## Task 3: Add Session rename

1. Add bounded title validation at the public contract.
2. Add idle-only `AgentSession.rename()`.
3. Persist titles through the existing metadata sidecar and update live info.
4. Emit `session.renamed` and test ephemeral, persistent, resume, and busy
   behavior.
5. Commit after focused tests pass.

## Task 4: Add bounded live event replay

1. Add an `EventPage` DTO with cursor and gap metadata.
2. Record all live Session events before best-effort sink dispatch.
3. Add bounded eviction and `replay_events()`.
4. Test ordering, isolation, gaps, sink failures, and close semantics.
5. Commit after focused tests pass.

## Task 5: Add optional Provider-neutral visible text streaming

1. Add the optional streaming port and visible-text event type without
   changing the required `LLMClient.complete()` contract.
2. Preserve streaming through request recording and usage metering wrappers.
3. Emit `model.output.delta` and `model.output.aborted` from the Agent while
   keeping final canonical response semantics unchanged.
4. Implement OpenAI-compatible stream reconstruction and tests.
5. Implement Anthropic stream reconstruction and tests.
6. Prove raw reasoning, signatures, tool-argument fragments, and partial failed
   output never enter public events or Session persistence.
7. Commit the port, wrappers, and each adapter in independently validated
   increments where practical.

## Task 6: Document and validate the consumer surface

1. Update English and Chinese Application API documentation with transcript,
   rename, replay, streaming, and privacy semantics.
2. Update canonical design and README capability summaries.
3. Run focused tests, the complete suite, Context Eval, Task Eval, and
   `git diff --check`.
4. Inspect the full diff and sensitive-content scan.
5. Commit documentation, push the feature branch, open a pull request, wait
   for required checks, merge to protected `main`, and synchronize the related
   Issue only when every acceptance criterion is satisfied.
