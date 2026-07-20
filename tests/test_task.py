import json
from datetime import datetime, timezone

import pytest

from noval.agent import Agent
from noval.api import (
    AcceptanceCriterion,
    ActionReceipt,
    CompletionStatus,
    CriterionStatus,
    EvidenceOutcome,
    GoalContract,
    ReceiptKind,
    ReceiptOutcome,
    VerificationResult,
)
from noval.client import MockClient, mock_text, mock_tool_call
from noval.config import Config
from noval.messages import MessageRole
from noval.task import (
    CompletionVerifier,
    CompletionVerdict,
    SemanticJudge,
    TaskController,
    TaskEventStore,
    TaskState,
    TaskStatus,
)
from noval.tools import Risk, tool


def cfg():
    return Config(
        model="m",
        base_url="u",
        api_key_env="K",
        max_steps=5,
        max_tool_output_chars=8000,
    )


def structured_goal(*, max_age_seconds=None, source="host:test-suite"):
    return GoalContract(
        goal_id="goal-1",
        objective="Deliver the verified change.",
        scope=("current repository",),
        authority=("modify files requested by the user",),
        acceptance_criteria=(
            AcceptanceCriterion(
                criterion_id="tests",
                description="The required test suite passes.",
                verification_source=source,
                max_age_seconds=max_age_seconds,
            ),
        ),
    )


def verification(
    *,
    outcome=EvidenceOutcome.PASSED,
    observed_at="2026-07-20T12:00:00+00:00",
    source="host:test-suite",
    goal_id="goal-1",
    criterion_id="tests",
):
    return VerificationResult(
        verification_id="verification-1",
        goal_id=goal_id,
        criterion_id=criterion_id,
        source=source,
        outcome=outcome,
        observed_at=observed_at,
    )


def test_task_event_store_replays_latest_snapshot(tmp_path):
    store = TaskEventStore(tmp_path / "task.jsonl")
    state = TaskState(recent_user_inputs=["Investigate duplicate data"])
    store.append_state(state, reason="user_input_observed")
    state.status = TaskStatus.COMPLETED
    state.last_verdict = CompletionVerdict(
        status=TaskStatus.COMPLETED,
        confidence=1.0,
        reason="done",
    )
    store.append_state(state, reason="completion_verdict")

    loaded = store.load_latest()

    assert loaded.status is TaskStatus.COMPLETED
    assert loaded.recent_user_inputs == ["Investigate duplicate data"]
    assert loaded.last_verdict is not None
    assert loaded.last_verdict.status is TaskStatus.COMPLETED


def test_task_event_store_v2_recovers_goal_evidence_and_skips_corrupt_tail(tmp_path):
    path = tmp_path / "task.jsonl"
    now = datetime(2026, 7, 20, 12, 0, 1, tzinfo=timezone.utc)
    controller = TaskController(event_store=TaskEventStore(path), now=lambda: now)
    controller.activate_goal(structured_goal(max_age_seconds=60))
    controller.record_verification(verification())
    with path.open("a", encoding="utf-8") as file:
        file.write('{"schema_version":2,"state":')

    records = path.read_text(encoding="utf-8").splitlines()
    loaded = TaskEventStore(path).load_latest()
    resumed = TaskController(state=loaded, now=lambda: now)
    report = resumed.completion_report()

    assert all(json.loads(line)["schema_version"] == 2 for line in records[:-1])
    assert loaded.active_goal == structured_goal(max_age_seconds=60)
    assert loaded.verifications == [verification()]
    assert report is not None
    assert report.status is CompletionStatus.COMPLETED


def test_task_event_store_migrates_schema_v1_semantic_snapshot(tmp_path):
    path = tmp_path / "task.jsonl"
    legacy = {
        "schema_version": 1,
        "timestamp": "2026-07-19T12:00:00+00:00",
        "type": "state_snapshot",
        "reason": "completion_verdict",
        "state": {
            "status": "completed",
            "recent_user_inputs": ["Explain the result"],
            "last_verdict": {
                "status": "completed",
                "confidence": 0.8,
                "reason": "The visible reply answered the question.",
                "missing": [],
                "source": "judge:legacy",
            },
        },
        "payload": {},
    }
    path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")

    loaded = TaskEventStore(path).load_latest()

    assert loaded.status is TaskStatus.COMPLETED
    assert loaded.recent_user_inputs == ["Explain the result"]
    assert loaded.last_verdict is not None
    assert loaded.last_verdict.source == "judge:legacy"
    assert loaded.active_goal is None
    assert loaded.receipts == []
    assert loaded.verifications == []


