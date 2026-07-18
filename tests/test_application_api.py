import json
import os
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from noval.application import ClientSpec, NovalRuntime
from noval.api import (
    ApiFormatError,
    ErrorInfo,
    EventType,
    NovalError,
    PermissionDecision,
    PermissionRequest,
    PermissionStateView,
    RuntimeEvent,
    RuntimeOptions,
    SessionInfo,
    SessionOptions,
    SessionPersistence,
    StopReason,
    TurnMetrics,
    TurnRequest,
    TurnResult,
    TurnStatus,
)
from noval.client import MockClient, TokenUsage, mock_text, mock_tool_call
from noval.config import Config
from noval.messages import assistant_message
from noval.permissions import PermissionMode
from noval.process import NetworkAccess, SandboxMode
from noval.tools import Risk, Tool


def application_config(tmp_path: Path, **overrides) -> Config:
    values = {
        "model": "agent-default",
        "judge_model": "judge-default",
        "base_url": "https://example.invalid",
        "api_key_env": "NOVAL_TEST_API_KEY",
        "api_key": "test-key",
        "max_steps": 4,
        "max_tool_output_chars": 2000,
        "persist_sessions": True,
        "sessions_dir_setting": str(tmp_path / "sessions"),
        "persist_logs": False,
        "logs_dir_setting": str(tmp_path / "logs"),
        "log_retention_days": 1,
        "persist_usage": False,
        "usage_dir_setting": str(tmp_path / "usage"),
        "context_budget_tokens": 256000,
        "request_timeout_seconds": 1.0,
        "request_max_retries": 0,
        "provider": "openai-compatible",
        "anthropic_base_url": "",
        "anthropic_max_tokens": 256,
        "raw": {},
    }
    values.update(overrides)
    return Config(**values)


class RecordingClientFactory:
    def __init__(self, agent_replies):
        self.agent_replies = iter(agent_replies)
        self.specs = []
        self.agent_clients = []

    def __call__(self, spec: ClientSpec):
        self.specs.append(spec)
        if spec.purpose == "agent":
            client = MockClient([mock_text(next(self.agent_replies))])
            self.agent_clients.append(client)
            return client
        return MockClient([])


class BlockingClient:
    def __init__(self, reply="done"):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.reply = reply
        self.seen_messages = []

    def complete(self, messages, tools):
        self.seen_messages.append(list(messages))
        self.entered.set()
        assert self.release.wait(5), "test did not release blocking client"
        return mock_text(self.reply)


class BlockingClientFactory:
    def __init__(self):
        self.clients = []

    def __call__(self, spec):
        if spec.purpose == "agent":
            client = BlockingClient(reply=spec.session_id)
            self.clients.append(client)
            return client
        return MockClient([])


