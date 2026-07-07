import json

from noval.agent import Agent
from noval.client import MockClient, mock_text, mock_tool_call
from noval.config import Config
from noval.executor import execute_tool_call
from noval.permissions import PermissionController, PermissionMode
from noval.task import (
    ActionMode,
    CompletionVerifier,
    CompletionVerdict,
    SemanticJudge,
    TaskController,
    TaskEventStore,
    TaskEvidence,
    TaskSpec,
    TaskState,
    TaskStatus,
)
from noval.tools import Context, Risk, tool


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
    state = TaskState(
        spec=TaskSpec("只查询问题原因", action_mode=ActionMode.READ_ONLY),
    )
    store.append_state(state, reason="start")
    state.status = TaskStatus.COMPLETED
    state.last_verdict = CompletionVerdict(
        status=TaskStatus.COMPLETED,
        confidence=1.0,
        reasons=["done"],
    )
    store.append_state(state, reason="completion_verdict")

    loaded = store.load_latest()

    assert loaded.status is TaskStatus.COMPLETED
    assert loaded.spec is not None
    assert loaded.spec.action_mode is ActionMode.READ_ONLY
    assert loaded.last_verdict is not None
    assert loaded.last_verdict.status is TaskStatus.COMPLETED


def test_readonly_task_guard_blocks_write_even_with_full_access(tmp_path):
    @tool(name="_task_write_guard", risk=Risk.WRITE)
    def write_guarded() -> str:
        """write guarded"""
        return "mutated"

    permissions = PermissionController()
    permissions.set_mode(PermissionMode.FULL_ACCESS)
    context = Context(workdir=tmp_path, permissions=permissions)
    controller = TaskController()
    controller.observe_user_input("只查询问题发生的原因，不修改代码")

    result = execute_tool_call(
        "_task_write_guard",
        "{}",
        cfg(),
        context=context,
        action_guard=controller.guard_action,
    )

    assert result.is_error
    assert result.meta["task_violation"] is True
    assert result.meta["effective_risk"] == "write"
    assert controller.state.status is TaskStatus.VIOLATED
    assert "只读" in controller.state.violations[0]


def test_diagnostic_question_does_not_become_task_guard_scope():
    controller = TaskController()

    controller.observe_user_input("这个错误发生的原因是什么：git pull fatal: couldn't find remote ref refs/heads/jinzhong")

    assert controller.state.spec is not None
    assert controller.state.spec.action_mode is ActionMode.UNSPECIFIED
    assert not controller.state.spec.prohibited_actions


def test_mutating_words_do_not_set_task_action_mode():
    controller = TaskController()

    controller.observe_user_input("修复配置读取问题")

    assert controller.state.spec is not None
    assert controller.state.spec.action_mode is ActionMode.UNSPECIFIED
    assert not controller.state.spec.prohibited_actions


def test_agent_records_task_events_and_tool_evidence(tmp_path):
    @tool(name="_task_read_ok", risk=Risk.READ)
    def read_ok() -> str:
        """read ok"""
        return "root cause: stale cache"

    task_path = tmp_path / "task.jsonl"
    controller = TaskController(event_store=TaskEventStore(task_path))
    client = MockClient([
        mock_tool_call("c1", "_task_read_ok", "{}"),
        mock_text("已完成：原因是缓存未刷新。"),
    ])
    agent = Agent(
        client,
        cfg(),
        workdir=str(tmp_path),
        task_controller=controller,
    )

    reply = agent.send("只查询问题原因")

    assert "已完成" in reply
    loaded = TaskEventStore(task_path).load_latest()
    assert loaded.status is TaskStatus.COMPLETED
    assert loaded.spec is not None
    assert loaded.spec.action_mode is ActionMode.READ_ONLY
    assert loaded.evidence
    assert loaded.evidence[-1].kind == "tool_result"
    assert loaded.evidence[-1].meta["effective_risk"] == "read"


def test_semantic_judge_uses_bounded_packet_without_main_history():
    state = TaskState(
        spec=TaskSpec(
            "排查重复数据",
            acceptance_criteria=["说明根因"],
            constraints=["不要修改代码"],
            action_mode=ActionMode.READ_ONLY,
        ),
        evidence=[
            TaskEvidence(
                evidence_id="ev1",
                kind="tool_result",
                summary="tool=grep; error=False",
            )
        ],
    )
    client = MockClient([
        mock_text(json.dumps({
            "status": "completed",
            "confidence": 0.91,
            "reasons": ["root cause identified"],
            "missing": [],
            "violations": [],
            "evidence_ids": ["ev1"],
        })),
    ])

    verdict = SemanticJudge(client, model="judge-model").judge(state, "候选最终回复")

    assert verdict.status is TaskStatus.COMPLETED
    assert verdict.source == "judge:judge-model"
    messages = client.seen_messages[0]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert "Noval" not in messages[0]["content"]
    packet = json.loads(messages[1]["content"])
    assert packet["task"]["objective"] == "排查重复数据"
    assert packet["evidence"][0]["evidence_id"] == "ev1"
    assert packet["candidate_reply"] == "候选最终回复"


def test_completion_verifier_handles_invalid_judge_json_as_waiting_user():
    state = TaskState(
        spec=TaskSpec(
            "复杂语义任务",
            constraints=["需要独立判定"],
        ),
    )
    verifier = CompletionVerifier(SemanticJudge(MockClient([mock_text("not json")]), model="judge"))

    verdict = verifier.verify(state, "暂时看起来可以")

    assert verdict.status is TaskStatus.WAITING_USER
    assert verdict.source == "judge_unavailable"
