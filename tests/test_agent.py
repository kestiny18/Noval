"""agent 循环测试：用 MockClient 跑完整条「工具调用 → 回填 → 最终回复」闭环，离线零成本。"""
import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from noval.agent import (
    Agent, AgentTurnOutcome, _choose_resume_session, _create_permission_controller, _format_turn,
    _format_reasoning_summary, _handle_permissions_command, _handle_reasoning_command,
    _read_turn,
    _supports_color, _tool_arg_keys, _turn_prefix, detect_environment,
    load_project_memory, run_cli, TurnMetrics,
)
from noval.client import (
    LLMResponse, MockClient, ProviderIdentity, TokenUsage, mock_text, mock_tool_call,
)
from noval.config import Config
from noval.permissions import PermissionController, PermissionMode
from noval.messages import (
    MessageRole, ToolCallBlock, assistant_message, tool_result_message, user_message,
)
from noval.process import NoSandbox, ProcessRuntime
from noval.session import JsonlSessionStore, SessionMeta
from noval.shell import ShellBackend, to_bash_path
from noval.skills import SkillRegistry


def _multi_tool_call(calls):
    """构造一个「一轮返回多个 tool_call」的响应。calls: [(id, name, args_json), ...]"""
    identity = ProviderIdentity("mock", "mock", "mock")
    return LLMResponse(
        message=assistant_message(
            tool_calls=[ToolCallBlock(i, n, a) for i, n, a in calls],
            provenance=identity.provenance(),
        ),
        provider=identity,
    )


def cfg():
    return Config(
        model="m", base_url="u", api_key_env="K", max_steps=5,
        max_tool_output_chars=8000,
    )


