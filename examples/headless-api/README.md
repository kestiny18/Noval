# Headless API example

This example runs two independent Noval Sessions concurrently in one Python
process without network access. It injects `MockClient` instances through the
native `client_factory` extension port, while requests, results, and events use
the same public contracts as a real host.

From the repository root:

```bash
pip install -e .
python examples/headless-api/main.py
```

The example demonstrates:

- one process-scoped `NovalRuntime`;
- two ephemeral Sessions with different workdirs;
- parallel turns across Sessions;
- JSON-safe `TurnResult` and live `RuntimeEvent` values;
- safe transcript paging, Session rename, and bounded live event replay;
- no Provider credentials or network calls.

Production hosts normally use `NovalRuntime.from_settings()` and choose
`SessionPersistence.PERSISTENT`. A single Session still accepts only one active
turn; hosts own any queue or retry policy for `session_busy`.
