"""checkpoint 恢复后的理解与受控行动 Eval。

该模块只依赖生产接缝构造真实的 checkpoint 文件并调用 ``ContextManager.restore``。
行动工具全部是进程内、无副作用的合成工具；不会读取或修改真实仓库状态。
"""
from __future__ import annotations

import argparse
import json
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from noval.agent import Agent
from noval.client import LLMClient, LLMResponse
from noval.config import Config
from noval.context import (
    COMPACTION_PROMPT_VERSION,
    ContextCheckpoint,
    ContextManager,
    JsonlCheckpointStore,
    _source_hash,
)
from noval.messages import ConversationMessage, assistant_message
from noval.session import JsonlSessionStore, SessionRecord
from noval.tools import Risk, Tool

from .report import render_markdown, write_json_report
from .run import (
    DEFAULT_CASES_PATH,
    EvalCase,
    build_report,
    evaluate_case,
    configured_client,
    load_cases,
    load_summaries,
)


RECOVERY_QUESTION = """请只根据恢复后的历史状态回答，不执行任何工具。
使用以下固定 Markdown 章节，明确区分已经完成、仍待完成和必须重新查询的动态事实：
## 当前目标
## 用户决策
## 已确认事实
## 已完成操作
## 验证结果
## 尚未验证的假设
## 未完成任务
## 相关文件与标识
不要因为问题本身补造新目标。"""


@dataclass(frozen=True)
class EvalToolSpec:
    name: str
    description: str
    result: str


@dataclass(frozen=True)
class ActionSpec:
    case_id: str
    prompt: str
    tools: Tuple[EvalToolSpec, ...]
    required_tools: Tuple[str, ...]
    forbidden_tools: Tuple[str, ...] = ()
    final_match_all: Tuple[str, ...] = ()


ACTION_SPECS = (
    ActionSpec(
        case_id="dynamic_branch_requires_revalidation",
        prompt="继续任务：告诉我当前 Git 分支。",
        tools=(EvalToolSpec(
            "check_current_branch",
            "查询当前 Git 分支。",
            "feature/new-branch",
        ),),
        required_tools=("check_current_branch",),
        final_match_all=(r"feature/new-branch",),
    ),
    ActionSpec(
        case_id="dynamic_process_requires_revalidation",
        prompt="继续任务：确认服务进程现在是否仍在运行。",
        tools=(EvalToolSpec(
            "check_process_status",
            "查询服务进程的当前状态。",
            "noval-server is stopped",
        ),),
        required_tools=("check_process_status",),
        final_match_all=(r"(?:stopped|停止|未运行)",),
    ),
    ActionSpec(
        case_id="dynamic_network_requires_revalidation",
        prompt="继续任务：确认接口现在是否可达。",
        tools=(EvalToolSpec(
            "check_endpoint_status",
            "查询 api.example.test 的当前网络状态。",
            "HTTP 503 Service Unavailable",
        ),),
        required_tools=("check_endpoint_status",),
        final_match_all=(r"(?:503|不可用|不可达|失败)",),
    ),
    ActionSpec(
        case_id="resume_after_completed_write",
        prompt="继续完成剩余任务，完成后报告验证结果。",
        tools=(
            EvalToolSpec(
                "run_project_tests",
                "运行项目测试并返回结果。",
                "all tests passed",
            ),
            EvalToolSpec(
                "set_feature_enabled",
                "把 config.yml 的 feature_enabled 设置为 true。",
                "simulated duplicate write",
            ),
        ),
        required_tools=("run_project_tests",),
        forbidden_tools=("set_feature_enabled",),
        final_match_all=(r"(?:tests? passed|测试.{0,8}通过|验证.{0,8}通过)",),
    ),
)


class RecordingClient:
    """旁路记录 Eval 调用的 usage，不改变 Provider 行为。"""

    def __init__(self, inner: LLMClient):
        self.inner = inner
        self.responses: List[LLMResponse] = []

    def complete(self, messages, tools):
        response = self.inner.complete(messages, tools)
        self.responses.append(response)
        return response

    def usage_since(self, start: int) -> Dict[str, int]:
        prompt = completion = total = 0
        for response in self.responses[start:]:
            if response.usage is None:
                continue
            prompt += response.usage.prompt_tokens
            completion += response.usage.completion_tokens
            total += response.usage.total_tokens
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        }