def test_full_tool_loop(tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("机密内容", encoding="utf-8")

    # 脚本：第一轮请求调 read_file，第二轮基于结果给出最终回复
    client = MockClient([
        mock_tool_call("c1", "read_file", json.dumps({"path": str(f)})),
        mock_text("文件里写着：机密内容"),
    ])
    agent = Agent(client, cfg(), workdir=str(tmp_path))

    reply = agent.send("看看 doc.txt 写了什么")
    assert reply == "文件里写着：机密内容"

    # 工具结果确实被回填进历史（第二轮请求时模型能看到）
    second_request = client.seen_messages[1]
    tool_msgs = [m for m in second_request if m.role is MessageRole.TOOL]
    assert tool_msgs and "机密内容" in tool_msgs[0].tool_results[0].content


def test_no_tool_call_returns_directly():
    client = MockClient([mock_text("你好！")])
    agent = Agent(client, cfg())
    assert agent.send("hi") == "你好！"


def test_structured_agent_turn_outcome_replaces_metric_side_channel():
    client = MockClient([
        mock_text(
            "done",
            meta={"duration_ms": 250},
            usage=TokenUsage(12, 3, 15, reasoning_tokens=2),
        )
    ])
    agent = Agent(client, cfg())

    outcome = agent.run_turn("hi")

    assert isinstance(outcome, AgentTurnOutcome)
    assert outcome.message == assistant_message(
        "done", provenance=ProviderIdentity("mock", "mock", "mock").provenance()
    )
    assert outcome.text == "done"
    assert outcome.stop_reason == "completed"
    assert outcome.metrics.api_calls == 1
    assert outcome.metrics.llm_duration_ms == 250
    assert outcome.usage == TokenUsage(12, 3, 15, reasoning_tokens=2)


def test_agent_observer_is_ordered_and_failure_is_isolated(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("hello", encoding="utf-8")
    events = []

    def observer(event_type, payload):
        events.append((event_type, payload))
        if event_type == "tool.completed":
            raise RuntimeError("observer is not control flow")

    client = MockClient([
        mock_tool_call("c1", "read_file", json.dumps({"path": str(target)})),
        mock_text("done"),
    ])
    outcome = Agent(client, cfg(), workdir=str(tmp_path), observer=observer).run_turn("read")

    assert outcome.text == "done"
    event_types = [event_type for event_type, _ in events]
    assert event_types == [
        "model.started",
        "model.completed",
        "tool.started",
        "tool.completed",
        "model.started",
        "model.completed",
    ]
    completed = next(payload for event, payload in events if event == "tool.completed")
    assert completed["tool_name"] == "read_file"
    assert completed["content"].endswith("hello")


def test_provider_receives_schema_only_tool_definitions(tmp_path):
    client = MockClient([mock_text("done")])
    Agent(client, cfg(), workdir=str(tmp_path)).send("hello")

    definition = client.seen_tools[0][0]
    assert definition.name
    assert definition.description
    assert isinstance(definition.input_schema, dict)
    assert not hasattr(definition, "func")
    assert not hasattr(definition, "risk")


def test_multiple_tool_calls_all_backfilled(tmp_path):
    # 一轮多个 tool_call，其中一个失败：回填的 tool 消息数必须 == call 数（否则下一轮 API 拒绝）
    f = tmp_path / "a.txt"
    f.write_text("hi", encoding="utf-8")
    client = MockClient([
        _multi_tool_call([
            ("c1", "read_file", json.dumps({"path": str(f)})),   # 成功
            ("c2", "read_file", json.dumps({})),                  # 缺 path → 报错
        ]),
        mock_text("done"),
    ])
    agent = Agent(client, cfg(), workdir=str(tmp_path))
    assert agent.send("read two") == "done"
    tool_msgs = [m for m in client.seen_messages[1] if m.role is MessageRole.TOOL]
    assert len(tool_msgs) == 2
    assert {m.tool_results[0].call_id for m in tool_msgs} == {"c1", "c2"}


def test_reasoning_replay_and_turn_metrics_across_tool_loop(tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("hello", encoding="utf-8")
    client = MockClient([
        mock_tool_call(
            "c1", "read_file", json.dumps({"path": str(f)}),
            reasoning_content="need to inspect the file",
            meta={
                "thinking_enabled": True,
                "duration_ms": 1200,
            },
            usage=TokenUsage(100, 20, 120, reasoning_tokens=20),
        ),
        mock_text("done", meta={
            "thinking_enabled": True,
            "duration_ms": 300,
        }, usage=TokenUsage(120, 10, 130, reasoning_tokens=5)),
    ])
    store = _MemoryStore()
    agent = Agent(client, cfg(), workdir=str(tmp_path), store=store)

    assert agent.send("read") == "done"

    replayed = client.seen_messages[1]
    assistant = next(m for m in replayed if m.role is MessageRole.ASSISTANT)
    assert assistant.replay_state.payload["reasoning_content"] == "need to inspect the file"
    assert any(
        m.replay_state is not None
        and m.replay_state.payload.get("reasoning_content") == "need to inspect the file"
        for m in store.saved
    )
    assert agent.last_turn_metrics.reasoning_tokens == 25
    assert agent.last_turn_metrics.llm_duration_ms == 1500
    assert agent.last_turn_metrics.tool_calls == 1
    assert agent.last_turn_metrics.api_calls == 2


def test_answer_pending_tool_calls_backfills():
    # 中断后补齐未回填的 tool_call，保持历史合法
    agent = Agent(MockClient([mock_text("x")]), cfg())
    agent.messages.append(assistant_message(tool_calls=(
        ToolCallBlock("a", "t", "{}"), ToolCallBlock("b", "t", "{}"),
    )))
    agent.messages.append(tool_result_message("a", "ok"))
    agent._answer_pending_tool_calls()
    tool_ids = [
        m.tool_results[0].call_id for m in agent.messages if m.role is MessageRole.TOOL
    ]
    assert tool_ids == ["a", "b"]                       # b 被补上


def test_workdir_defaults_to_cwd():
    agent = Agent(MockClient([mock_text("hi")]), cfg())
    assert agent.workdir == Path(os.getcwd()).resolve()


def test_workdir_explicit(tmp_path):
    agent = Agent(MockClient([mock_text("hi")]), cfg(), workdir=str(tmp_path))
    assert agent.workdir == tmp_path.resolve()


def test_to_bash_path():
    assert to_bash_path("C:\\Users\\x", "WSL") == "/mnt/c/Users/x"
    assert to_bash_path("E:/Work/y", "Git Bash") == "/e/Work/y"
    assert to_bash_path("/already/unix", "WSL") is None        # 非 Windows 路径不转


def test_detect_environment_has_basics(tmp_path):
    backend = ShellBackend("C:/Git/bin/bash.exe", "Git Bash", "MINGW64_NT", "path hint")
    env = detect_environment(tmp_path, backend)
    assert "<environment>" in env and "workdir" in env
    assert str(tmp_path) in env
    assert "Noval 主进程平台" in env
    assert "run_bash 执行后端: Git Bash" in env
    assert "子进程隔离" in env and "NoSandbox" in env
    assert "C:/Git/bin/bash.exe" in env
    assert "当前日期" not in env and "当前时间" not in env       # 时间不进 system 前缀


def test_agent_context_keeps_selected_shell_backend():
    backend = ShellBackend("chosen-bash", "Git Bash")
    agent = Agent(MockClient([mock_text("hi")]), cfg(), shell_backend=backend)
    assert agent.context.shell_backend is backend


def test_agent_context_keeps_single_process_runtime():
    runtime = ProcessRuntime()
    agent = Agent(MockClient([mock_text("hi")]), cfg(), process_runtime=runtime)

    assert agent.process_runtime is runtime
    assert agent.context.process_runtime is runtime
    assert agent.mcp_registry._client.runtime is runtime


def test_agent_derives_subprocess_roots_from_workdir(tmp_path):
    agent = Agent(MockClient([mock_text("hi")]), cfg(), workdir=str(tmp_path))

    assert agent.process_runtime.policy.read_roots == (tmp_path.resolve(),)
    assert agent.process_runtime.policy.write_roots == (tmp_path.resolve(),)


def test_cli_required_sandbox_failure_is_reported_by_application(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "noval.process.detect_sandbox_backend",
        lambda: NoSandbox("test fallback"),
    )
    monkeypatch.setattr("noval.cli.Config.load", lambda: cfg())

    previous_cwd = Path.cwd()
    try:
        with pytest.raises(SystemExit, match="NoSandbox"):
            run_cli(["--workdir", str(tmp_path), "--sandbox", "required"])
    finally:
        os.chdir(previous_cwd)


def test_tool_arg_keys_never_include_values():
    assert _tool_arg_keys('{"path":"C:/secret.txt","limit":20}') == ["limit", "path"]
    assert _tool_arg_keys("not json") == ["<invalid-json>"]


def test_tool_call_log_omits_argument_values(tmp_path, caplog):
    secret_path = tmp_path / "private-token-file.txt"
    secret_path.write_text("ok", encoding="utf-8")
    client = MockClient([
        mock_tool_call("c1", "read_file", json.dumps({"path": str(secret_path)})),
        mock_text("done"),
    ])
    caplog.set_level("INFO", logger="noval.agent")

    Agent(client, cfg(), workdir=str(tmp_path)).send("read it")

    assert "arg_keys=['path']" in caplog.text
    assert str(secret_path) not in caplog.text


def test_send_stamps_user_message_with_current_time():
    # 时间随回合注入 user 消息，不进 system —— 保前缀稳定 + 每轮刷新「现在」
    client = MockClient([mock_text("ok")])
    agent = Agent(client, cfg())
    agent.send("hello")
    sys_and_user = client.seen_messages[0]
    user_msg = sys_and_user[-1]
    assert user_msg.role is MessageRole.USER
    assert "当前时间" in user_msg.text
    assert datetime.now().strftime("%Y-%m-%d") in user_msg.text
    assert "hello" in user_msg.text
    # system 消息(前缀)里不含时间
    assert "当前时间" not in sys_and_user[0].text


def test_system_prompt_assembly_order():
    # 稳定性从高到低：人设 → 环境 → 项目记忆（缓存前缀尽量长 + 规则先立框）
    agent = Agent(
        MockClient([mock_text("hi")]), cfg(),
        system_prompt="PERSONA",
        env_context="<environment>E</environment>",
        project_memory="<project_instructions>P</project_instructions>",
    )
    c = agent.messages[0].text
    assert c.index("PERSONA") < c.index("<environment>") < c.index("<project_instructions>")


def test_default_system_prompt_when_not_overridden():
    from noval.agent import DEFAULT_SYSTEM_PROMPT
    agent = Agent(
        MockClient([mock_text("hi")]),
        cfg(),
        skill_registry=SkillRegistry([]),
    )        # 不传任何 system_prompt
    assert agent.messages[0].text == DEFAULT_SYSTEM_PROMPT
    assert "默认先只读调查" in DEFAULT_SYSTEM_PROMPT
    assert "等待确认后再执行" in DEFAULT_SYSTEM_PROMPT
    assert "FULL_ACCESS" in DEFAULT_SYSTEM_PROMPT
    assert "只授权执行验证" in DEFAULT_SYSTEM_PROMPT
    assert "不授权修改源码" in DEFAULT_SYSTEM_PROMPT
    assert "等待用户明确确认" in DEFAULT_SYSTEM_PROMPT
    assert "运行相关测试" in DEFAULT_SYSTEM_PROMPT
    assert "commit hash" in DEFAULT_SYSTEM_PROMPT


def test_agent_injects_skill_index_without_full_skill_body(tmp_path):
    skill_dir = tmp_path / ".claude" / "skills" / "bug"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: bug-investigation\ndescription: debug issues\n---\n\nSECRET BODY",
        encoding="utf-8",
    )

    agent = Agent(MockClient([mock_text("hi")]), cfg(), workdir=str(tmp_path))

    system = agent.messages[0].text
    assert "<available_skills>" in system
    assert "bug-investigation" in system
    assert "debug issues" in system
    assert "SECRET BODY" not in system


# --- 项目记忆 (AGENTS.md / CLAUDE.md) -------------------------------------
def test_project_memory_loads_agents_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text("用 pnpm；提交前跑测试", encoding="utf-8")
    mem = load_project_memory(tmp_path)
    assert mem is not None
    assert 'source="AGENTS.md"' in mem
    assert "用 pnpm" in mem
    assert "不是系统规则" in mem               # 安全边界框架文字在


def test_project_memory_falls_back_to_claude_md(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("项目约定 X", encoding="utf-8")
    mem = load_project_memory(tmp_path)
    assert mem is not None and 'source="CLAUDE.md"' in mem and "项目约定 X" in mem


def test_project_memory_prefers_agents_over_claude(tmp_path):
    (tmp_path / "AGENTS.md").write_text("AAA", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("CCC", encoding="utf-8")
    mem = load_project_memory(tmp_path)
    assert 'source="AGENTS.md"' in mem and "AAA" in mem and "CCC" not in mem


def test_project_memory_none_when_absent(tmp_path):
    assert load_project_memory(tmp_path) is None


def test_project_memory_truncates_when_huge(tmp_path):
    (tmp_path / "AGENTS.md").write_text("x" * 50000, encoding="utf-8")
    mem = load_project_memory(tmp_path)
    assert "已截断" in mem and len(mem) < 50000


def test_max_steps_guard_summarizes():
    # 模型每轮都调工具永不收手 → 被 max_steps 截停；触顶时让模型总结现场再停
    script = [mock_tool_call(f"c{i}", "read_file", "{}") for i in range(5)]  # cfg max_steps=5
    script.append(mock_text("进度小结：已查明 X，卡在 Y，下一步 Z"))         # 触顶后的总结
    client = MockClient(script)
    agent = Agent(client, cfg())
    reply = agent.send("loop forever")
    assert "进度小结" in reply                       # 返回模型的现场总结，而非固定句
    # 最后一次请求是「无工具」的总结调用
    assert client.seen_messages[-1][-1].text.startswith("已达到最大工具调用步数")


# --- 会话持久化接缝 -------------------------------------------------------
class _MemoryStore:
    def __init__(self):
        self.saved = []

    def append(self, msg):
        self.saved.append(msg)

    def load(self):
        return list(self.saved)


class _BrokenStore:
    def append(self, msg):
        raise OSError("disk full")

    def load(self):
        return []


def test_agent_persists_only_non_system_messages():
    store = _MemoryStore()
    client = MockClient([mock_text("hello")])
    agent = Agent(client, cfg(), store=store, env_context="<environment>E</environment>")

    assert agent.send("hi") == "hello"

    assert [m.role for m in store.saved] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert "hi" in store.saved[0].text
    assert store.saved[1].text == "hello"


def test_skill_registry_update_is_ephemeral_request_context(tmp_path):
    store = _MemoryStore()
    client = MockClient([mock_text("ok")])
    agent = Agent(client, cfg(), workdir=str(tmp_path), store=store)
    skill_dir = tmp_path / ".codex" / "skills" / "runtime"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: runtime-skill\ndescription: added while session is alive\n---\n\nBody",
        encoding="utf-8",
    )

    assert agent.send("看看当前可用 skills") == "ok"

    request_text = client.seen_messages[0][-1].text
    assert "<skills_update>" in request_text
    assert "project.codex:runtime" in request_text
    assert "runtime-skill" not in agent.messages[0].text
    assert "<skills_update>" not in store.saved[0].text
    assert "看看当前可用 skills" in store.saved[0].text


def test_mcp_registry_update_is_ephemeral_request_context(tmp_path):
    store = _MemoryStore()
    client = MockClient([mock_text("ok")])
    agent = Agent(client, cfg(), workdir=str(tmp_path), store=store)
    mcp_config = tmp_path / ".noval" / "mcp.json"
    mcp_config.parent.mkdir(parents=True)
    mcp_config.write_text(json.dumps({
        "mcpServers": {
            "runtime-mcp": {
                "command": "python",
                "args": ["server.py"],
            }
        }
    }), encoding="utf-8")

    assert agent.send("看看当前可用 MCP") == "ok"

    request_text = client.seen_messages[0][-1].text
    assert "<mcp_update>" in request_text
    assert "project.mcp:runtime-mcp" in request_text
    assert "runtime-mcp" not in agent.messages[0].text
    assert "<mcp_update>" not in store.saved[0].text
    assert "看看当前可用 MCP" in store.saved[0].text


def test_resume_messages_loaded_without_rewriting_store():
    history = [
        user_message("<context>当前时间: old</context>\n\nold question"),
        assistant_message("old answer"),
    ]
    store = _MemoryStore()
    client = MockClient([mock_text("new answer")])
    agent = Agent(client, cfg(), store=store, resume_messages=history)

    assert [m.role for m in agent.messages] == [
        MessageRole.SYSTEM, MessageRole.USER, MessageRole.ASSISTANT,
    ]
    assert store.saved == []

    assert agent.send("new question") == "new answer"
    assert [m.role for m in store.saved] == [MessageRole.USER, MessageRole.ASSISTANT]
    first_request = client.seen_messages[0]
    assert [m.role for m in first_request] == [
        MessageRole.SYSTEM, MessageRole.USER, MessageRole.ASSISTANT, MessageRole.USER,
    ]
    assert "old question" in first_request[1].text
    assert "new question" in first_request[-1].text


def test_resume_self_heals_pending_tool_call_and_persists_placeholder():
    history = [assistant_message(tool_calls=(ToolCallBlock(
        "call-1", "read_file", "{}",
    ),))]
    store = _MemoryStore()

    agent = Agent(MockClient([mock_text("unused")]), cfg(), store=store, resume_messages=history)

    assert agent.messages[-1] == tool_result_message(
        "call-1", "（已中断，未执行）", is_error=True,
    )
    assert store.saved == [agent.messages[-1]]


def test_persistence_failure_does_not_break_send():
    client = MockClient([mock_text("still works")])
    agent = Agent(client, cfg(), store=_BrokenStore())

    assert agent.send("hi") == "still works"


def _meta(session_id, title="t"):
    return SessionMeta(
        session_id=session_id,
        created_at="2026-01-01T00:00:00+00:00",
        last_active="2026-01-01T00:00:00+00:00",
        title=title,
        message_count=2,
        model="m",
    )


def test_choose_resume_session_defaults_to_latest(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert _choose_resume_session([_meta("a"), _meta("b")]) == "a"


def test_choose_resume_session_accepts_number_prefix_and_new(monkeypatch):
    sessions = [_meta("20260623-aaa"), _meta("20260624-bbb")]

    monkeypatch.setattr("builtins.input", lambda prompt: "2")
    assert _choose_resume_session(sessions) == "20260624-bbb"

    monkeypatch.setattr("builtins.input", lambda prompt: "20260623")
    assert _choose_resume_session(sessions) == "20260623-aaa"

    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert _choose_resume_session(sessions) is None


def test_choose_resume_session_skips_or_rejects_incompatible(monkeypatch):
    old = _meta("old")
    old.compatible = False
    current = _meta("current")
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert _choose_resume_session([old, current]) == "current"

    monkeypatch.setattr("builtins.input", lambda prompt: "1")
    with pytest.raises(SystemExit, match="不兼容"):
        _choose_resume_session([old, current])


# --- CLI 轻量排版 ---------------------------------------------------------
def test_turn_prefixes_align_labels():
    assert _turn_prefix("You") == "You   > "
    assert _turn_prefix("Noval") == "Noval > "


def test_format_turn_aligns_multiline_content():
    assert _format_turn("Noval", "第一行\n第二行\n\n第四行") == (
        "Noval > 第一行\n"
        "        第二行\n"
        "        \n"
        "        第四行"
    )


def test_read_turn_writes_colored_prompt_before_input(monkeypatch, capsys):
    seen_before_input = []

    monkeypatch.setattr("noval.agent._supports_color", lambda: True)

    def fake_input():
        seen_before_input.append(capsys.readouterr().out)
        return "hello"

    monkeypatch.setattr("builtins.input", fake_input)

    assert _read_turn("You") == "hello"
    assert seen_before_input == [_turn_prefix("You", use_color=True)]


def test_no_color_disables_ansi(monkeypatch):
    class TTY:
        def isatty(self):
            return True

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    assert _supports_color(TTY()) is True

    monkeypatch.setenv("NO_COLOR", "1")
    assert _supports_color(TTY()) is False


def test_reasoning_status_and_summary_hide_raw_content():
    metrics = TurnMetrics(
        api_calls=2,
        reasoning_tokens=1234,
        has_reasoning_usage=True,
        llm_duration_ms=3800,
        tool_calls=3,
        thinking_detected=True,
    )

    summary = _format_reasoning_summary(metrics)
    assert summary == "思考: 1,234 reasoning tokens · 模型耗时 3.8s · 3 次工具调用"

    status = _handle_reasoning_command("/reasoning", cfg(), metrics)
    assert "思考模式: 由 Provider 决定" in status
    assert "上次请求: 1,234 reasoning tokens" in status
    assert "原始思考过程: 不展示" in status


def test_reasoning_command_is_local_and_exact():
    assert _handle_reasoning_command("/reasoning", cfg(), TurnMetrics()) is not None
    assert _handle_reasoning_command("/reasoning show", cfg(), TurnMetrics()) is None
    assert _handle_reasoning_command("question", cfg(), TurnMetrics()) is None


def test_anthropic_reasoning_status_does_not_use_ignored_deepseek_base_url():
    anthropic = cfg()
    anthropic.provider = "anthropic"
    anthropic.model = "claude-test"

    status = _handle_reasoning_command("/reasoning", anthropic, TurnMetrics())

    assert "由 Provider 决定" in status
    assert "DeepSeek 默认" not in status


def test_permissions_commands_change_session_state():
    permissions = PermissionController()

    assert "请求批准" in _handle_permissions_command("/permissions", permissions)
    full_status = _handle_permissions_command("/permissions full-access", permissions)
    assert "完全访问" in full_status
    assert "工具审批: 全部允许" in full_status
    assert "本会话始终允许: 无" not in full_status
    assert permissions.mode is PermissionMode.FULL_ACCESS

    reply = _handle_permissions_command("/permissions allow run_bash", permissions)
    assert "请求批准模式保留授权: run_bash" in reply
    assert "run_bash" in permissions.approved_tools

    _handle_permissions_command("/permissions ask", permissions)
    assert permissions.mode is PermissionMode.ASK
    assert "run_bash" in permissions.approved_tools      # 切模式不清空显式授权

    _handle_permissions_command("/permissions revoke run_bash", permissions)
    assert "run_bash" not in permissions.approved_tools

    _handle_permissions_command("/permissions allow run_bash", permissions)
    _handle_permissions_command("/permissions reset", permissions)
    assert permissions.mode is PermissionMode.ASK
    assert not permissions.approved_tools


def test_permissions_restore_directly_from_session_sidecar(tmp_path):
    base = tmp_path / "sessions"
    workdir = tmp_path / "project"
    workdir.mkdir()
    store = JsonlSessionStore.create(base, workdir, "m")
    store.append(user_message("hello"))

    first = _create_permission_controller(store)
    first.set_mode(PermissionMode.FULL_ACCESS)
    first.allow_tool("run_bash")

    store.close()
    resumed = JsonlSessionStore.open(base, workdir, store.session_id, "m")
    restored = _create_permission_controller(resumed)
    assert restored.mode is PermissionMode.FULL_ACCESS
    assert restored.approved_tools == {"run_bash"}
