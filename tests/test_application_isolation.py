import json
import os
import threading
from pathlib import Path

from noval.api import (
    NovalError,
    PermissionRequest,
    PermissionStateView,
    RequestInspection,
    RuntimeEvent,
    RuntimeOptions,
    SessionInfo,
    SessionOptions,
    SessionPersistence,
    StopReason,
    TurnRequest,
    TurnResult,
    TurnStatus,
)
from noval.application import NovalRuntime
from noval.client import (
    MockClient,
    ProviderError,
    ProviderErrorKind,
    ProviderIdentity,
    TokenUsage,
    mock_text,
    mock_tool_call,
)
from noval.config import Config
from noval.permissions import PermissionMode


def isolation_config(tmp_path):
    return Config(
        model="agent",
        judge_model="judge",
        base_url="https://example.invalid",
        api_key_env="NOVAL_TEST_KEY",
        api_key="test-key",
        max_steps=5,
        max_tool_output_chars=2000,
        persist_sessions=False,
        sessions_dir_setting=str(tmp_path / "sessions"),
        persist_logs=False,
        persist_usage=True,
        usage_dir_setting=str(tmp_path / "usage"),
        context_budget_tokens=256000,
        provider="openai-compatible",
    )


def write_project_runtime_config(workdir, label):
    skill = workdir / ".noval" / "skills" / f"skill-{label}"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        f"name: skill-{label}\n"
        f"description: skill-{label}-description\n"
        "---\n\nBody\n",
        encoding="utf-8",
    )
    config_dir = workdir / ".noval"
    (config_dir / "mcp.json").write_text(json.dumps({
        "mcpServers": {
            f"mcp-{label}": {"command": "python", "args": ["--version"]}
        }
    }), encoding="utf-8")
    (config_dir / "hooks.json").write_text(json.dumps({
        "version": 1,
        "hooks": {
            "PreToolUse": [{
                "id": f"hook-{label}",
                "match": {"tools": ["run_bash"]},
                "command": "python",
                "args": ["--version"],
            }]
        },
    }), encoding="utf-8")


class IsolationClient:
    def __init__(self, label, barrier):
        self.label = label
        self.barrier = barrier
        self.calls = 0
        self.seen_messages = []

    def complete(self, messages, tools):
        self.seen_messages.append(list(messages))
        self.calls += 1
        usage = TokenUsage(10, 2, 12)
        if self.calls == 1:
            self.barrier.wait(timeout=5)
            return mock_tool_call(
                f"call-{self.label}",
                "write_file",
                json.dumps({
                    "path": "marker.txt",
                    "content": f"content-{self.label}",
                }),
                usage=usage,
            )
        return mock_text(f"done-{self.label}", usage=usage)


class IsolationFactory:
    def __init__(self):
        self.barrier = threading.Barrier(2)
        self.clients = {}
        self.session_labels = {}

    def bind_session(self, session_id, label):
        self.session_labels[session_id] = label

    def __call__(self, spec):
        if spec.purpose == "agent":
            label = self.session_labels[spec.session_id]
            client = IsolationClient(label, self.barrier)
            self.clients[label] = client
            return client
        return MockClient([mock_text(
            '{"status":"completed","confidence":1,"reason":"visible"}',
            usage=TokenUsage(3, 1, 4),
        )])