def test_task_state_keeps_bounded_receipt_and_verification_history():
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    controller = TaskController(now=lambda: now)
    controller.activate_goal(structured_goal(source=None))

    for index in range(140):
        receipt = ActionReceipt(
            receipt_id=f"receipt-{index}",
            call_id=f"call-{index}",
            tool_name="read_file",
            target="tool:read_file",
            kind=ReceiptKind.OBSERVATION,
            risk="read",
            outcome=ReceiptOutcome.SUCCEEDED,
            executed=True,
            started_at="2026-07-20T12:00:00+00:00",
            completed_at="2026-07-20T12:00:00+00:00",
        )
        controller.record_receipt(receipt)
        controller.record_verification(VerificationResult(
            verification_id=f"verification-{index}",
            goal_id="goal-1",
            criterion_id="tests",
            source="host:test-suite",
            outcome=EvidenceOutcome.PASSED,
            observed_at="2026-07-20T12:00:00+00:00",
            receipt_ids=(receipt.receipt_id,),
        ))

    assert len(controller.state.receipts) == 128
    assert len(controller.state.verifications) == 128
    assert controller.state.receipts[0].receipt_id == "receipt-12"
    assert controller.state.verifications[0].verification_id == "verification-12"


def test_verification_free_text_is_redacted_before_task_persistence(tmp_path):
    path = tmp_path / "task.jsonl"
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    controller = TaskController(event_store=TaskEventStore(path), now=lambda: now)
    controller.activate_goal(structured_goal())

    controller.record_verification(VerificationResult(
        verification_id="verification-secret",
        goal_id="goal-1",
        criterion_id="tests",
        source="host:test-suite",
        outcome=EvidenceOutcome.PASSED,
        observed_at="2026-07-20T12:00:00+00:00",
        subject="token=super-sensitive-value",
        summary="api_key=super-sensitive-value",
    ))

    persisted = path.read_text(encoding="utf-8")
    assert "super-sensitive-value" not in persisted
    assert "<redacted>" in persisted


def test_controller_keeps_last_three_unique_user_inputs():
    controller = TaskController()

    for text in ["Goal A", "Goal B", "Goal A", "Goal C", "Goal D"]:
        controller.observe_user_input(text)

    assert controller.state.recent_user_inputs == ["Goal A", "Goal C", "Goal D"]


def test_explicit_goal_is_uncertain_until_every_criterion_has_evidence():
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    controller = TaskController(now=lambda: now)

    report = controller.activate_goal(structured_goal())

    assert report.status is CompletionStatus.UNCERTAIN
    assert report.criteria[0].status is CriterionStatus.MISSING
    assert controller.state.status is TaskStatus.UNCERTAIN


def test_compatible_goal_can_repeat_but_same_id_cannot_be_redefined():
    controller = TaskController()
    goal = structured_goal()

    controller.activate_goal(goal)
    assert controller.activate_goal(goal).goal_id == goal.goal_id

    changed = GoalContract(
        goal_id=goal.goal_id,
        objective="A different objective.",
        acceptance_criteria=goal.acceptance_criteria,
    )
    with pytest.raises(ValueError, match="redefine"):
        controller.activate_goal(changed)


def test_matching_current_verification_completes_explicit_goal():
    now = datetime(2026, 7, 20, 12, 0, 1, tzinfo=timezone.utc)
    controller = TaskController(now=lambda: now)
    controller.activate_goal(structured_goal(max_age_seconds=60))

    report = controller.record_verification(verification())

    assert report.status is CompletionStatus.COMPLETED
    assert report.criteria[0].status is CriterionStatus.PASSED
    assert report.criteria[0].age_seconds == 1.0
    assert controller.state.status is TaskStatus.COMPLETED


