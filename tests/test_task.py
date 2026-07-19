import json

from noval.agent import Agent
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


def test_controller_keeps_last_three_unique_user_inputs():
    controller = TaskController()

    for text in ["Goal A", "Goal B", "Goal A", "Goal C", "Goal D"]:
        controller.observe_user_input(text)

    assert controller.state.recent_user_inputs == ["Goal A", "Goal C", "Goal D"]


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