def json_round_trip(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def test_runtime_and_session_options_round_trip_and_reject_unknown_requests():
    runtime = RuntimeOptions(settings_path="C:/config/settings.json")
    session = SessionOptions(
        workdir="C:/projects/a",
        persistence=SessionPersistence.EPHEMERAL,
        provider="anthropic",
        model="claude",
        judge_model="judge",
        sandbox_mode=SandboxMode.REQUIRED,
        network_access=NetworkAccess.DENY,
    )

    assert RuntimeOptions.from_dict(json_round_trip(runtime.to_dict())) == runtime
    assert SessionOptions.from_dict(json_round_trip(session.to_dict())) == session

    with pytest.raises(ApiFormatError, match="unknown field"):
        TurnRequest.from_dict({"text": "hello", "surprise": True})
    with pytest.raises(ApiFormatError, match="text"):
        TurnRequest(text="")


def test_session_info_and_permission_contracts_are_json_safe():
    info = SessionInfo(
        session_id="s1",
        workdir="C:/projects/a",
        persistence=SessionPersistence.PERSISTENT,
        provider="openai-compatible",
        model="deepseek",
        is_open=True,
    )
    state = PermissionStateView(PermissionMode.ASK, ("run_bash",))
    request = PermissionRequest(
        request_id="p1",
        session_id="s1",
        turn_id="t1",
        tool_name="run_bash",
        risk="dangerous",
        arguments={"command": "git status"},
    )

    assert SessionInfo.from_dict(json_round_trip(info.to_dict())) == info
    assert PermissionStateView.from_dict(json_round_trip(state.to_dict())) == state
    assert PermissionRequest.from_dict(json_round_trip(request.to_dict())) == request
    assert PermissionDecision.ALLOW_ONCE.value == "allow_once"


def test_turn_result_round_trip_preserves_canonical_message_usage_and_error():
    result = TurnResult(
        session_id="s1",
        turn_id="t1",
        client_request_id="client-7",
        status=TurnStatus.FAILED,
        message=assistant_message("partial answer"),
        stop_reason=StopReason.ERROR,
        usage=TokenUsage(10, 4, 14, cache_hit_tokens=3, reasoning_tokens=2),
        metrics=TurnMetrics(
            model_calls=2,
            tool_calls=1,
            reasoning_tokens=2,
            model_duration_ms=1200.5,
            duration_ms=1600.0,
        ),
        error=ErrorInfo(
            code="provider_unavailable",
            safe_message="Provider request failed.",
            retryable=True,
            session_id="s1",
            turn_id="t1",
            details={"kind": "timeout"},
        ),
    )

    encoded = json_round_trip(result.to_dict())

    assert TurnResult.from_dict(encoded) == result
    assert "partial answer" in json.dumps(encoded)


def test_response_readers_tolerate_additive_fields():
    raw = TurnResult(
        session_id="s1",
        turn_id="t1",
        status=TurnStatus.COMPLETED,
        message=assistant_message("done"),
        stop_reason=StopReason.COMPLETED,
    ).to_dict()
    raw["future_field"] = {"new": True}

    assert TurnResult.from_dict(raw).message == assistant_message("done")


def test_runtime_event_preserves_unknown_event_types_for_forward_compatibility():
    known = RuntimeEvent(
        event_id="e1",
        session_id="s1",
        turn_id="t1",
        sequence=4,
        timestamp="2026-07-18T01:02:03Z",
        type=EventType.TOOL_COMPLETED.value,
        payload={"tool_name": "read_file", "is_error": False},
    )
    unknown = known.to_dict()
    unknown["type"] = "future.event"
    unknown["future_field"] = 1

    assert RuntimeEvent.from_dict(json_round_trip(known.to_dict())) == known
    assert RuntimeEvent.from_dict(unknown).type == "future.event"


def test_public_errors_round_trip_without_raw_exception_data():
    error = NovalError(
        "session_busy",
        "Session already has an active turn.",
        retryable=True,
        session_id="s1",
        details={"active_turn_id": "t1"},
    )

    encoded = json_round_trip(error.to_dict())
    decoded = NovalError.from_dict(encoded)

    assert decoded.code == "session_busy"
    assert decoded.retryable is True
    assert decoded.session_id == "s1"
    assert "traceback" not in json.dumps(encoded).lower()


def test_runtime_creates_ephemeral_session_without_changing_process_state(tmp_path):
    workdir = tmp_path / "project"
    workdir.mkdir()
    config = application_config(tmp_path)
    factory = RecordingClientFactory(["ephemeral reply"])
    before_cwd = Path.cwd()
    before_env = dict(os.environ)

    with NovalRuntime(config, client_factory=factory) as runtime:
        session = runtime.create_session(SessionOptions(
            workdir=str(workdir),
            persistence=SessionPersistence.EPHEMERAL,
        ))
        result = session.run_turn(TurnRequest("hello", client_request_id="c-1"))

        assert result.status is TurnStatus.COMPLETED
        assert result.message is not None
        assert result.message.text == "ephemeral reply"
        assert result.client_request_id == "c-1"
        assert session.info.persistence is SessionPersistence.EPHEMERAL
        assert runtime.get_session(session.info.session_id) is session
        assert runtime.list_active_sessions() == (session.info,)

    assert not config.sessions_dir().exists()
    assert Path.cwd() == before_cwd
    assert dict(os.environ) == before_env


def test_persistent_session_can_be_listed_closed_and_resumed(tmp_path):
    workdir = tmp_path / "project"
    workdir.mkdir()
    config = application_config(tmp_path)
    first_factory = RecordingClientFactory(["first reply"])

    with NovalRuntime(config, client_factory=first_factory) as runtime:
        first = runtime.create_session(SessionOptions(
            workdir=str(workdir),
            persistence=SessionPersistence.PERSISTENT,
            provider="anthropic",
            model="persisted-agent",
            judge_model="persisted-judge",
        ))
        session_id = first.info.session_id
        first.run_turn(TurnRequest("first question"))
        persisted = runtime.list_persisted_sessions(str(workdir))
        assert [item.session_id for item in persisted] == [session_id]
        assert persisted[0].is_open is True
        assert persisted[0].provider == "anthropic"
        assert persisted[0].model == "persisted-agent"

    second_factory = RecordingClientFactory(["second reply"])
    with NovalRuntime(config, client_factory=second_factory) as runtime:
        resumed = runtime.resume_session(session_id, SessionOptions(
            workdir=str(workdir),
            persistence=SessionPersistence.PERSISTENT,
        ))
        result = resumed.run_turn(TurnRequest("second question"))

        assert result.message is not None
        assert result.message.text == "second reply"
        sent_text = "\n".join(
            message.text
            for message in second_factory.agent_clients[0].seen_messages[0]
        )
        assert "first question" in sent_text
        assert "first reply" in sent_text
        assert [(spec.purpose, spec.provider, spec.model) for spec in second_factory.specs] == [
            ("agent", "anthropic", "persisted-agent"),
            ("completion_judge", "anthropic", "persisted-judge"),
        ]


def test_session_provider_and_model_overrides_are_session_scoped(tmp_path):
    workdir = tmp_path / "project"
    workdir.mkdir()
    factory = RecordingClientFactory(["one", "two"])

    with NovalRuntime(application_config(tmp_path), client_factory=factory) as runtime:
        default_session = runtime.create_session(SessionOptions(
            workdir=str(workdir),
            persistence=SessionPersistence.EPHEMERAL,
        ))
        override_session = runtime.create_session(SessionOptions(
            workdir=str(workdir),
            persistence=SessionPersistence.EPHEMERAL,
            provider="anthropic",
            model="agent-override",
            judge_model="judge-override",
        ))
        default_session.run_turn(TurnRequest("one"))
        override_session.run_turn(TurnRequest("two"))

    assert [(spec.purpose, spec.provider, spec.model) for spec in factory.specs] == [
        ("agent", "openai-compatible", "agent-default"),
        ("completion_judge", "openai-compatible", "judge-default"),
        ("agent", "anthropic", "agent-override"),
        ("completion_judge", "anthropic", "judge-override"),
    ]


def test_same_session_rejects_a_concurrent_turn_without_queueing(tmp_path):
    workdir = tmp_path / "project"
    workdir.mkdir()
    factory = BlockingClientFactory()
    runtime = NovalRuntime(application_config(tmp_path), client_factory=factory)
    session = runtime.create_session(SessionOptions(
        workdir=str(workdir),
        persistence=SessionPersistence.EPHEMERAL,
    ))
    results = []
    worker = threading.Thread(
        target=lambda: results.append(session.run_turn(TurnRequest("first")))
    )
    worker.start()
    assert factory.clients[0].entered.wait(2)

    started = time.perf_counter()
    with pytest.raises(NovalError) as busy:
        session.run_turn(TurnRequest("second"))
    elapsed = time.perf_counter() - started

    assert busy.value.code == "session_busy"
    assert busy.value.retryable is True
    assert elapsed < 0.2
    with pytest.raises(NovalError) as runtime_busy:
        runtime.close()
    assert runtime_busy.value.code == "runtime_busy"

    factory.clients[0].release.set()
    worker.join(2)
    assert results[0].status is TurnStatus.COMPLETED
    runtime.close()


def test_different_sessions_execute_in_parallel_without_message_leakage(tmp_path):
    one_dir = tmp_path / "one"
    two_dir = tmp_path / "two"
    one_dir.mkdir()
    two_dir.mkdir()
    factory = BlockingClientFactory()
    results = {}

    with NovalRuntime(application_config(tmp_path), client_factory=factory) as runtime:
        one = runtime.create_session(SessionOptions(
            workdir=str(one_dir), persistence=SessionPersistence.EPHEMERAL,
        ))
        two = runtime.create_session(SessionOptions(
            workdir=str(two_dir), persistence=SessionPersistence.EPHEMERAL,
        ))
        threads = [
            threading.Thread(
                target=lambda: results.setdefault(
                    "one", one.run_turn(TurnRequest("message-one"))
                )
            ),
            threading.Thread(
                target=lambda: results.setdefault(
                    "two", two.run_turn(TurnRequest("message-two"))
                )
            ),
        ]
        for thread in threads:
            thread.start()
        assert all(client.entered.wait(2) for client in factory.clients)
        for client in factory.clients:
            client.release.set()
        for thread in threads:
            thread.join(2)

    assert set(results) == {"one", "two"}
    observed = [
        "\n".join(message.text for message in client.seen_messages[0])
        for client in factory.clients
    ]
    assert sum("message-one" in text for text in observed) == 1
    assert sum("message-two" in text for text in observed) == 1


def test_permission_handler_is_serializable_ordered_and_fail_closed(tmp_path):
    workdir = tmp_path / "project"
    workdir.mkdir()
    executed = []
    events = []
    permission_requests = []
    dangerous = Tool(
        name="dangerous_test",
        description="test tool",
        parameters={
            "type": "object",
            "properties": {"password": {"type": "string"}},
            "required": ["password"],
        },
        func=lambda password: executed.append(password) or "ok",
        risk=Risk.DANGEROUS,
    )

    class Factory:
        def __call__(self, spec):
            if spec.purpose == "agent":
                return MockClient([
                    mock_tool_call(
                        "call-1",
                        "dangerous_test",
                        json.dumps({"password": "very-secret"}),
                    ),
                    mock_text("done"),
                ])
            return MockClient([mock_text(
                '{"status":"completed","confidence":1,"reason":"visible"}'
            )])

    def allow(request):
        permission_requests.append(request)
        return PermissionDecision.ALLOW_SESSION

    with NovalRuntime(
        application_config(tmp_path),
        client_factory=Factory(),
        tools=[dangerous],
        event_sink=events.append,
    ) as runtime:
        session = runtime.create_session(
            SessionOptions(
                workdir=str(workdir),
                persistence=SessionPersistence.EPHEMERAL,
            ),
            permission_handler=allow,
        )
        result = session.run_turn(TurnRequest("use the tool"))

        assert result.status is TurnStatus.COMPLETED
        assert session.permission_state().approved_tools == ("dangerous_test",)

    assert executed == ["very-secret"]
    assert permission_requests[0].arguments == {"password": "<redacted>"}
    event_types = [event.type for event in events]
    assert EventType.PERMISSION_REQUESTED.value in event_types
    assert EventType.PERMISSION_RESOLVED.value in event_types
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert event_types[-1] == EventType.SESSION_CLOSED.value

    denied = []

    class DeniedFactory(Factory):
        pass

    def broken_handler(request):
        raise RuntimeError("host approval UI failed")

    with NovalRuntime(
        application_config(tmp_path),
        client_factory=DeniedFactory(),
        tools=[replace(dangerous, func=lambda password: denied.append(password))],
    ) as runtime:
        session = runtime.create_session(
            SessionOptions(
                workdir=str(workdir), persistence=SessionPersistence.EPHEMERAL,
            ),
            permission_handler=broken_handler,
        )
        session.run_turn(TurnRequest("use the tool"))
    assert denied == []


def test_cancel_is_cooperative_and_event_sink_failures_do_not_break_turn(tmp_path):
    workdir = tmp_path / "project"
    workdir.mkdir()
    factory = BlockingClientFactory()
    received = []

    def flaky_sink(event):
        received.append(event)
        raise RuntimeError("host sink failed")

    with NovalRuntime(
        application_config(tmp_path),
        client_factory=factory,
        event_sink=flaky_sink,
    ) as runtime:
        session = runtime.create_session(SessionOptions(
            workdir=str(workdir), persistence=SessionPersistence.EPHEMERAL,
        ))
        results = []
        worker = threading.Thread(
            target=lambda: results.append(session.run_turn(TurnRequest("wait")))
        )
        worker.start()
        assert factory.clients[0].entered.wait(2)
        assert session.cancel_active_turn() is True
        factory.clients[0].release.set()
        worker.join(2)

        assert results[0].status is TurnStatus.STOPPED
        assert results[0].stop_reason is StopReason.CANCELLED
        assert session.cancel_active_turn() is False

    terminal = [
        event for event in received
        if event.type in {
            EventType.TURN_COMPLETED.value,
            EventType.TURN_FAILED.value,
        }
    ]
    assert len(terminal) == 1