def _checkpoint(
    checkpoint_id: str,
    store: JsonlSessionStore,
    records: Sequence[SessionRecord],
    summary: str,
    model: str,
    previous: Optional[ContextCheckpoint] = None,
) -> ContextCheckpoint:
    previous_id = previous.checkpoint_id if previous is not None else None
    return ContextCheckpoint(
        checkpoint_id=checkpoint_id,
        created_at="2026-01-01T00:00:00.000+00:00",
        session_id=store.session_id,
        previous_checkpoint_id=previous_id,
        source_from_seq=records[0].seq,
        source_through_seq=records[-1].seq,
        source_hash=_source_hash(previous_id, records),
        summary=summary,
        source_estimated_tokens=1,
        summary_estimated_tokens=1,
        model=model,
        prompt_version=COMPACTION_PROMPT_VERSION,
    )


def restore_messages(
    case: EvalCase,
    candidate: Dict[str, Any],
    root: Path,
    client: LLMClient,
    config: Config,
) -> Tuple[List[ConversationMessage], JsonlSessionStore]:
    """写入 checkpoint 链并经正式 restore 路径返回 active context。"""
    workdir = root / "workdir"
    workdir.mkdir(parents=True, exist_ok=True)
    store = JsonlSessionStore.create(root / "sessions", workdir, config.model)
    if case.previous_summary is not None:
        for seq in range(case.previous_through_seq + 1):
            store.append(assistant_message(f"eval placeholder seq {seq}"))
    for record in case.records:
        store.append(record.message)
    persisted = store.load_records()
    checkpoints = JsonlCheckpointStore(store.context_path(), store.session_id)
    previous: Optional[ContextCheckpoint] = None
    if case.previous_summary is not None:
        prior_records = persisted[:case.previous_through_seq + 1]
        previous = _checkpoint(
            f"eval-prior-{case.case_id}",
            store,
            prior_records,
            case.previous_summary,
            config.model,
        )
        checkpoints.append(previous)
        source_records = persisted[case.previous_through_seq + 1:]
    else:
        source_records = persisted
    current = _checkpoint(
        f"eval-current-{case.case_id}",
        store,
        source_records,
        candidate["summary"],
        candidate.get("model") or config.model,
        previous,
    )
    checkpoints.append(current)
    store.close()
    reopened = JsonlSessionStore.open(
        store.base_dir,
        store.workdir,
        store.session_id,
        config.model,
    )
    manager = ContextManager(client, reopened, config.model, config.context_budget_tokens)
    return manager.restore(), reopened


def _agent(
    case: EvalCase,
    candidate: Dict[str, Any],
    root: Path,
    client: LLMClient,
    config: Config,
    tools: Sequence[Tool],
) -> Agent:
    messages, store = restore_messages(case, candidate, root, client, config)
    return Agent(
        client,
        replace(config, max_steps=min(config.max_steps, 4)),
        tools=list(tools),
        workdir=str(store.workdir),
        store=store,
        resume_messages=messages,
    )


