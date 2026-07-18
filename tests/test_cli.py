import ast
from pathlib import Path

from noval.api import (
    PermissionDecision,
    PermissionRequest,
    PermissionStateView,
    SessionInfo,
    SessionPersistence,
    StopReason,
    TurnMetrics,
    TurnResult,
    TurnStatus,
)
from noval.cli import _cli_permission_handler, run_cli
from noval.config import Config
from noval.messages import assistant_message
from noval.permissions import PermissionMode


def cli_config(tmp_path):
    return Config(
        model="agent",
        judge_model="judge",
        base_url="https://example.invalid",
        api_key_env="TEST_KEY",
        api_key="test-key",
        max_steps=4,
        max_tool_output_chars=1000,
        persist_sessions=True,
        sessions_dir_setting=str(tmp_path / "sessions"),
        persist_logs=False,
        persist_usage=False,
        context_budget_tokens=10000,
        provider="openai-compatible",
    )


class FakeSession:
    def __init__(self, workdir, *, session_id="session-1", message_count=0):
        self.info = SessionInfo(
            session_id=session_id,
            workdir=str(workdir),
            persistence=SessionPersistence.PERSISTENT,
            provider="openai-compatible",
            model="agent",
            is_open=True,
            message_count=message_count,
            schema_version=2,
        )
        self.available_tools = ("read_file", "run_bash")
        self.requests = []
        self.closed = False
        self.mode = PermissionMode.ASK
        self.approved = set()

    def run_turn(self, request):
        self.requests.append(request)
        return TurnResult(
            session_id=self.info.session_id,
            turn_id="turn-1",
            status=TurnStatus.COMPLETED,
            stop_reason=StopReason.COMPLETED,
            message=assistant_message("headless reply"),
            metrics=TurnMetrics(model_calls=1),
        )

    def permission_state(self):
        return PermissionStateView(self.mode, tuple(sorted(self.approved)))

    def set_permission_mode(self, mode):
        self.mode = mode

    def allow_tool(self, name):
        self.approved.add(name)

    def revoke_tool(self, name):
        self.approved.discard(name)

    def reset_permissions(self):
        self.mode = PermissionMode.ASK
        self.approved.clear()

    def close(self):
        self.closed = True


class FakeRuntime:
    def __init__(self, session):
        self.session = session
        self.created = []
        self.resumed = []
        self.closed = False

    def create_session(self, options, **kwargs):
        self.created.append((options, kwargs))
        return self.session

    def resume_session(self, session_id, options, **kwargs):
        self.resumed.append((session_id, options, kwargs))
        return self.session

    def list_persisted_sessions(self, workdir):
        return (self.session.info,)

    def close(self):
        self.closed = True


def test_cli_runs_turn_through_application_api_without_chdir(monkeypatch, tmp_path, capsys):
    session = FakeSession(tmp_path)
    runtime = FakeRuntime(session)
    inputs = iter(["hello", "exit"])
    monkeypatch.setattr("noval.cli.Config.load", lambda: cli_config(tmp_path))
    monkeypatch.setattr("noval.cli.NovalRuntime", lambda config: runtime)
    monkeypatch.setattr("noval.cli.setup_runtime_logging", lambda *args: None)
    monkeypatch.setattr("noval.cli._read_turn", lambda label: next(inputs))
    monkeypatch.setattr(
        "noval.cli.os.chdir",
        lambda path: (_ for _ in ()).throw(AssertionError("CLI changed cwd")),
    )

    run_cli(["--workdir", str(tmp_path)])

    assert len(runtime.created) == 1
    assert session.requests[0].text == "hello"
    assert session.closed is True
    assert runtime.closed is True
    assert "headless reply" in capsys.readouterr().out


def test_cli_resume_and_permission_slash_command_use_public_session(monkeypatch, tmp_path):
    session = FakeSession(tmp_path, session_id="saved-1", message_count=7)
    runtime = FakeRuntime(session)
    inputs = iter(["/permissions full-access", "exit"])
    monkeypatch.setattr("noval.cli.Config.load", lambda: cli_config(tmp_path))
    monkeypatch.setattr("noval.cli.NovalRuntime", lambda config: runtime)
    monkeypatch.setattr("noval.cli.setup_runtime_logging", lambda *args: None)
    monkeypatch.setattr("noval.cli._read_turn", lambda label: next(inputs))

    run_cli(["--workdir", str(tmp_path), "--resume", "saved-1"])

    assert runtime.resumed[0][0] == "saved-1"
    assert session.mode is PermissionMode.FULL_ACCESS
    assert session.requests == []


def test_cli_permission_handler_returns_three_state_decision(monkeypatch):
    request = PermissionRequest(
        request_id="permission-1",
        session_id="session-1",
        turn_id="turn-1",
        tool_name="run_bash",
        risk="dangerous",
        arguments={"command": "git status"},
    )
    monkeypatch.setattr("builtins.input", lambda prompt: "a")
    assert _cli_permission_handler(request) is PermissionDecision.ALLOW_SESSION

    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert _cli_permission_handler(request) is PermissionDecision.ALLOW_ONCE

    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert _cli_permission_handler(request) is PermissionDecision.DENY


def test_cli_host_does_not_construct_agent_directly():
    source = (Path(__file__).parents[1] / "noval" / "cli.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    calls = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "Agent" not in calls
