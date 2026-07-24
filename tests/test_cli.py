import ast
import json
from pathlib import Path

from noval.api import (
    EventType,
    PermissionDecision,
    PermissionRequest,
    PermissionStateView,
    RuntimeEvent,
    SessionInfo,
    SessionPersistence,
    StopReason,
    TurnMetrics,
    TurnResult,
    TurnStatus,
)
from noval.cli import _CliStreamRenderer, _cli_permission_handler, run_cli
from noval.config import Config
from noval.messages import assistant_message
from noval.model_config import packaged_settings
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
            selected_model_id="configured-agent",
            selected_judge_model_id="configured-judge",
            is_open=True,
            message_count=message_count,
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
    monkeypatch.setattr(
        "noval.cli.NovalRuntime", lambda config, **kwargs: runtime
    )
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


def test_cli_session_model_flags_use_configured_ids(monkeypatch, tmp_path):
    session = FakeSession(tmp_path)
    runtime = FakeRuntime(session)
    monkeypatch.setattr("noval.cli.Config.load", lambda: cli_config(tmp_path))
    monkeypatch.setattr(
        "noval.cli.NovalRuntime", lambda config, **kwargs: runtime
    )
    monkeypatch.setattr("noval.cli._read_turn", lambda label: "exit")

    run_cli([
        "--workdir",
        str(tmp_path),
        "--model-id",
        "agent-selection",
        "--judge-model-id",
        "judge-selection",
    ])

    options = runtime.created[0][0]
    assert options.selected_model_id == "agent-selection"
    assert options.selected_judge_model_id == "judge-selection"


def test_cli_credential_command_hides_secret_and_updates_settings(
    monkeypatch, tmp_path, capsys
):
    secret = "FAKE_CLI_SECRET_MUST_STAY_HIDDEN"
    settings_path = tmp_path / "settings.json"
    document = packaged_settings()
    document.update({
        "sessions_dir": str(tmp_path / "sessions"),
        "logs_dir": str(tmp_path / "logs"),
        "usage_dir": str(tmp_path / "usage"),
    })
    settings_path.write_text(json.dumps(document), encoding="utf-8")
    config = Config.load(settings_path)
    connection_id = config.model_configuration.connections[0].id
    monkeypatch.setattr("noval.cli.Config.load", lambda: config)
    monkeypatch.setattr("noval.cli.getpass.getpass", lambda prompt: secret)

    run_cli(["models", "credential", connection_id])

    output = capsys.readouterr().out
    assert secret not in output
    assert "Credential updated" in output
    assert secret in settings_path.read_text(encoding="utf-8")


def test_cli_models_list_and_validate_are_credential_free(
    monkeypatch, tmp_path, capsys
):
    secret = "FAKE_LIST_SECRET_MUST_STAY_HIDDEN"
    settings_path = tmp_path / "settings.json"
    document = packaged_settings()
    document["models"]["connections"][0]["api_key"] = secret
    settings_path.write_text(json.dumps(document), encoding="utf-8")
    config = Config.load(settings_path)
    monkeypatch.setattr("noval.cli.Config.load", lambda: config)

    run_cli(["models", "list"])
    run_cli(["models", "validate"])

    output = capsys.readouterr().out
    assert "Provider Profiles" in output
    assert "Model configuration is valid" in output
    assert secret not in output


def test_cli_resume_and_permission_slash_command_use_public_session(monkeypatch, tmp_path):
    session = FakeSession(tmp_path, session_id="saved-1", message_count=7)
    runtime = FakeRuntime(session)
    inputs = iter(["/permissions full-access", "exit"])
    monkeypatch.setattr("noval.cli.Config.load", lambda: cli_config(tmp_path))
    monkeypatch.setattr(
        "noval.cli.NovalRuntime", lambda config, **kwargs: runtime
    )
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


def test_cli_stream_renderer_prints_deltas_once_and_handles_abort(capsys):
    renderer = _CliStreamRenderer()

    renderer.handle(RuntimeEvent(
        event_id="event-1",
        session_id="session-1",
        turn_id="turn-1",
        sequence=1,
        timestamp="2026-07-21T01:02:03Z",
        type=EventType.MODEL_OUTPUT_DELTA.value,
        payload={"request_id": "request-1", "text": "Hel"},
    ))
    renderer.handle(RuntimeEvent(
        event_id="event-2",
        session_id="session-1",
        turn_id="turn-1",
        sequence=2,
        timestamp="2026-07-21T01:02:04Z",
        type=EventType.MODEL_OUTPUT_DELTA.value,
        payload={"request_id": "request-1", "text": "lo"},
    ))
    renderer.handle(RuntimeEvent(
        event_id="event-3",
        session_id="session-1",
        turn_id="turn-1",
        sequence=3,
        timestamp="2026-07-21T01:02:05Z",
        type=EventType.MODEL_COMPLETED.value,
    ))

    assert capsys.readouterr().out == "Noval > Hello\n"
    assert renderer.displayed("turn-1", "Hello") is True
    assert renderer.displayed("turn-1", "different") is False

    renderer.handle(RuntimeEvent(
        event_id="event-4",
        session_id="session-1",
        turn_id="turn-2",
        sequence=4,
        timestamp="2026-07-21T01:02:06Z",
        type=EventType.MODEL_OUTPUT_DELTA.value,
        payload={"request_id": "request-2", "text": "partial"},
    ))
    renderer.handle(RuntimeEvent(
        event_id="event-5",
        session_id="session-1",
        turn_id="turn-2",
        sequence=5,
        timestamp="2026-07-21T01:02:07Z",
        type=EventType.MODEL_OUTPUT_ABORTED.value,
    ))

    assert capsys.readouterr().out == "Noval > partial\n"
    assert renderer.displayed("turn-2", "partial") is False


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