@pytest.mark.parametrize(
    ("outcome", "expected_criterion", "expected_completion"),
    [
        (EvidenceOutcome.FAILED, CriterionStatus.FAILED, CompletionStatus.INCOMPLETE),
        (EvidenceOutcome.UNKNOWN, CriterionStatus.UNKNOWN, CompletionStatus.UNCERTAIN),
    ],
)
def test_failed_and_unknown_verification_do_not_complete_goal(
    outcome, expected_criterion, expected_completion
):
    now = datetime(2026, 7, 20, 12, 0, 1, tzinfo=timezone.utc)
    controller = TaskController(now=lambda: now)
    controller.activate_goal(structured_goal())

    report = controller.record_verification(verification(outcome=outcome))

    assert report.status is expected_completion
    assert report.criteria[0].status is expected_criterion


def test_stale_verification_is_uncertain():
    now = datetime(2026, 7, 20, 12, 2, tzinfo=timezone.utc)
    controller = TaskController(now=lambda: now)
    controller.activate_goal(structured_goal(max_age_seconds=60))

    report = controller.record_verification(verification())

    assert report.status is CompletionStatus.UNCERTAIN
    assert report.criteria[0].status is CriterionStatus.STALE
    assert report.criteria[0].age_seconds == 120.0


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (verification(goal_id="another-goal"), "active goal"),
        (verification(criterion_id="unknown"), "criterion"),
        (verification(source="host:other"), "source"),
        (
            verification(observed_at="2026-07-20T12:10:00+00:00"),
            "future",
        ),
    ],
)
def test_verification_must_match_goal_criterion_source_and_time(result, message):
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    controller = TaskController(now=lambda: now)
    controller.activate_goal(structured_goal())

    with pytest.raises(ValueError, match=message):
        controller.record_verification(result)


def test_semantic_judge_cannot_upgrade_missing_contracted_evidence():
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    judge = SemanticJudge(
        MockClient([
            mock_text(json.dumps({
                "status": "completed",
                "confidence": 0.99,
                "reason": "The reply confidently says it is done.",
                "missing": [],
            })),
        ]),
        model="judge-model",
    )
    controller = TaskController(
        completion_verifier=CompletionVerifier(judge),
        now=lambda: now,
    )
    controller.activate_goal(structured_goal())
    controller.observe_user_input("Do the work.")

    verdict = controller.verify_completion("Everything is complete.")
    report = controller.completion_report()

    assert verdict.status is TaskStatus.COMPLETED
    assert report is not None
    assert report.status is CompletionStatus.UNCERTAIN
    assert report.semantic is not None
    assert report.semantic.status is CompletionStatus.COMPLETED
    assert controller.state.status is TaskStatus.UNCERTAIN


def test_goal_context_is_observed_data_and_not_a_permission_grant():
    controller = TaskController()
    controller.activate_goal(structured_goal())

    context = controller.goal_context()

    assert context is not None
    assert '"goal_id":"goal-1"' in context
    assert '"status":"uncertain"' in context
    assert "does not grant permission" in context


def test_completion_verifier_uses_judge_without_deterministic_completion_rules():
    state = TaskState(recent_user_inputs=["Fix the configuration loading issue"])
    judge = SemanticJudge(
        MockClient([
            mock_text(json.dumps({
                "status": "incomplete",
                "confidence": 0.81,
                "reason": "tests were not mentioned",
                "missing": ["verification evidence"],
            })),
        ]),
        model="judge-model",
    )

    verdict = CompletionVerifier(judge).verify(state, "Completed: the configuration loading issue is fixed.")

    assert verdict.status is TaskStatus.INCOMPLETE
    assert verdict.source == "judge:judge-model"
    assert verdict.reason == "tests were not mentioned"
    assert verdict.missing == ["verification evidence"]


