import json

import pytest

from noval.messages import (
    AdapterReplayState, MessageProvenance, ToolCallBlock, assistant_message, user_message,
)
from noval.session import (
    SCHEMA_VERSION, JsonlSessionStore, UnsupportedSessionVersion, list_sessions,
)


def session_file(base_dir, session_id):
    [project_dir] = list(base_dir.iterdir())
    return project_dir / f"{session_id}.jsonl"


def close(store):
    if store._fh:
        store._fh.close()


def test_create_is_lazy_and_writes_canonical_schema_v2(tmp_path):
    base = tmp_path / "sessions"
    workdir = tmp_path / "project"
    workdir.mkdir()
    store = JsonlSessionStore.create(base, workdir, "model-a")
    assert not base.exists()

    message = user_message("<context>当前时间: x</context>\n\nhello")
    store.append(message)
    close(store)

    path = session_file(base, store.session_id)
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["_meta"]["schema_version"] == SCHEMA_VERSION == 2
    assert lines[1]["seq"] == 0
    assert lines[1]["message"] == message.to_dict()
    assert store.load() == [message]
    [record] = store.load_records()
    assert record.message == message
    assert record.ts
    assert (path.parent / "project.json").exists()


def test_context_path_is_outside_session_jsonl_scan(tmp_path):
    base = tmp_path / "sessions"
    workdir = tmp_path / "project"
    workdir.mkdir()
    store = JsonlSessionStore.create(base, workdir, "model-a")
    store.append(user_message("hello"))
    close(store)

    path = store.context_path()
    assert path.parent.name == "context"
    assert path.name == f"{store.session_id}.jsonl"
    assert path.parent.parent == session_file(base, store.session_id).parent


def test_open_continues_seq_numbers(tmp_path):
    base = tmp_path / "sessions"
    workdir = tmp_path / "project"
    workdir.mkdir()
    store = JsonlSessionStore.create(base, workdir, "model-a")
    store.append(user_message("one"))
    store.append(assistant_message("two"))
    close(store)

    resumed = JsonlSessionStore.open(base, workdir, store.session_id, "model-a")
    resumed.append(user_message("three"))
    close(resumed)

    records = [
        item for item in map(json.loads, session_file(base, store.session_id).read_text().splitlines())
        if "seq" in item
    ]
    assert [record["seq"] for record in records] == [0, 1, 2]


def test_load_skips_bad_lines_and_invalid_canonical_messages(tmp_path, caplog):
    base = tmp_path / "sessions"
    workdir = tmp_path / "project"
    workdir.mkdir()
    store = JsonlSessionStore.create(base, workdir, "model-a")
    store.append(user_message("ok"))
    close(store)
    path = session_file(base, store.session_id)
    with path.open("a", encoding="utf-8") as file:
        file.write("{bad json\n")
        file.write(json.dumps({
            "seq": 1, "ts": "t", "message": assistant_message("still ok").to_dict(),
        }, ensure_ascii=False) + "\n")
        file.write(json.dumps({
            "seq": 2, "ts": "t", "message": {"role": "assistant", "blocks": "bad"},
        }) + "\n")

    assert store.load() == [user_message("ok"), assistant_message("still ok")]
    assert "损坏" in caplog.text


def test_list_sessions_derives_title_and_sidecar_overrides(tmp_path):
    base = tmp_path / "sessions"
    workdir = tmp_path / "project"
    workdir.mkdir()
    store = JsonlSessionStore.create(base, workdir, "model-a")
    store.append(user_message("<context>当前时间: 2026</context>\n\n请解释 session store"))
    close(store)

    [meta] = list_sessions(base, workdir)
    assert meta.title == "请解释 session store"
    assert meta.compatible is True
    assert meta.schema_version == 2
    store.set_title("自定义标题")
    assert list_sessions(base, workdir)[0].title == "自定义标题"


def test_sidecar_metadata_merges_and_keeps_lazy_creation(tmp_path):
    base = tmp_path / "sessions"
    workdir = tmp_path / "project"
    workdir.mkdir()
    store = JsonlSessionStore.create(base, workdir, "model-a")
    store.update_metadata({"permissions": {"mode": "full_access", "approved_tools": []}})
    store.set_title("保留标题")
    assert not base.exists()

    store.append(user_message("hello"))
    close(store)
    metadata = store.load_metadata()
    assert metadata["title"] == "保留标题"
    assert metadata["permissions"]["mode"] == "full_access"


def test_adapter_replay_state_and_provenance_round_trip(tmp_path):
    base = tmp_path / "sessions"
    workdir = tmp_path / "project"
    workdir.mkdir()
    store = JsonlSessionStore.create(base, workdir, "model-a")
    message = assistant_message(
        tool_calls=(ToolCallBlock("call-1", "read_file", "{}"),),
        replay_state=AdapterReplayState("test-adapter", 1, {"opaque": ["exact"]}),
        provenance=MessageProvenance("test", "model-a", "test-adapter", 1),
    )
    store.append(message)
    close(store)

    resumed = JsonlSessionStore.open(base, workdir, store.session_id, "model-a")
    assert resumed.load() == [message]


def test_v1_session_is_listed_as_incompatible_and_open_fails_without_mutation(tmp_path):
    base = tmp_path / "sessions"
    workdir = tmp_path / "project"
    workdir.mkdir()
    seed = JsonlSessionStore.create(base, workdir, "model-a")
    path = seed._dir / "legacy.jsonl"
    path.parent.mkdir(parents=True)
    original = "\n".join([
        json.dumps({"_meta": {"schema_version": 1, "session_id": "legacy", "model": "m"}}),
        json.dumps({"seq": 0, "ts": "t", "msg": {"role": "user", "content": "old"}}),
    ]) + "\n"
    path.write_text(original, encoding="utf-8")

    [meta] = list_sessions(base, workdir)
    assert meta.compatible is False
    assert meta.schema_version == 1
    assert "不兼容" in meta.title
    with pytest.raises(UnsupportedSessionVersion, match="schema v1"):
        JsonlSessionStore.open(base, workdir, "legacy", "model-a")
    assert path.read_text(encoding="utf-8") == original


def test_open_missing_session_and_invalid_id_raise(tmp_path):
    with pytest.raises(FileNotFoundError):
        JsonlSessionStore.open(tmp_path / "sessions", tmp_path, "missing", "model-a")
    for session_id in ("", "../outside", "..\\outside", "C:/outside"):
        with pytest.raises(ValueError, match="非法会话 ID"):
            JsonlSessionStore.open(tmp_path / "sessions", tmp_path, session_id, "model-a")
