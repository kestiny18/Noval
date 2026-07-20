import json
import os
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from noval.application import ClientSpec, NovalRuntime
from noval.api import (
    AcceptanceCriterion,
    ActionReceipt,
    ApiFormatError,
    CompletionReport,
    CompletionStatus,
    CriterionReport,
    CriterionStatus,
    ErrorInfo,
    EvidenceOutcome,
    EventType,
    GoalContract,
    NovalError,
    PermissionDecision,
    PermissionRequest,
    PermissionStateView,
    RequestInspection,
    RuntimeEvent,
    RuntimeOptions,
    ReceiptKind,
    ReceiptOutcome,
    SemanticAssessment,
    SessionInfo,
    SessionOptions,
    SessionPersistence,
    StopReason,
    TurnMetrics,
    TurnRequest,
    TurnResult,
    TurnStatus,
    VerificationResult,
)
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


class QueueClientFactory:
    def __init__(self, agent_responses, judge_responses=()):
        self.agent = MockClient(list(agent_responses))
        self.judge = MockClient(list(judge_responses))

    def __call__(self, spec):
        return self.agent if spec.purpose == "agent" else self.judge


def json_round_trip(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def application_goal(*, objective="Deliver the verified result."):
    return GoalContract(
        goal_id="application-goal",
        objective=objective,
        scope=("current workspace",),
        authority=("use only the authority granted by the host",),
        acceptance_criteria=(
            AcceptanceCriterion(
                criterion_id="verified",
                description="The result is independently verified.",
                verification_source="host:validator",
            ),
        ),
    )


def block_process_turn_start(session, monkeypatch):
    entered = threading.Event()
    proceed = threading.Event()
    original = session._process_runtime.begin_turn

    def blocked_begin_turn():
        entered.set()
        assert proceed.wait(5), "test did not release turn admission"
        original()

    monkeypatch.setattr(
        session._process_runtime, "begin_turn", blocked_begin_turn
    )
    return entered, proceed


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
        title="Investigate runtime",
        message_count=12,
        last_active="2026-07-18T11:00:00Z",
        compatible=True,
        schema_version=2,
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


def test_goal_evidence_and_completion_contracts_round_trip():
    goal = GoalContract(
        goal_id="release-0.12.0",
        objective="Publish v0.12.0 only after all required checks pass.",
        scope=("goal evidence contract", "release metadata"),
        authority=("may modify this repository", "must use a pull request"),
        acceptance_criteria=(
            AcceptanceCriterion(
                criterion_id="tests",
                description="The full test suite passes.",
                verification_source="hook:test-suite",
                max_age_seconds=3600,
            ),
        ),
    )
    receipt = ActionReceipt(
        receipt_id="receipt-1",
        call_id="call-1",
        tool_name="run_bash",
        target="tool:run_bash",
        kind=ReceiptKind.ACTION,
        risk="dangerous",
        outcome=ReceiptOutcome.SUCCEEDED,
        executed=True,
        started_at="2026-07-20T12:00:00+00:00",
        completed_at="2026-07-20T12:00:01+00:00",
        argument_keys=("command",),
        duration_ms=1000.0,
        truncated=False,
        redacted=True,
        result_digest="sha256:abc123",
    )
    verification = VerificationResult(
        verification_id="verification-1",
        goal_id=goal.goal_id,
        criterion_id="tests",
        source="hook:test-suite",
        outcome=EvidenceOutcome.PASSED,
        observed_at="2026-07-20T12:00:01+00:00",
        subject="repository test suite",
        summary="All tests passed.",
        receipt_ids=(receipt.receipt_id,),
    )
    semantic = SemanticAssessment(
        status=CompletionStatus.COMPLETED,
        confidence=0.91,
        reason="The visible reply reports the required checks.",
        missing=(),
        source="semantic_judge:test-model",
    )
    report = CompletionReport(
        goal_id=goal.goal_id,
        status=CompletionStatus.COMPLETED,
        evaluated_at="2026-07-20T12:00:02+00:00",
        criteria=(
            CriterionReport(
                criterion_id="tests",
                status=CriterionStatus.PASSED,
                verification_id=verification.verification_id,
                source=verification.source,
                observed_at=verification.observed_at,
                age_seconds=1.0,
                receipt_ids=verification.receipt_ids,
            ),
        ),
        semantic=semantic,
    )

    assert GoalContract.from_dict(json_round_trip(goal.to_dict())) == goal
    assert ActionReceipt.from_dict(json_round_trip(receipt.to_dict())) == receipt
    assert VerificationResult.from_dict(
        json_round_trip(verification.to_dict())
    ) == verification
    assert CompletionReport.from_dict(json_round_trip(report.to_dict())) == report


def test_semantic_assessment_allows_an_empty_reason_but_rejects_nonfinite_confidence():
    assessment = SemanticAssessment(
        status=CompletionStatus.UNCERTAIN,
        confidence=0.0,
        reason="",
        missing=(),
        source="judge:test-model",
    )

    assert SemanticAssessment.from_dict(
        json_round_trip(assessment.to_dict())
    ) == assessment
    with pytest.raises(ApiFormatError, match="finite"):
        SemanticAssessment(
            status=CompletionStatus.UNCERTAIN,
            confidence=float("nan"),
            reason="",
            missing=(),
            source="judge:test-model",
        )


def test_goal_contract_rejects_unsafe_shapes_and_requires_criteria():
    with pytest.raises(ApiFormatError, match="acceptance_criteria"):
        GoalContract(goal_id="g1", objective="Do the work.")
    with pytest.raises(ApiFormatError, match="unknown field"):
        GoalContract.from_dict({
            "goal_id": "g1",
            "objective": "Do the work.",
            "acceptance_criteria": [
                {"criterion_id": "done", "description": "It is done."}
            ],
            "surprise": True,
        })
    with pytest.raises(ApiFormatError, match="unique"):
        GoalContract(
            goal_id="g1",
            objective="Do the work.",
            acceptance_criteria=(
                AcceptanceCriterion("done", "First definition."),
                AcceptanceCriterion("done", "Second definition."),
            ),
        )
    with pytest.raises(ApiFormatError, match="identifier"):
        VerificationResult(
            verification_id="contains spaces",
            goal_id="g1",
            criterion_id="done",
            source="host:test",
            outcome=EvidenceOutcome.PASSED,
            observed_at="2026-07-20T12:00:00+00:00",
        )


def test_turn_contract_adds_optional_goal_receipts_and_completion_compatibly():
    goal = GoalContract(
        goal_id="g1",
        objective="Verify the result.",
        acceptance_criteria=(
            AcceptanceCriterion("done", "The result is verified."),
        ),
    )
    request = TurnRequest("do it", client_request_id="c1", goal=goal)
    encoded_request = json_round_trip(request.to_dict())

    assert TurnRequest.from_dict(encoded_request) == request
    assert TurnRequest.from_dict({"text": "legacy"}).goal is None

    receipt = ActionReceipt(
        receipt_id="r1",
        call_id="call1",
        tool_name="read_file",
        target="tool:read_file",
        kind=ReceiptKind.OBSERVATION,
        risk="read",
        outcome=ReceiptOutcome.SUCCEEDED,
        executed=True,
        started_at="2026-07-20T12:00:00+00:00",
        completed_at="2026-07-20T12:00:00+00:00",
    )
    completion = CompletionReport(
        goal_id="g1",
        status=CompletionStatus.UNCERTAIN,
        evaluated_at="2026-07-20T12:00:01+00:00",
        criteria=(CriterionReport("done", CriterionStatus.MISSING),),
    )
    result = TurnResult(
        session_id="s1",
        turn_id="t1",
        status=TurnStatus.UNCERTAIN,
        stop_reason=StopReason.COMPLETED,
        receipts=(receipt,),
        completion=completion,
    )

    assert TurnResult.from_dict(json_round_trip(result.to_dict())) == result
    legacy = TurnResult.from_dict({
        "session_id": "s1",
        "turn_id": "t1",
        "status": "completed",
        "stop_reason": "completed",
    })
    assert legacy.receipts == ()
    assert legacy.completion is None


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


def test_request_inspection_contract_round_trips_json_safely():
    inspection = RequestInspection(
        request_id="request-1",
        session_id="session-1",
        turn_id="turn-1",
        purpose="agent",
        step=2,
        timestamp="2026-07-18T01:02:03Z",
        provider={"provider": "mock", "model": "m", "adapter": "mock"},
        canonical_messages=({"role": "user", "blocks": []},),
        tools=({"name": "read_file", "input_schema": {}},),
        adapter_request={"model": "m", "messages": []},
    )

    assert RequestInspection.from_dict(
        json_round_trip(inspection.to_dict())
    ) == inspection


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


def test_explicit_goal_exposes_uncertain_status_then_accepts_host_verification(
    tmp_path,
):
    workdir = tmp_path / "project"
    workdir.mkdir()
    events = []
    factory = QueueClientFactory([mock_text("The requested work is done.")])

    with NovalRuntime(
        application_config(tmp_path),
        client_factory=factory,
        event_sink=events.append,
    ) as runtime:
        session = runtime.create_session(SessionOptions(
            workdir=str(workdir),
            persistence=SessionPersistence.EPHEMERAL,
        ))
        result = session.run_turn(TurnRequest("do the work", goal=application_goal()))

        assert result.status is TurnStatus.UNCERTAIN
        assert result.stop_reason is StopReason.COMPLETED
        assert result.completion is not None
        assert result.completion.status is CompletionStatus.UNCERTAIN
        assert result.completion.criteria[0].status is CriterionStatus.MISSING
        observed_request = "\n".join(
            message.text for message in factory.agent.seen_messages[0]
        )
        assert '<goal_contract source="host">' in observed_request
        assert "does not grant permission" in observed_request

        observed_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        report = session.record_verification(VerificationResult(
            verification_id="host-verification-1",
            goal_id="application-goal",
            criterion_id="verified",
            source="host:validator",
            outcome=EvidenceOutcome.PASSED,
            observed_at=observed_at,
            summary="token=must-not-enter-event-output",
        ))

        assert report.status is CompletionStatus.COMPLETED
        refreshed = session.completion_report()
        assert refreshed is not None
        assert refreshed.status is report.status
        assert replace(
            refreshed.criteria[0],
            age_seconds=report.criteria[0].age_seconds,
        ) == report.criteria[0]
        assert (
            refreshed.criteria[0].age_seconds
            >= report.criteria[0].age_seconds
        )

    verification_event = next(
        event for event in events
        if event.type == EventType.VERIFICATION_RECORDED.value
    )
    encoded_event = json.dumps(verification_event.to_dict(), ensure_ascii=False)
    assert "must-not-enter-event-output" not in encoded_event
    turn_event = next(
        event for event in events if event.type == EventType.TURN_COMPLETED.value
    )
    assert turn_event.payload["status"] == "uncertain"
    assert turn_event.payload["completion"]["status"] == "uncertain"


def test_goal_contract_redefinition_returns_safe_failed_turn(tmp_path):
    workdir = tmp_path / "project"
    workdir.mkdir()
    factory = QueueClientFactory([mock_text("first reply")])

    with NovalRuntime(
        application_config(tmp_path), client_factory=factory
    ) as runtime:
        session = runtime.create_session(SessionOptions(
            workdir=str(workdir),
            persistence=SessionPersistence.EPHEMERAL,
        ))
        first = session.run_turn(TurnRequest("first", goal=application_goal()))
        changed = application_goal(objective="Silently changed objective.")
        second = session.run_turn(TurnRequest("second", goal=changed))

    assert first.status is TurnStatus.UNCERTAIN
    assert second.status is TurnStatus.FAILED
    assert second.stop_reason is StopReason.ERROR
    assert second.error is not None
    assert second.error.code == "goal_contract_error"
    assert "redefine" in second.error.safe_message
    assert len(factory.agent.seen_messages) == 1


def test_semantic_judge_cannot_upgrade_application_completion_status(tmp_path):
    workdir = tmp_path / "project"
    workdir.mkdir()
    read_tool = Tool(
        name="inspect_test",
        description="Inspect test state.",
        parameters={"type": "object", "properties": {}},
        func=lambda: "observed",
        risk=Risk.READ,
    )
    factory = QueueClientFactory(
        [
            mock_tool_call("call-1", "inspect_test", "{}"),
            mock_text("Everything is complete."),
        ],
        [mock_text(json.dumps({
            "status": "completed",
            "confidence": 0.99,
            "reason": "The visible reply claims completion.",
            "missing": [],
        }))],
    )

    with NovalRuntime(
        application_config(tmp_path),
        client_factory=factory,
        tools=[read_tool],
    ) as runtime:
        session = runtime.create_session(SessionOptions(
            workdir=str(workdir),
            persistence=SessionPersistence.EPHEMERAL,
        ))
        result = session.run_turn(TurnRequest("inspect", goal=application_goal()))

    assert result.status is TurnStatus.UNCERTAIN
    assert result.completion is not None
    assert result.completion.status is CompletionStatus.UNCERTAIN
    assert result.completion.semantic is not None
    assert result.completion.semantic.status is CompletionStatus.COMPLETED
    assert len(result.receipts) == 1


def test_operational_failure_remains_failed_even_when_goal_was_complete(tmp_path):
    workdir = tmp_path / "project"
    workdir.mkdir()
    identity = ProviderIdentity("mock", "agent", "mock")

    class FailSecondClient:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return mock_text("first turn")
            raise ProviderError(
                ProviderErrorKind.TIMEOUT,
                "provider timed out",
                retryable=True,
                identity=identity,
            )

    client = FailSecondClient()

    def factory(spec):
        return client if spec.purpose == "agent" else MockClient([])

    with NovalRuntime(
        application_config(tmp_path), client_factory=factory
    ) as runtime:
        session = runtime.create_session(SessionOptions(
            workdir=str(workdir),
            persistence=SessionPersistence.EPHEMERAL,
        ))
        session.run_turn(TurnRequest("first", goal=application_goal()))
        session.record_verification(VerificationResult(
            verification_id="complete-before-failure",
            goal_id="application-goal",
            criterion_id="verified",
            source="host:validator",
            outcome=EvidenceOutcome.PASSED,
            observed_at=datetime.now(timezone.utc).isoformat(
                timespec="milliseconds"
            ),
        ))

        failed = session.run_turn(TurnRequest("second"))

    assert failed.status is TurnStatus.FAILED
    assert failed.stop_reason is StopReason.ERROR
    assert failed.error is not None
    assert failed.error.code == "provider_timeout"
    assert failed.completion is not None
    assert failed.completion.status is CompletionStatus.COMPLETED


def test_persistent_session_recovers_goal_and_external_verification(tmp_path):
    workdir = tmp_path / "project"
    workdir.mkdir()
    config = application_config(tmp_path)

    with NovalRuntime(
        config,
        client_factory=QueueClientFactory([mock_text("draft")]),
    ) as runtime:
        session = runtime.create_session(SessionOptions(
            workdir=str(workdir),
            persistence=SessionPersistence.PERSISTENT,
        ))
        session_id = session.info.session_id
        result = session.run_turn(TurnRequest("work", goal=application_goal()))
        assert result.status is TurnStatus.UNCERTAIN

    with NovalRuntime(
        config,
        client_factory=QueueClientFactory([mock_text("unused")]),
    ) as runtime:
        resumed = runtime.resume_session(session_id, SessionOptions(
            workdir=str(workdir),
            persistence=SessionPersistence.PERSISTENT,
        ))
        restored = resumed.completion_report()
        assert restored is not None
        assert restored.status is CompletionStatus.UNCERTAIN
        completed = resumed.record_verification(VerificationResult(
            verification_id="host-verification-resume",
            goal_id="application-goal",
            criterion_id="verified",
            source="host:validator",
            outcome=EvidenceOutcome.PASSED,
            observed_at=datetime.now(timezone.utc).isoformat(
                timespec="milliseconds"
            ),
        ))
        assert completed.status is CompletionStatus.COMPLETED

    with NovalRuntime(
        config,
        client_factory=QueueClientFactory([mock_text("unused")]),
    ) as runtime:
        resumed_again = runtime.resume_session(session_id, SessionOptions(
            workdir=str(workdir),
            persistence=SessionPersistence.PERSISTENT,
        ))
        final = resumed_again.completion_report()
        assert final is not None
        assert final.status is CompletionStatus.COMPLETED


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


def test_session_close_cannot_overtake_turn_admission(tmp_path, monkeypatch):
    workdir = tmp_path / "project"
    workdir.mkdir()
    runtime = NovalRuntime(
        application_config(tmp_path),
        client_factory=RecordingClientFactory(["done"]),
    )
    session = runtime.create_session(SessionOptions(
        workdir=str(workdir),
        persistence=SessionPersistence.EPHEMERAL,
    ))
    entered, proceed = block_process_turn_start(session, monkeypatch)
    results = []
    worker = threading.Thread(
        target=lambda: results.append(session.run_turn(TurnRequest("first")))
    )
    worker.start()
    assert entered.wait(2)

    try:
        with pytest.raises(NovalError) as busy:
            session.close()
        assert busy.value.code == "session_busy"
    finally:
        proceed.set()
        worker.join(3)
        if session.info.is_open:
            session.close()
        runtime.close()

    assert not worker.is_alive()
    assert results[0].status is TurnStatus.COMPLETED


def test_permission_changes_cannot_overtake_turn_admission(
    tmp_path, monkeypatch
):
    workdir = tmp_path / "project"
    workdir.mkdir()
    runtime = NovalRuntime(
        application_config(tmp_path),
        client_factory=RecordingClientFactory(["done"]),
    )
    session = runtime.create_session(SessionOptions(
        workdir=str(workdir),
        persistence=SessionPersistence.EPHEMERAL,
    ))
    entered, proceed = block_process_turn_start(session, monkeypatch)
    results = []
    worker = threading.Thread(
        target=lambda: results.append(session.run_turn(TurnRequest("first")))
    )
    worker.start()
    assert entered.wait(2)

    try:
        with pytest.raises(NovalError) as busy:
            session.set_permission_mode(PermissionMode.FULL_ACCESS)
        assert busy.value.code == "session_busy"
        with pytest.raises(NovalError) as verification_busy:
            session.record_verification(VerificationResult(
                verification_id="busy-verification",
                goal_id="application-goal",
                criterion_id="verified",
                source="host:validator",
                outcome=EvidenceOutcome.PASSED,
                observed_at=datetime.now(timezone.utc).isoformat(
                    timespec="milliseconds"
                ),
            ))
        assert verification_busy.value.code == "session_busy"
        assert session.permission_state().mode is PermissionMode.ASK
    finally:
        proceed.set()
        worker.join(3)
        session.close()
        runtime.close()

    assert not worker.is_alive()
    assert results[0].status is TurnStatus.COMPLETED


def test_runtime_close_cannot_overtake_turn_admission(tmp_path, monkeypatch):
    workdir = tmp_path / "project"
    workdir.mkdir()
    runtime = NovalRuntime(
        application_config(tmp_path),
        client_factory=RecordingClientFactory(["done"]),
    )
    session = runtime.create_session(SessionOptions(
        workdir=str(workdir),
        persistence=SessionPersistence.EPHEMERAL,
    ))
    entered, proceed = block_process_turn_start(session, monkeypatch)
    results = []
    worker = threading.Thread(
        target=lambda: results.append(session.run_turn(TurnRequest("first")))
    )
    worker.start()
    assert entered.wait(2)

    try:
        with pytest.raises(NovalError) as busy:
            runtime.close()
        assert busy.value.code == "runtime_busy"
    finally:
        proceed.set()
        worker.join(3)
        if session.info.is_open:
            session.close()
        runtime.close()

    assert not worker.is_alive()
    assert results[0].status is TurnStatus.COMPLETED


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
        assert len(result.receipts) == 1
        assert result.receipts[0].tool_name == "dangerous_test"
        assert result.receipts[0].outcome is ReceiptOutcome.SUCCEEDED
        assert result.receipts[0].executed is True
        assert "very-secret" not in json.dumps(
            result.receipts[0].to_dict(), ensure_ascii=False
        )
        assert session.permission_state().approved_tools == ("dangerous_test",)

    assert executed == ["very-secret"]
    assert permission_requests[0].arguments == {"password": "<redacted>"}
    event_types = [event.type for event in events]
    assert EventType.PERMISSION_REQUESTED.value in event_types
    assert EventType.PERMISSION_RESOLVED.value in event_types
    completed_event = next(
        event for event in events if event.type == EventType.TURN_COMPLETED.value
    )
    assert completed_event.payload["receipts"][0]["tool_name"] == "dangerous_test"
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


def test_application_maps_persistent_writer_conflicts_to_session_locked(tmp_path):
    workdir = tmp_path / "project"
    workdir.mkdir()
    config = application_config(tmp_path)
    first_factory = RecordingClientFactory(["saved"])
    first_runtime = NovalRuntime(config, client_factory=first_factory)
    first = first_runtime.create_session(SessionOptions(
        workdir=str(workdir), persistence=SessionPersistence.PERSISTENT,
    ))
    session_id = first.info.session_id
    first.run_turn(TurnRequest("persist this"))

    second_runtime = NovalRuntime(
        config, client_factory=RecordingClientFactory(["resumed"])
    )
    with pytest.raises(NovalError) as locked:
        second_runtime.resume_session(session_id, SessionOptions(
            workdir=str(workdir), persistence=SessionPersistence.PERSISTENT,
        ))
    assert locked.value.code == "session_locked"
    assert locked.value.retryable is True

    first_runtime.close()
    resumed = second_runtime.resume_session(session_id, SessionOptions(
        workdir=str(workdir), persistence=SessionPersistence.PERSISTENT,
    ))
    assert resumed.info.session_id == session_id
    second_runtime.close()