def test_semantic_judge_uses_only_recent_inputs_and_final_reply():
    client = MockClient([
        mock_text(json.dumps({
            "status": "completed",
            "confidence": 0.91,
            "reason": "answered the latest request",
            "missing": [],
        })),
    ])
    judge = SemanticJudge(client, model="judge-model")

    verdict = judge.judge(
        ["Old task", "Investigate duplicate data", "Investigate duplicate data", "Explain the error"],
        "The remote branch does not exist.",
    )

    assert verdict.status is TaskStatus.COMPLETED
    messages = client.seen_messages[0]
    assert [m.role for m in messages] == [MessageRole.SYSTEM, MessageRole.USER]
    packet = json.loads(messages[1].text)
    assert packet["current_user_input"] == "Explain the error"
    assert packet["context_user_inputs"] == ["Old task", "Investigate duplicate data"]
    assert packet["recent_user_inputs"] == ["Old task", "Investigate duplicate data", "Explain the error"]
    assert packet["assistant_final_reply"] == "The remote branch does not exist."
    assert "evidence" not in packet
    assert "reasoning" not in json.dumps(packet, ensure_ascii=False).lower()
    assert "tools" in messages[0].text
    assert "current_user_input" in messages[0].text
    assert "context_user_inputs provide context for references and background" in messages[0].text
    assert "not a list of tasks that must be completed again" in messages[0].text
    assert "do not claim that an operation did or did not occur" in messages[0].text
    assert "final reply does not provide sufficient evidence" in messages[0].text
    assert "must not claim that an unobserved action" in packet["instruction"]


def test_completion_verifier_handles_invalid_judge_json_as_uncertain():
    state = TaskState(recent_user_inputs=["Complex semantic task"])
    verifier = CompletionVerifier(SemanticJudge(MockClient([mock_text("not json")]), model="judge"))

    verdict = verifier.verify(state, "It appears acceptable for now")

    assert verdict.status is TaskStatus.UNCERTAIN
    assert verdict.source == "judge_unavailable"


def test_agent_judges_final_reply_after_tool_loop(tmp_path):
    @tool(name="_task_read_ok", risk=Risk.READ)
    def read_ok() -> str:
        """read ok"""
        return "root cause: stale cache"

    task_path = tmp_path / "task.jsonl"
    judge_client = MockClient([
        mock_text(json.dumps({
            "status": "completed",
            "confidence": 0.9,
            "reason": "root cause was explained",
            "missing": [],
        })),
    ])
    controller = TaskController(
        event_store=TaskEventStore(task_path),
        completion_verifier=CompletionVerifier(SemanticJudge(judge_client, model="judge")),
    )
    client = MockClient([
        mock_tool_call("c1", "_task_read_ok", "{}"),
        mock_text("The cache was not refreshed."),
    ])
    agent = Agent(
        client,
        cfg(),
        workdir=str(tmp_path),
        task_controller=controller,
    )

    reply = agent.send("Only identify the cause")

    assert reply == "The cache was not refreshed."
    loaded = TaskEventStore(task_path).load_latest()
    assert loaded.status is TaskStatus.COMPLETED
    assert loaded.recent_user_inputs == ["Only identify the cause"]
    assert loaded.last_verdict is not None
    assert loaded.last_verdict.source == "judge:judge"
    packet = json.loads(judge_client.seen_messages[0][1].text)
    assert packet["current_user_input"] == "Only identify the cause"
    assert packet["context_user_inputs"] == []
    assert packet["recent_user_inputs"] == ["Only identify the cause"]
    assert packet["assistant_final_reply"] == "The cache was not refreshed."


def test_agent_skips_completion_judge_for_direct_reply(tmp_path):
    task_path = tmp_path / "task.jsonl"
    judge_client = MockClient([
        mock_text(json.dumps({
            "status": "completed",
            "confidence": 0.9,
            "reason": "would be wasteful",
            "missing": [],
        })),
    ])
    controller = TaskController(
        event_store=TaskEventStore(task_path),
        completion_verifier=CompletionVerifier(SemanticJudge(judge_client, model="judge")),
    )
    agent = Agent(
        MockClient([mock_text("Hello!")]),
        cfg(),
        workdir=str(tmp_path),
        task_controller=controller,
    )

    assert agent.send("hi") == "Hello!"

    assert judge_client.seen_messages == []
    loaded = TaskEventStore(task_path).load_latest()
    assert loaded.recent_user_inputs == ["hi"]
    assert loaded.last_verdict is None
