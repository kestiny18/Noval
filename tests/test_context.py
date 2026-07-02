import json

import pytest

from noval.agent import Agent
from noval.client import MockClient, mock_text
from noval.config import Config
from noval.context import (
    ContextLimitError, ContextManager, JsonlCheckpointStore,
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
    store.append({"role": "user", "content": f"question-{number}"})
    if tool:
        store.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": f"call-{number}",
                "type": "function",
                "function": {"name": "read_file", "arguments": "{}"},
            }],
            "reasoning_content": "protocol state",
        })
        store.append({
            "role": "tool", "tool_call_id": f"call-{number}", "content": "result",
        })
    store.append({"role": "assistant", "content": f"answer-{number}"})


def active_messages(store):
    return [{"role": "system", "content": "system"}] + store.load()


def agent_config():
    return Config(
        model="model-a", base_url="u", api_key_env="K", max_steps=5,
        max_tool_output_chars=8000,
    )


def context_summary(label="summary"):
    return (
        f"## 当前目标\n{label}\n"
        "## 用户决策\n（无）\n"
        "## 已确认事实\n（无）\n"
        "## 已完成操作\n（无）\n"
        "## 验证结果\n（无）\n"
        "## 尚未验证的假设\n（无）\n"
        "## 未完成任务\n（无）\n"
        "## 相关文件与标识\n（无）"
    )


def test_first_compaction_persists_checkpoint_and_keeps_raw_history(tmp_path):
    store = make_store(tmp_path)
    for number in range(8):
        append_turn(store, number)
    original = store.load()
    manager = ContextManager(
        MockClient([mock_text(context_summary("继续任务"))]),
        store,
        "model-a",
        100,
        estimator=MessageCountEstimator(),
    )

    compacted = manager.prepare(active_messages(store), [])

    assert store.load() == original
    assert compacted[0]["role"] == "system"
    assert compacted[1]["role"] == "user"
    assert "<historical_context" in compacted[1]["content"]
    assert "继续任务" in compacted[1]["content"]
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
    if store._fh:
        store._fh.close()
    reopened = JsonlSessionStore.open(
        store.base_dir, store.workdir, store.session_id, "model-a",
    )

    resumed = ContextManager(
        MockClient([]), reopened, "model-a", 100,
        estimator=MessageCountEstimator(),
    )
    restored = resumed.restore()

    assert len(restored) == 3
    assert "summary" in restored[0]["content"]
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
    assert "summary-two" in compacted[1]["content"]


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
    assert "损坏" in caplog.text
    assert "无效" in caplog.text


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
        [{"role": "system", "content": "system"}] + second.restore(), [],
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
        MockClient([mock_text("## 当前目标\n只有一个章节")]),
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

    with pytest.raises(ContextLimitError, match="压缩后上下文仍约"):
        manager.prepare(active_messages(store), [])
    assert manager.checkpoint is None
    assert not store.context_path().exists()


def test_compaction_boundary_never_splits_tool_protocol(tmp_path):
    store = make_store(tmp_path)
    append_turn(store, 0, tool=True)
    append_turn(store, 1)
    manager = ContextManager(
        MockClient([mock_text(context_summary())]), store, "model-a", 50,
        estimator=MessageCountEstimator(),
        preferred_recent_turns=1,
    )

    compacted = manager.prepare(active_messages(store), [])

    assert manager.checkpoint is not None
    assert manager.checkpoint.source_through_seq == 3
    assert compacted[-2:] == store.load()[-2:]
    assert not any(message.get("tool_call_id") == "call-0" for message in compacted)


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
    incomplete.append({"role": "user", "content": "unfinished"})
    hard = ContextManager(
        MockClient([]), incomplete, "model-a", 100, estimator=FixedEstimator(90),
    )
    with pytest.raises(ContextLimitError, match="没有可安全压缩"):
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
    store.append({"role": "user", "content": "current question"})
    manager = ContextManager(
        MockClient([mock_text(context_summary())]), store, "model-a", 50,
        estimator=MessageCountEstimator(),
    )

    compacted = manager.prepare(active_messages(store), [])

    assert manager.checkpoint is not None
    assert manager.checkpoint.source_through_seq == 1
    assert compacted[-1]["content"] == "current question"


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

    assert "<source_records>" in client.seen_messages[0][-1]["content"]
    model_request = client.seen_messages[1]
    assert any("<historical_context" in m.get("content", "") for m in model_request)
    raw_messages = store.load()
    assert not any("<historical_context" in m.get("content", "") for m in raw_messages)
    assert raw_messages[-1]["content"] == "final answer"
