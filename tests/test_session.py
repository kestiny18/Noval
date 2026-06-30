import json

import pytest

from noval.session import JsonlSessionStore, list_sessions


def _session_file(base_dir, workdir, session_id):
    [project_dir] = list(base_dir.iterdir())
    return project_dir / f"{session_id}.jsonl"


def test_create_is_lazy_until_first_append(tmp_path):
    base = tmp_path / "sessions"
    workdir = tmp_path / "project"
    workdir.mkdir()

    store = JsonlSessionStore.create(base, workdir, "model-a")

    assert not base.exists()

    msg = {"role": "user", "content": "<context>当前时间: x</context>\n\nhello"}
    store.append(msg)

    path = _session_file(base, workdir, store.session_id)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["_meta"]["session_id"] == store.session_id
    assert json.loads(lines[1])["seq"] == 0
    assert json.loads(lines[1])["msg"] == msg
    assert store.load() == [msg]
    assert (path.parent / "project.json").exists()


def test_open_continues_seq_numbers(tmp_path):
    base = tmp_path / "sessions"
    workdir = tmp_path / "project"
    workdir.mkdir()

    store = JsonlSessionStore.create(base, workdir, "model-a")
    store.append({"role": "user", "content": "one"})
    store.append({"role": "assistant", "content": "two"})
    if store._fh:
        store._fh.close()

    resumed = JsonlSessionStore.open(base, workdir, store.session_id, "model-a")
    resumed.append({"role": "user", "content": "three"})

    path = _session_file(base, workdir, store.session_id)
    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and "seq" in json.loads(line)
    ]
    assert [r["seq"] for r in records] == [0, 1, 2]


def test_load_skips_bad_lines_and_half_written_tail(tmp_path):
    base = tmp_path / "sessions"
    workdir = tmp_path / "project"
    workdir.mkdir()

    store = JsonlSessionStore.create(base, workdir, "model-a")
    store.append({"role": "user", "content": "ok"})
    if store._fh:
        store._fh.close()

    path = _session_file(base, workdir, store.session_id)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{bad json\n")
        fh.write(json.dumps({"seq": 1, "ts": "t", "msg": {"role": "assistant", "content": "still ok"}}, ensure_ascii=False) + "\n")
        fh.write('{"seq": 2, "ts": "t", "msg": ')

    assert store.load() == [
        {"role": "user", "content": "ok"},
        {"role": "assistant", "content": "still ok"},
    ]


def test_list_sessions_derives_title_and_sidecar_overrides(tmp_path):
    base = tmp_path / "sessions"
    workdir = tmp_path / "project"
    workdir.mkdir()

    store = JsonlSessionStore.create(base, workdir, "model-a")
    store.append({
        "role": "user",
        "content": "<context>当前时间: 2026</context>\n\n请解释 session store",
    })
    if store._fh:
        store._fh.close()

    [meta] = list_sessions(base, workdir)
    assert meta.session_id == store.session_id
    assert meta.title == "请解释 session store"
    assert meta.message_count == 1
    assert meta.model == "model-a"

    store.set_title("自定义标题")
    [meta] = list_sessions(base, workdir)
    assert meta.title == "自定义标题"


def test_sidecar_metadata_merges_and_keeps_lazy_creation(tmp_path):
    base = tmp_path / "sessions"
    workdir = tmp_path / "project"
    workdir.mkdir()
    store = JsonlSessionStore.create(base, workdir, "model-a")

    store.update_metadata({"permissions": {"mode": "full_access", "approved_tools": []}})
    store.set_title("保留标题")
    assert not base.exists()                       # 空会话仍不落盘

    store.append({"role": "user", "content": "hello"})
    metadata = store.load_metadata()
    assert metadata["title"] == "保留标题"
    assert metadata["permissions"]["mode"] == "full_access"


def test_open_loads_existing_sidecar_metadata(tmp_path):
    base = tmp_path / "sessions"
    workdir = tmp_path / "project"
    workdir.mkdir()
    store = JsonlSessionStore.create(base, workdir, "model-a")
    store.append({"role": "user", "content": "hello"})
    store.update_metadata({"permissions": {
        "mode": "ask",
        "approved_tools": ["run_bash"],
    }})

    resumed = JsonlSessionStore.open(base, workdir, store.session_id, "model-a")
    assert resumed.load_metadata()["permissions"]["approved_tools"] == ["run_bash"]


def test_reasoning_content_round_trips_for_tool_call(tmp_path):
    base = tmp_path / "sessions"
    workdir = tmp_path / "project"
    workdir.mkdir()
    store = JsonlSessionStore.create(base, workdir, "model-a")
    message = {
        "role": "assistant",
        "content": None,
        "reasoning_content": "required replay state",
        "tool_calls": [{
            "id": "call-1",
            "type": "function",
            "function": {"name": "read_file", "arguments": "{}"},
        }],
    }

    store.append(message)

    resumed = JsonlSessionStore.open(base, workdir, store.session_id, "model-a")
    assert resumed.load() == [message]


def test_open_missing_session_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        JsonlSessionStore.open(tmp_path / "sessions", tmp_path, "missing", "model-a")


@pytest.mark.parametrize("session_id", ["", "../outside", "..\\outside", "C:/outside"])
def test_session_id_cannot_escape_project_directory(tmp_path, session_id):
    with pytest.raises(ValueError, match="非法会话 ID"):
        JsonlSessionStore.open(tmp_path / "sessions", tmp_path, session_id, "model-a")
