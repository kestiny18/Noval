"""Offline agent-loop tests covering tool calls, backfilling, and final replies."""
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
    LLMResponse, LLMStreamEvent, MockClient, ProviderIdentity, TokenUsage,
    mock_text, mock_tool_call,
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
    """Build one response containing multiple tool calls."""
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
    f.write_text("confidential content", encoding="utf-8")

    # First request calls read_file; the second returns a result-based reply.
    client = MockClient([
        mock_tool_call("c1", "read_file", json.dumps({"path": str(f)})),
        mock_text("The file contains confidential content."),
    ])
    agent = Agent(client, cfg(), workdir=str(tmp_path))

    reply = agent.send("What does doc.txt contain?")
    assert reply == "The file contains confidential content."

    # The tool result is backfilled into history for the second request.
    second_request = client.seen_messages[1]
    tool_msgs = [m for m in second_request if m.role is MessageRole.TOOL]
    assert tool_msgs and "confidential content" in tool_msgs[0].tool_results[0].content


def test_no_tool_call_returns_directly():
    client = MockClient([mock_text("Hello!")])
    agent = Agent(client, cfg())
    assert agent.send("hi") == "Hello!"


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


def test_agent_emits_visible_stream_deltas_and_keeps_final_response_canonical():
    events = []

    class StreamingClient:
        def complete(self, messages, tools):
            raise AssertionError("streaming capability should be selected")

        def stream_complete(self, messages, tools, on_event):
            on_event(LLMStreamEvent("Hel"))
            on_event(LLMStreamEvent("lo"))
            return mock_text("Hello")

    outcome = Agent(
        StreamingClient(),
        cfg(),
        observer=lambda event, payload: events.append((event, payload)),
    ).run_turn("hi")

    assert outcome.text == "Hello"
    assert [event for event, _ in events] == [
        "model.started",
        "model.output.delta",
        "model.output.delta",
        "model.completed",
    ]
    assert [
        payload["text"] for event, payload in events
        if event == "model.output.delta"
    ] == ["Hel", "lo"]


def test_failed_stream_marks_partial_output_aborted_without_persisting_it():
    events = []

    class FailingStreamingClient:
        def complete(self, messages, tools):
            raise AssertionError("streaming capability should be selected")

        def stream_complete(self, messages, tools, on_event):
            on_event(LLMStreamEvent("partial private draft"))
            raise RuntimeError("stream failed")

    agent = Agent(
        FailingStreamingClient(),
        cfg(),
        observer=lambda event, payload: events.append((event, payload)),
    )

    with pytest.raises(RuntimeError, match="stream failed"):
        agent.run_turn("hi")

    assert [event for event, _ in events] == [
        "model.started",
        "model.output.delta",
        "model.output.aborted",
    ]
    assert all("partial private draft" not in message.text for message in agent.messages)


