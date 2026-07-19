import json

import pytest

from noval.agent import Agent
from noval.client import MockClient, mock_text
from noval.config import Config
from noval.context import (
    ContextLimitError, ContextManager, JsonlCheckpointStore,
)
from noval.messages import (
    AdapterReplayState, MessageRole, ToolCallBlock, assistant_message,
    system_message, tool_result_message, user_message,
)
from noval.session import JsonlSessionStore


class MessageCountEstimator:
    def estimate(self, messages, tools):
        return len(messages) * 10

    def observe(self, messages, tools, actual_prompt_tokens):
        pass


class FixedEstimator:
    def __init__(self, value):
        self.value = value

    def estimate(self, messages, tools):
        return self.value

    def observe(self, messages, tools, actual_prompt_tokens):
        pass


def make_store(tmp_path):
    workdir = tmp_path / "project"
    workdir.mkdir(parents=True)
    return JsonlSessionStore.create(tmp_path / "sessions", workdir, "model-a")


def append_turn(store, number, *, tool=False):
    store.append(user_message(f"question-{number}"))
    if tool:
        store.append(assistant_message(
            tool_calls=(ToolCallBlock(f"call-{number}", "read_file", "{}"),),
            replay_state=AdapterReplayState("test", 1, {"private": "protocol state"}),
        ))
        store.append(tool_result_message(f"call-{number}", "result"))
    store.append(assistant_message(f"answer-{number}"))


def active_messages(store):
    return [system_message("system")] + store.load()


def agent_config():
    return Config(
        model="model-a", base_url="u", api_key_env="K", max_steps=5,
        max_tool_output_chars=8000,
    )


def context_summary(label="summary"):
    return (
        f"## Current Goal\n{label}\n"
        "## User Decisions\n(none)\n"
        "## Confirmed Facts\n(none)\n"
        "## Completed Actions\n(none)\n"
        "## Verification Results\n(none)\n"
        "## Unverified Hypotheses\n(none)\n"
        "## Pending Tasks\n(none)\n"
        "## Relevant Files and Identifiers\n(none)"
    )


def test_first_compaction_persists_checkpoint_and_keeps_raw_history(tmp_path):
    store = make_store(tmp_path)
    for number in range(8):
        append_turn(store, number)
    original = store.load()
    manager = ContextManager(
        MockClient([mock_text(context_summary("Continue the task"))]),
        store,
        "model-a",
        100,
        estimator=MessageCountEstimator(),
    )

    compacted = manager.prepare(active_messages(store), [])

    assert store.load() == original
    assert compacted[0].role is MessageRole.SYSTEM
    assert compacted[1].role is MessageRole.USER
    assert "<historical_context" in compacted[1].text
    assert "Continue the task" in compacted[1].text
    assert compacted[-2:] == original[-2:]
    assert manager.checkpoint is not None
    assert manager.checkpoint.source_from_seq == 0
    assert manager.checkpoint.source_through_seq == 13
    assert store.context_path().exists()


def test_below_trigger_does_not_call_compactor_or_create_checkpoint(tmp_path):
    store = make_store(tmp_path)
    for number in range(2):
        append_turn(store, number)
    manager = ContextManager(
        MockClient([]), store, "model-a", 100, estimator=FixedEstimator(69),
    )
    messages = active_messages(store)

    assert manager.prepare(messages, []) is messages
    assert manager.checkpoint is None
    assert not store.context_path().exists()


def test_resume_reuses_checkpoint_without_calling_model(tmp_path):
    store = make_store(tmp_path)
    for number in range(8):
        append_turn(store, number)
    first = ContextManager(
        MockClient([mock_text(context_summary())]), store, "model-a", 100,
        estimator=MessageCountEstimator(),
    )
    first.prepare(active_messages(store), [])
    store.close()
    reopened = JsonlSessionStore.open(
        store.base_dir, store.workdir, store.session_id, "model-a",
    )

    resumed = ContextManager(
        MockClient([]), reopened, "model-a", 100,
        estimator=MessageCountEstimator(),
    )
    restored = resumed.restore()

    assert len(restored) == 3
    assert "summary" in restored[0].text
    assert restored[1:] == reopened.load()[-2:]


