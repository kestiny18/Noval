# ADR-0009: Add Bounded Reverse Transcript Pagination

## Status

Accepted.

## Context

ADR-0006 exposes a safe forward-paged transcript. That contract is appropriate
for synchronization after a known sequence, but a desktop host opening a long
historical Session needs the opposite access pattern: render the newest bounded
portion first, then fetch older history only when the user scrolls upward.

Loading every transcript page into Electron before rendering would preserve the
transport contract but defeat its boundedness. Letting Desktop read canonical
JSONL directly would duplicate Session parsing and weaken the Application API
boundary.

## Decision

The Application API adds `AgentSession.transcript_history()` and a
`TranscriptHistoryPage` DTO.

- An omitted cursor returns the newest bounded safe transcript entries.
- `before_sequence` is an exclusive transcript sequence cursor.
- Returned entries remain in canonical ascending display order.
- `previous_sequence` identifies the earliest returned entry and is the cursor
  for the next older page.
- `has_more` explicitly reports whether older entries remain.
- Page limits use the existing transcript maximum.
- Persistent storage scans append-only Session truth while retaining at most
  `limit + 1` decoded records in memory.
- The projection has exactly the same privacy boundary as the forward
  transcript: no system messages, Provider-private state, provenance, or raw
  tool arguments.

Forward pagination remains available for synchronization and existing hosts.
Reverse pagination is an additional observation contract, not a replacement.

## Consequences

Desktop can open a long Session with bounded transfer and rendering cost, then
prepend older pages while preserving the scroll anchor. The Runtime remains the
only owner of canonical Session parsing.

Without a persistent sequence index, each reverse page still performs a linear
file scan. Memory and transport stay bounded; an index may be added later as
recoverable derived state if measured Session sizes justify it.

## References

- [ADR-0006](0006-desktop-consumer-observation-boundary.md)
- [Canonical design](../../DESIGN.md)