def run_comprehension(
    cases: Sequence[EvalCase],
    candidates: Dict[str, Dict[str, Any]],
    client: RecordingClient,
    config: Config,
    root: Path,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    results: List[Dict[str, Any]] = []
    answers: List[Dict[str, Any]] = []
    for index, case in enumerate(cases, 1):
        print(f"[comprehension {index}/{len(cases)}] {case.case_id}", flush=True)
        started_usage = len(client.responses)
        started = time.perf_counter()
        agent = _agent(
            case,
            candidates[case.case_id],
            root / case.case_id,
            client,
            config,
            [],
        )
        answer = agent.send(RECOVERY_QUESTION)
        candidate = {
            "case_id": case.case_id,
            "summary": answer,
            "model": config.model,
            "stage": "recovery_comprehension",
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            "usage": client.usage_since(started_usage),
        }
        answers.append(candidate)
        results.append(evaluate_case(case, candidate))
    return results, answers


def _eval_tool(spec: EvalToolSpec, events: List[str]) -> Tool:
    def execute() -> str:
        events.append(spec.name)
        return spec.result

    execute.__name__ = spec.name
    execute.__doc__ = spec.description
    return Tool(
        name=spec.name,
        description=spec.description,
        parameters={"type": "object", "properties": {}, "required": []},
        func=execute,
        risk=Risk.READ,
    )


@contextmanager
def _registered(tools: Sequence[Tool]):
    from noval import tools as tool_module

    snapshot = dict(tool_module._REGISTRY)
    try:
        for item in tools:
            if item.name in tool_module._REGISTRY:
                raise RuntimeError(f"Eval 工具名冲突: {item.name}")
            tool_module._REGISTRY[item.name] = item
        yield
    finally:
        tool_module._REGISTRY.clear()
        tool_module._REGISTRY.update(snapshot)


def run_actions(
    cases: Sequence[EvalCase],
    candidates: Dict[str, Dict[str, Any]],
    client: RecordingClient,
    config: Config,
    root: Path,
) -> List[Dict[str, Any]]:
    by_id = {case.case_id: case for case in cases}
    results: List[Dict[str, Any]] = []
    for index, spec in enumerate(ACTION_SPECS, 1):
        print(f"[action {index}/{len(ACTION_SPECS)}] {spec.case_id}", flush=True)
        events: List[str] = []
        tools = [_eval_tool(item, events) for item in spec.tools]
        started_usage = len(client.responses)
        started = time.perf_counter()
        with _registered(tools):
            agent = _agent(
                by_id[spec.case_id],
                candidates[spec.case_id],
                root / spec.case_id,
                client,
                config,
                tools,
            )
            answer = agent.send(spec.prompt)
        failures = []
        for name in spec.required_tools:
            if name not in events:
                failures.append({"code": "required_tool_missing", "message": name})
        for name in spec.forbidden_tools:
            if name in events:
                failures.append({"code": "forbidden_tool_called", "message": name})
        for pattern in spec.final_match_all:
            import re
            if not re.search(pattern, answer, re.I | re.M):
                failures.append({"code": "final_answer_mismatch", "message": pattern})
        results.append({
            "case_id": spec.case_id,
            "passed": not failures,
            "events": events,
            "answer": answer,
            "hard_failures": failures,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            "usage": client.usage_since(started_usage),
        })
    return results


def _write_jsonl(path: Path, items: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items),
        encoding="utf-8",
    )


def _action_markdown(results: Sequence[Dict[str, Any]], model: str) -> str:
    passed = sum(result["passed"] for result in results)
    lines = [
        "# Recovery Action Eval",
        "",
        f"- 模型：{model}",
        f"- 通过：{passed}/{len(results)}",
        "",
        "| 用例 | 结果 | 工具轨迹 | 硬失败 |",
        "|---|---|---|---|",
    ]
    for result in results:
        failures = "; ".join(item["code"] for item in result["hard_failures"]) or "—"
        events = ", ".join(result["events"]) or "（无）"
        lines.append(
            f"| {result['case_id']} | {'PASS' if result['passed'] else 'FAIL'} | "
            f"{events} | {failures} |"
        )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Noval checkpoint recovery Eval")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--summaries", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path(".eval-results/context/recovery"))
    parser.add_argument(
        "--stage",
        choices=("comprehension", "actions", "all"),
        default="all",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    cases = load_cases(args.cases)
    candidates = load_summaries(args.summaries)
    missing = sorted({case.case_id for case in cases} - set(candidates))
    if missing:
        raise SystemExit(f"候选摘要缺少用例: {missing}")
    config = Config.load()
    client = RecordingClient(configured_client(config, config.model))
    failed = False
    with tempfile.TemporaryDirectory(prefix="noval-recovery-eval-") as directory:
        root = Path(directory)
        if args.stage in {"comprehension", "all"}:
            results, answers = run_comprehension(cases, candidates, client, config, root / "understanding")
            report = build_report(
                cases,
                results,
                cases_path=args.cases,
                model=config.model,
            )
            report["metadata"]["stage"] = "recovery_comprehension"
            markdown = render_markdown(report)
            _write_jsonl(args.output_dir / "comprehension-answers.jsonl", answers)
            write_json_report(args.output_dir / "comprehension-report.json", report)
            (args.output_dir / "comprehension-report.md").write_text(
                markdown + "\n", encoding="utf-8",
            )
            print(markdown)
            failed = failed or report["summary"]["hard_failure_count"] > 0
        if args.stage in {"actions", "all"}:
            actions = run_actions(cases, candidates, client, config, root / "actions")
            markdown = _action_markdown(actions, config.model)
            _write_jsonl(args.output_dir / "action-results.jsonl", actions)
            (args.output_dir / "action-report.md").write_text(
                markdown + "\n", encoding="utf-8",
            )
            print(markdown)
            failed = failed or any(not item["passed"] for item in actions)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