def test_second_compaction_only_covers_new_tail_and_links_previous(tmp_path):
    store = make_store(tmp_path)
    for number in range(8):
        append_turn(store, number)
    first = ContextManager(
        MockClient([mock_text(context_summary("summary-one"))]), store, "model-a", 100,
        estimator=MessageCountEstimator(),
    )
    active = first.prepare(active_messages(store), [])
    first_checkpoint = first.checkpoint
    assert first_checkpoint is not None

    new_messages = []
    for number in range(8, 12):
        before = len(store.load())
        append_turn(store, number)
        new_messages.extend(store.load()[before:])
    active.extend(new_messages)
    second = ContextManager(
        MockClient([mock_text(context_summary("summary-two"))]), store, "model-a", 100,
        estimator=MessageCountEstimator(),
    )

    compacted = second.prepare(active, [])

    checkpoint = second.checkpoint
    assert checkpoint is not None
    assert checkpoint.previous_checkpoint_id == first_checkpoint.checkpoint_id
    assert checkpoint.source_from_seq == first_checkpoint.source_through_seq + 1
    assert "summary-two" in compacted[1].text


def test_checkpoint_loader_skips_corrupt_tail_and_invalid_source(tmp_path, caplog):
    store = make_store(tmp_path)
    for number in range(8):
        append_turn(store, number)
    manager = ContextManager(
        MockClient([mock_text(context_summary("valid summary"))]), store, "model-a", 100,
        estimator=MessageCountEstimator(),
    )
    manager.prepare(active_messages(store), [])
    valid = manager.checkpoint
    assert valid is not None
    with store.context_path().open("a", encoding="utf-8") as file:
        file.write("not-json\n")
        invalid = valid.to_dict()
        invalid["checkpoint_id"] = "ctx-invalid"
        invalid["source"]["previous_checkpoint_id"] = valid.checkpoint_id
        invalid["source"]["from_seq"] = valid.source_through_seq + 1
        invalid["source"]["through_seq"] = valid.source_through_seq + 1
        invalid["source"]["source_hash"] = "sha256:wrong"
        file.write(json.dumps(invalid, ensure_ascii=False) + "\n")

    latest = JsonlCheckpointStore(
        store.context_path(), store.session_id
    ).load_latest(store.load_records())

    assert latest == valid
    assert "corrupt" in caplog.text
    assert "invalid" in caplog.text


def test_v1_checkpoint_is_not_reused(tmp_path):
    store = make_store(tmp_path)
    append_turn(store, 0)
    path = store.context_path()
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"_meta": {"schema_version": 1, "session_id": store.session_id}})
        + "\n"
        + json.dumps({"schema_version": 1, "checkpoint_id": "old"})
        + "\n",
        encoding="utf-8",
    )

    manager = ContextManager(MockClient([]), store, "model-a", 100)

    assert manager.checkpoint is None
    assert manager.restore() == store.load()


def test_append_after_partial_checkpoint_line_remains_recoverable(tmp_path):
    store = make_store(tmp_path)
    for number in range(8):
        append_turn(store, number)
    first = ContextManager(
        MockClient([mock_text(context_summary("first"))]), store, "model-a", 100,
        estimator=MessageCountEstimator(),
    )
    first.prepare(active_messages(store), [])
    first_checkpoint = first.checkpoint
    assert first_checkpoint is not None
    with store.context_path().open("a", encoding="utf-8") as file:
        file.write('{"partial":')
    for number in range(8, 12):
        append_turn(store, number)

    second = ContextManager(
        MockClient([mock_text(context_summary("second"))]), store, "model-a", 100,
        estimator=MessageCountEstimator(),
    )
    second.prepare(
        [system_message("system")] + second.restore(), [],
    )

    latest = JsonlCheckpointStore(
        store.context_path(), store.session_id,
    ).load_latest(store.load_records())
    assert latest == second.checkpoint
    assert latest is not None
    assert latest.previous_checkpoint_id == first_checkpoint.checkpoint_id


def test_summary_missing_required_sections_is_not_persisted(tmp_path):
    store = make_store(tmp_path)
    for number in range(2):
        append_turn(store, number)
    messages = active_messages(store)
    manager = ContextManager(
        MockClient([mock_text("## Current Goal\nOnly one section")]),
        store,
        "model-a",
        100,
        estimator=FixedEstimator(75),
        preferred_recent_turns=1,
    )

    assert manager.prepare(messages, []) is messages
    assert manager.checkpoint is None
    assert not store.context_path().exists()