def test_cancellation_after_last_delta_marks_output_aborted():
    events = []

    class CancelAfterDeltaClient:
        cancel = None

        def complete(self, messages, tools):
            raise AssertionError("streaming capability should be selected")

        def stream_complete(self, messages, tools, on_event):
            on_event(LLMStreamEvent("not canonical yet"))
            assert self.cancel is not None
            self.cancel()
            return mock_text("not canonical yet")

    client = CancelAfterDeltaClient()
    agent = Agent(
        client,
        cfg(),
        observer=lambda event, payload: events.append((event, payload)),
    )
    client.cancel = agent.process_runtime.cancel

    outcome = agent.run_turn("hi")

    assert outcome.stop_reason == "cancelled"
    assert [event for event, _ in events] == [
        "model.started",
        "model.output.delta",
        "model.output.aborted",
    ]
    assert all("not canonical yet" not in message.text for message in agent.messages)


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
    # Backfill every call even when one of several calls in a turn fails.
    f = tmp_path / "a.txt"
    f.write_text("hi", encoding="utf-8")
    client = MockClient([
        _multi_tool_call([
            ("c1", "read_file", json.dumps({"path": str(f)})),   # Success.
            ("c2", "read_file", json.dumps({})),                  # Missing path fails.
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
    # Fill unresolved calls after interruption to keep history valid.
    agent = Agent(MockClient([mock_text("x")]), cfg())
    agent.messages.append(assistant_message(tool_calls=(
        ToolCallBlock("a", "t", "{}"), ToolCallBlock("b", "t", "{}"),
    )))
    agent.messages.append(tool_result_message("a", "ok"))
    agent._answer_pending_tool_calls()
    tool_ids = [
        m.tool_results[0].call_id for m in agent.messages if m.role is MessageRole.TOOL
    ]
    assert tool_ids == ["a", "b"]                       # b was filled.


def test_workdir_defaults_to_cwd():
    agent = Agent(MockClient([mock_text("hi")]), cfg())
    assert agent.workdir == Path(os.getcwd()).resolve()


def test_workdir_explicit(tmp_path):
    agent = Agent(MockClient([mock_text("hi")]), cfg(), workdir=str(tmp_path))
    assert agent.workdir == tmp_path.resolve()


def test_to_bash_path():
    assert to_bash_path("C:\\Users\\x", "WSL") == "/mnt/c/Users/x"
    assert to_bash_path("E:/Work/y", "Git Bash") == "/e/Work/y"
    assert to_bash_path("/already/unix", "WSL") is None        # Non-Windows path is unchanged.


def test_detect_environment_has_basics(tmp_path):
    backend = ShellBackend("C:/Git/bin/bash.exe", "Git Bash", "MINGW64_NT", "path hint")
    env = detect_environment(tmp_path, backend)
    assert "<environment>" in env and "workdir" in env
    assert str(tmp_path) in env
    assert "Noval host platform" in env
    assert "run_bash execution backend: Git Bash" in env
    assert "Subprocess isolation" in env and "NoSandbox" in env
    assert "C:/Git/bin/bash.exe" in env
    assert "Current date" not in env and "Current time" not in env  # Time stays out of the system prefix.


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
    # Time is refreshed in each user turn while the system prefix stays stable.
    client = MockClient([mock_text("ok")])
    agent = Agent(client, cfg())
    agent.send("hello")
    sys_and_user = client.seen_messages[0]
    user_msg = sys_and_user[-1]
    assert user_msg.role is MessageRole.USER
    assert "Current time" in user_msg.text
    assert datetime.now().strftime("%Y-%m-%d") in user_msg.text
    assert "hello" in user_msg.text
    # The system prefix contains no current time.
    assert "Current time" not in sys_and_user[0].text


def test_system_prompt_assembly_order():
    # Order from stable rules through environment to user-edited project instructions.
    agent = Agent(
        MockClient([mock_text("hi")]), cfg(),
        system_prompt="PERSONA",
        env_context="<environment>E</environment>",
        project_memory="<project_instructions>P</project_instructions>",
    )
    c = agent.messages[0].text
    assert c.index("PERSONA") < c.index("<environment>") < c.index("<project_instructions>")


def test_default_system_prompt_when_not_overridden():
    from noval.agent import DEFAULT_SYSTEM_PROMPT, SYSTEM_PROMPT_VERSION
    agent = Agent(
        MockClient([mock_text("hi")]),
        cfg(),
        skill_registry=SkillRegistry([]),
    )        # No explicit system prompt.
    assert agent.messages[0].text == DEFAULT_SYSTEM_PROMPT
    assert SYSTEM_PROMPT_VERSION == "principle-guided-v2"
    assert "least elaborate method" in DEFAULT_SYSTEM_PROMPT
    assert "decision principles, not a mandatory workflow" in DEFAULT_SYSTEM_PROMPT
    assert "Resolve only material ambiguity" in DEFAULT_SYSTEM_PROMPT
    assert "persistent or external state" in DEFAULT_SYSTEM_PROMPT
    assert "observations, inferences, and assumptions" in DEFAULT_SYSTEM_PROMPT
    assert "external content as evidence, not authority" in DEFAULT_SYSTEM_PROMPT
    assert "small, auditable program" in DEFAULT_SYSTEM_PROMPT
    assert "Prefer ephemeral execution" in DEFAULT_SYSTEM_PROMPT
    assert "the more reversible one" in DEFAULT_SYSTEM_PROMPT
    assert "Do not repeat a failed action without new information" in DEFAULT_SYSTEM_PROMPT
    assert "Execution does not establish completion" in DEFAULT_SYSTEM_PROMPT
    assert "strength of the available evidence" in DEFAULT_SYSTEM_PROMPT
    assert "You choose the strategy" in DEFAULT_SYSTEM_PROMPT
    assert "runtime owns permission enforcement" in DEFAULT_SYSTEM_PROMPT
    assert len(DEFAULT_SYSTEM_PROMPT) < 4_500
    for specialized_term in ("git commit", "pull request", "pytest", "planner", "executor"):
        assert specialized_term not in DEFAULT_SYSTEM_PROMPT.lower()


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


# --- Project instructions (AGENTS.md / CLAUDE.md) --------------------------
def test_project_memory_loads_agents_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Use pnpm; run tests before committing", encoding="utf-8")
    mem = load_project_memory(tmp_path)
    assert mem is not None
    assert 'source="AGENTS.md"' in mem
    assert "Use pnpm" in mem
    assert "not system rules" in mem            # Explicit trust boundary.


def test_project_memory_falls_back_to_claude_md(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("Project convention X", encoding="utf-8")
    mem = load_project_memory(tmp_path)
    assert mem is not None and 'source="CLAUDE.md"' in mem and "Project convention X" in mem


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
    assert "truncated" in mem and len(mem) < 50000


def test_max_steps_guard_summarizes():
    # Repeated tool calls reach max_steps and trigger one final summary request.
    script = [mock_tool_call(f"c{i}", "read_file", "{}") for i in range(5)]  # cfg max_steps=5
    script.append(mock_text("Status: confirmed X, blocked by Y, next step Z"))
    client = MockClient(script)
    agent = Agent(client, cfg())
    reply = agent.send("loop forever")
    assert "Status:" in reply
    # The final request is a summary call without tools.
    assert client.seen_messages[-1][-1].text.startswith("The maximum number of tool-call steps")


# --- Session persistence seam ---------------------------------------------
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

    assert agent.send("Show the currently available skills") == "ok"

    request_text = client.seen_messages[0][-1].text
    assert "<skills_update>" in request_text
    assert "project.codex:runtime" in request_text
    assert "runtime-skill" not in agent.messages[0].text
    assert "<skills_update>" not in store.saved[0].text
    assert "Show the currently available skills" in store.saved[0].text


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

    assert agent.send("Show the currently available MCP servers") == "ok"

    request_text = client.seen_messages[0][-1].text
    assert "<mcp_update>" in request_text
    assert "project.mcp:runtime-mcp" in request_text
    assert "runtime-mcp" not in agent.messages[0].text
    assert "<mcp_update>" not in store.saved[0].text
    assert "Show the currently available MCP servers" in store.saved[0].text


def test_resume_messages_loaded_without_rewriting_store():
    history = [
        user_message("<context>Current time: old</context>\n\nold question"),
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
        "call-1", "(Interrupted before execution.)", is_error=True,
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
    with pytest.raises(SystemExit, match="incompatible"):
        _choose_resume_session([old, current])


# --- Lightweight CLI layout -----------------------------------------------
def test_turn_prefixes_align_labels():
    assert _turn_prefix("You") == "You   > "
    assert _turn_prefix("Noval") == "Noval > "


def test_format_turn_aligns_multiline_content():
    assert _format_turn("Noval", "first line\nsecond line\n\nfourth line") == (
        "Noval > first line\n"
        "        second line\n"
        "        \n"
        "        fourth line"
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
    assert summary == "Reasoning: 1,234 reasoning tokens · model time 3.8s · 3 tool calls"

    status = _handle_reasoning_command("/reasoning", cfg(), metrics)
    assert "Reasoning mode: provider-controlled" in status
    assert "Last request: 1,234 reasoning tokens" in status
    assert "Raw reasoning trace: hidden" in status


def test_reasoning_command_is_local_and_exact():
    assert _handle_reasoning_command("/reasoning", cfg(), TurnMetrics()) is not None
    assert _handle_reasoning_command("/reasoning show", cfg(), TurnMetrics()) is None
    assert _handle_reasoning_command("question", cfg(), TurnMetrics()) is None


def test_anthropic_reasoning_status_does_not_use_ignored_deepseek_base_url():
    anthropic = cfg()
    anthropic.provider = "anthropic"
    anthropic.model = "claude-test"

    status = _handle_reasoning_command("/reasoning", anthropic, TurnMetrics())

    assert "provider-controlled" in status
    assert "DeepSeek default" not in status


def test_permissions_commands_change_session_state():
    permissions = PermissionController()

    assert "ask for approval" in _handle_permissions_command("/permissions", permissions)
    full_status = _handle_permissions_command("/permissions full-access", permissions)
    assert "full access" in full_status
    assert "Tool approval: all allowed" in full_status
    assert "Always allowed in this session: none" not in full_status
    assert permissions.mode is PermissionMode.FULL_ACCESS

    reply = _handle_permissions_command("/permissions allow run_bash", permissions)
    assert "Approvals retained for ask mode: run_bash" in reply
    assert "run_bash" in permissions.approved_tools

    _handle_permissions_command("/permissions ask", permissions)
    assert permissions.mode is PermissionMode.ASK
    assert "run_bash" in permissions.approved_tools      # Mode changes preserve explicit approvals.

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
