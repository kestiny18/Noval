import json

from noval.agent import Agent
from noval.client import MockClient, mock_text, mock_tool_call
from noval.config import Config
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
    state = TaskState(recent_user_inputs=["排查重复数据"])
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
    assert loaded.recent_user_inputs == ["排查重复数据"]
    assert loaded.last_verdict is not None
    assert loaded.last_verdict.status is TaskStatus.COMPLETED


def test_controller_keeps_last_three_unique_user_inputs():
    controller = TaskController()

    for text in ["目标 A", "目标 B", "目标 A", "目标 C", "目标 D"]:
        controller.observe_user_input(text)

    assert controller.state.recent_user_inputs == ["目标 A", "目标 C", "目标 D"]


def test_completion_verifier_uses_judge_without_deterministic_completion_rules():
    state = TaskState(recent_user_inputs=["修复配置读取问题"])
    judge = SemanticJudge(
        MockClient([
            mock_text(json.dumps({
                "status": "incomplete",
                "confidence": 0.81,
                "reason": "tests were not mentioned",
                "missing": ["验证结果"],
            })),
        ]),
        model="judge-model",
    )

    verdict = CompletionVerifier(judge).verify(state, "已完成：配置读取问题已经修复。")

    assert verdict.status is TaskStatus.INCOMPLETE
    assert verdict.source == "judge:judge-model"
    assert verdict.reason == "tests were not mentioned"
    assert verdict.missing == ["验证结果"]


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
        ["旧任务", "排查重复数据", "排查重复数据", "解释错误原因"],
        "原因是远程分支不存在。",
    )

    assert verdict.status is TaskStatus.COMPLETED
    messages = client.seen_messages[0]
    assert [m["role"] for m in messages] == ["system", "user"]
    packet = json.loads(messages[1]["content"])
    assert packet["current_user_input"] == "解释错误原因"
    assert packet["context_user_inputs"] == ["旧任务", "排查重复数据"]
    assert packet["recent_user_inputs"] == ["旧任务", "排查重复数据", "解释错误原因"]
    assert packet["assistant_final_reply"] == "原因是远程分支不存在。"
    assert "evidence" not in packet
    assert "reasoning" not in json.dumps(packet, ensure_ascii=False).lower()
    assert "工具" in messages[0]["content"]
    assert "current_user_input" in messages[0]["content"]
    assert "context_user_inputs 只是" in messages[0]["content"]
    assert "不是本轮必须重新完成的任务清单" in messages[0]["content"]


def test_completion_verifier_handles_invalid_judge_json_as_uncertain():
    state = TaskState(recent_user_inputs=["复杂语义任务"])
    verifier = CompletionVerifier(SemanticJudge(MockClient([mock_text("not json")]), model="judge"))

    verdict = verifier.verify(state, "暂时看起来可以")

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
        mock_text("原因是缓存未刷新。"),
    ])
    agent = Agent(
        client,
        cfg(),
        workdir=str(tmp_path),
        task_controller=controller,
    )

    reply = agent.send("只查询问题原因")

    assert reply == "原因是缓存未刷新。"
    loaded = TaskEventStore(task_path).load_latest()
    assert loaded.status is TaskStatus.COMPLETED
    assert loaded.recent_user_inputs == ["只查询问题原因"]
    assert loaded.last_verdict is not None
    assert loaded.last_verdict.source == "judge:judge"
    packet = json.loads(judge_client.seen_messages[0][1]["content"])
    assert packet["current_user_input"] == "只查询问题原因"
    assert packet["context_user_inputs"] == []
    assert packet["recent_user_inputs"] == ["只查询问题原因"]
    assert packet["assistant_final_reply"] == "原因是缓存未刷新。"