def test_parallel_sessions_isolate_runtime_state_and_project_discovery(tmp_path):
    one_dir = tmp_path / "one"
    two_dir = tmp_path / "two"
    one_dir.mkdir()
    two_dir.mkdir()
    write_project_runtime_config(one_dir, "one")
    write_project_runtime_config(two_dir, "two")
    factory = IsolationFactory()
    one_events = []
    two_events = []
    before_cwd = Path.cwd()
    before_env = dict(os.environ)
    results = {}

    with NovalRuntime(
        isolation_config(tmp_path), client_factory=factory
    ) as runtime:
        one = runtime.create_session(
            SessionOptions(
                workdir=str(one_dir),
                persistence=SessionPersistence.EPHEMERAL,
            ),
            event_sink=one_events.append,
        )
        two = runtime.create_session(
            SessionOptions(
                workdir=str(two_dir),
                persistence=SessionPersistence.EPHEMERAL,
            ),
            event_sink=two_events.append,
        )
        factory.bind_session(one.info.session_id, "one")
        factory.bind_session(two.info.session_id, "two")
        one.set_permission_mode(PermissionMode.FULL_ACCESS)
        threads = [
            threading.Thread(target=lambda: results.setdefault(
                "one", one.run_turn(TurnRequest("request-one"))
            )),
            threading.Thread(target=lambda: results.setdefault(
                "two", two.run_turn(TurnRequest("request-two"))
            )),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(8)

        one_id = one.info.session_id
        two_id = two.info.session_id
        assert one.permission_state().mode is PermissionMode.FULL_ACCESS
        assert two.permission_state().mode is PermissionMode.ASK

    assert results["one"].message.text == "done-one"
    assert results["two"].message.text == "done-two"
    assert (one_dir / "marker.txt").read_text() == "content-one"
    assert (two_dir / "marker.txt").read_text() == "content-two"
    one_messages = "\n".join(
        message.text for request in factory.clients["one"].seen_messages
        for message in request
    )
    two_messages = "\n".join(
        message.text for request in factory.clients["two"].seen_messages
        for message in request
    )
    for unique in ("skill-one-description", "mcp-one", "hook-one", "request-one"):
        assert unique in one_messages
        assert unique not in two_messages
    for unique in ("skill-two-description", "mcp-two", "hook-two", "request-two"):
        assert unique in two_messages
        assert unique not in one_messages
    assert {event.session_id for event in one_events} == {one_id}
    assert {event.session_id for event in two_events} == {two_id}
    usage_files = list((tmp_path / "usage").rglob("*.jsonl"))
    assert any(one_id in path.name for path in usage_files)
    assert any(two_id in path.name for path in usage_files)
    assert Path.cwd() == before_cwd
    assert dict(os.environ) == before_env


def test_provider_failure_is_terminal_only_for_its_own_session(tmp_path):
    one_dir = tmp_path / "one"
    two_dir = tmp_path / "two"
    one_dir.mkdir()
    two_dir.mkdir()
    identity = ProviderIdentity("mock", "model", "mock")

    class FailingClient:
        def complete(self, messages, tools):
            raise ProviderError(
                ProviderErrorKind.TIMEOUT,
                "provider timed out",
                retryable=True,
                identity=identity,
            )

    clients = iter((FailingClient(), MockClient([mock_text("healthy")])) )

    def factory(spec):
        return next(clients) if spec.purpose == "agent" else MockClient([])

    with NovalRuntime(
        isolation_config(tmp_path), client_factory=factory
    ) as runtime:
        failed = runtime.create_session(SessionOptions(
            workdir=str(one_dir), persistence=SessionPersistence.EPHEMERAL,
        ))
        healthy = runtime.create_session(SessionOptions(
            workdir=str(two_dir), persistence=SessionPersistence.EPHEMERAL,
        ))
        failed_result = failed.run_turn(TurnRequest("fail"))
        healthy_result = healthy.run_turn(TurnRequest("continue"))

    assert failed_result.status is TurnStatus.FAILED
    assert failed_result.error.code == "provider_timeout"
    assert failed_result.error.retryable is True
    assert healthy_result.status is TurnStatus.COMPLETED
    assert healthy_result.message.text == "healthy"


def test_application_api_v2_golden_fixture_round_trips():
    fixture = Path(__file__).parent / "fixtures" / "application_api_v2.json"
    documents = json.loads(fixture.read_text(encoding="utf-8"))
    readers = {
        "runtime_options": RuntimeOptions,
        "session_options": SessionOptions,
        "session_info": SessionInfo,
        "turn_request": TurnRequest,
        "turn_result": TurnResult,
        "runtime_event": RuntimeEvent,
        "permission_state": PermissionStateView,
        "permission_request": PermissionRequest,
        "request_inspection": RequestInspection,
        "error": NovalError,
    }

    for name, reader in readers.items():
        assert reader.from_dict(documents[name]).to_dict() == documents[name]