def test_compacted_context_over_hard_limit_is_not_persisted(tmp_path):
    store = make_store(tmp_path)
    for number in range(2):
        append_turn(store, number)
    manager = ContextManager(
        MockClient([mock_text(context_summary())]),
        store,
        "model-a",
        100,
        estimator=FixedEstimator(90),
        preferred_recent_turns=1,
    )

    with pytest.raises(ContextLimitError, match="compacted context is still approximately"):
        manager.prepare(active_messages(store), [])
    assert manager.checkpoint is None
    assert not store.context_path().exists()


def test_compaction_boundary_never_splits_tool_protocol(tmp_path):
    store = make_store(tmp_path)
    append_turn(store, 0, tool=True)
    append_turn(store, 1)
    client = MockClient([mock_text(context_summary())])
    manager = ContextManager(
        client, store, "model-a", 50,
        estimator=MessageCountEstimator(),
        preferred_recent_turns=1,
    )

    compacted = manager.prepare(active_messages(store), [])

    assert manager.checkpoint is not None
    assert manager.checkpoint.source_through_seq == 3
    assert compacted[-2:] == store.load()[-2:]
    assert not any(
        result.call_id == "call-0"
        for message in compacted for result in message.tool_results
    )
    assert "protocol state" not in client.seen_messages[0][-1].text
    assert "replay_state" not in client.seen_messages[0][-1].text


def test_soft_failure_keeps_original_but_hard_limit_stops(tmp_path):
    store = make_store(tmp_path)
    for number in range(2):
        append_turn(store, number)
    messages = active_messages(store)
    soft = ContextManager(
        MockClient([]), store, "model-a", 100, estimator=FixedEstimator(75),
        preferred_recent_turns=1,
    )

    assert soft.prepare(messages, []) is messages

    incomplete = make_store(tmp_path / "other")
    incomplete.append(user_message("unfinished"))
    hard = ContextManager(
        MockClient([]), incomplete, "model-a", 100, estimator=FixedEstimator(90),
    )
    with pytest.raises(ContextLimitError, match="no complete historical turn can be compacted safely"):
        hard.prepare(active_messages(incomplete), [])


def test_checkpoint_write_failure_never_replaces_active_context(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    for number in range(2):
        append_turn(store, number)
    manager = ContextManager(
        MockClient([mock_text(context_summary())]), store, "model-a", 100,
        estimator=FixedEstimator(75), preferred_recent_turns=1,
    )
    def fail_write(checkpoint):
        raise OSError("full")

    monkeypatch.setattr(manager.checkpoints, "append", fail_write)
    messages = active_messages(store)

    assert manager.prepare(messages, []) is messages
    assert manager.checkpoint is None


def test_completed_turn_can_compact_while_current_turn_is_incomplete(tmp_path):
    store = make_store(tmp_path)
    append_turn(store, 0)
    store.append(user_message("current question"))
    manager = ContextManager(
        MockClient([mock_text(context_summary())]), store, "model-a", 50,
        estimator=MessageCountEstimator(),
    )

    compacted = manager.prepare(active_messages(store), [])

    assert manager.checkpoint is not None
    assert manager.checkpoint.source_through_seq == 1
    assert compacted[-1].text == "current question"


def test_agent_compacts_before_provider_call_without_persisting_summary(tmp_path):
    store = make_store(tmp_path)
    for number in range(8):
        append_turn(store, number)
    client = MockClient([mock_text(context_summary()), mock_text("final answer")])
    manager = ContextManager(
        client, store, "model-a", 100, estimator=MessageCountEstimator(),
    )
    agent = Agent(
        client, agent_config(), store=store, resume_messages=manager.restore(),
        context_manager=manager,
    )

    assert agent.send("new question") == "final answer"

    assert "<source_records>" in client.seen_messages[0][-1].text
    model_request = client.seen_messages[1]
    assert any("<historical_context" in message.text for message in model_request)
    raw_messages = store.load()
    assert not any("<historical_context" in message.text for message in raw_messages)
    assert raw_messages[-1].text == "final answer"
