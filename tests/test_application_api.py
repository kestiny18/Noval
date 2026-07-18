import json

import pytest

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
from noval.client import TokenUsage
from noval.messages import assistant_message
from noval.permissions import PermissionMode
from noval.process import NetworkAccess, SandboxMode


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
