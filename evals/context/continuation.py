"""同一 Agent 在对话内触发压缩后继续工作的 Eval。"""
from __future__ import annotations

import argparse
import json
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from noval.agent import Agent
from noval.client import OpenAICompatibleClient
from noval.config import Config
from noval.context import ContextManager
from noval.session import JsonlSessionStore

from .recovery import RecordingClient
from .report import render_markdown, write_json_report
from .run import (
    DEFAULT_CASES_PATH,
    EvalCase,
    build_report,
    evaluate_case,
    load_cases,
)


CONTINUATION_CASE_IDS = (
    "decision_resolves_agreement",
    "preserve_rejection",
    "commit_becomes_completed",
    "dynamic_branch_requires_revalidation",
    "secret_canary_redaction",
    "resume_after_completed_write",
)

CONTINUATION_QUESTION = """请基于压缩后的当前状态继续回答，不执行工具，也不要补造新目标。
使用以下固定 Markdown 章节，明确区分已经完成、仍待完成和必须重新查询的动态事实：
## 当前目标
## 用户决策
## 已确认事实
## 已完成操作
## 验证结果
## 尚未验证的假设
## 未完成任务
## 相关文件与标识"""


class BoundaryEstimator:
    """原历史触发软水位，移除较早回合后落入目标范围。"""

    def estimate(self, messages, tools):
        non_system = sum(message.get("role") != "system" for message in messages)
        return 7500 if non_system >= 5 else 3000

    def observe(self, messages, tools, actual_prompt_tokens):
        pass


def run_continuation_case(
    case: EvalCase,
    client: RecordingClient,
    config: Config,
    root: Path,
) -> Dict[str, Any]:
    if case.previous_summary is not None:
        raise ValueError(f"{case.case_id}: 对话内首次压缩用例不能预置 previous_summary")
    workdir = root / "workdir"
    workdir.mkdir(parents=True, exist_ok=True)
    store = JsonlSessionStore.create(root / "sessions", workdir, config.model)
    for record in case.records:
        store.append(record.msg)
    expected_through_seq = store.load_records()[-1].seq
    store.append({"role": "user", "content": "（最近回合占位：没有新任务或状态变化。）"})
    store.append({"role": "assistant", "content": "（收到，任务状态不变。）"})

    manager = ContextManager(
        client,
        store,
        config.model,
        10000,
        estimator=BoundaryEstimator(),
        preferred_recent_turns=1,
    )
    agent = Agent(
        client,
        replace(config, max_steps=min(config.max_steps, 4)),
        tools=[],
        workdir=str(workdir),
        store=store,
        resume_messages=manager.restore(),
        context_manager=manager,
    )
    started_usage = len(client.responses)
    started = time.perf_counter()
    answer = agent.send(CONTINUATION_QUESTION)
    checkpoint = manager.checkpoint
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    if checkpoint is None:
        raise RuntimeError(f"{case.case_id}: 没有生成 checkpoint")

    summary_candidate = {
        "case_id": case.case_id,
        "summary": checkpoint.summary,
        "model": config.model,
        "stage": "in_conversation_summary",
    }
    answer_candidate = {
        "case_id": case.case_id,
        "summary": answer,
        "model": config.model,
        "stage": "in_conversation_continuation",
    }
    summary_result = evaluate_case(case, summary_candidate)
    continuation_result = evaluate_case(case, answer_candidate)
    boundary_failures: List[Dict[str, str]] = []
    if checkpoint.source_through_seq != expected_through_seq:
        failure = {
            "code": "unexpected_compaction_boundary",
            "message": (
                f"期望覆盖至 seq {expected_through_seq}，"
                f"实际为 {checkpoint.source_through_seq}"
            ),
        }
        boundary_failures.append(failure)
        summary_result["hard_failures"].append(failure)
        continuation_result["hard_failures"].append(failure)
        summary_result["passed"] = False
        continuation_result["passed"] = False
    return {
        "case_id": case.case_id,
        "summary_candidate": summary_candidate,
        "answer_candidate": answer_candidate,
        "summary_result": summary_result,
        "continuation_result": continuation_result,
        "boundary_failures": boundary_failures,
        "checkpoint": {
            "checkpoint_id": checkpoint.checkpoint_id,
            "source_from_seq": checkpoint.source_from_seq,
            "source_through_seq": checkpoint.source_through_seq,
            "expected_through_seq": expected_through_seq,
        },
        "raw_message_count": len(store.load_records()),
        "duration_ms": duration_ms,
        "usage": client.usage_since(started_usage),
    }


def run_continuations(
    cases: Sequence[EvalCase],
    client: RecordingClient,
    config: Config,
    root: Path,
) -> List[Dict[str, Any]]:
    results = []
    for index, case in enumerate(cases, 1):
        print(f"[continuation {index}/{len(cases)}] {case.case_id}", flush=True)
        results.append(run_continuation_case(
            case,
            client,
            config,
            root / f"{index:03d}-{case.case_id}",
        ))
    return results


def _write_jsonl(path: Path, items: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items),
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Noval in-conversation compaction Eval")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".eval-results/context/continuation"),
    )
    parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        help="只运行指定 case id；可重复提供",
    )
    parser.add_argument("--repeat", type=int, default=1, help="每个选定用例重复次数")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    by_id = {case.case_id: case for case in load_cases(args.cases)}
    selected_ids = args.case_ids or list(CONTINUATION_CASE_IDS)
    unknown = sorted(set(selected_ids) - set(by_id))
    if unknown:
        raise SystemExit(f"未知用例: {unknown}")
    if args.repeat < 1:
        raise SystemExit("--repeat 必须大于等于 1")
    cases = [
        by_id[case_id]
        for _ in range(args.repeat)
        for case_id in selected_ids
    ]
    config = Config.load()
    client = RecordingClient(OpenAICompatibleClient(
        config.base_url,
        config.resolve_api_key(),
        config.model,
    ))
    with tempfile.TemporaryDirectory(prefix="noval-continuation-eval-") as directory:
        results = run_continuations(cases, client, config, Path(directory))

    summary_results = [item["summary_result"] for item in results]
    continuation_results = [item["continuation_result"] for item in results]
    summary_report = build_report(
        cases, summary_results, cases_path=args.cases, model=config.model,
    )
    summary_report["metadata"]["stage"] = "in_conversation_summary"
    continuation_report = build_report(
        cases, continuation_results, cases_path=args.cases, model=config.model,
    )
    continuation_report["metadata"]["stage"] = "in_conversation_continuation"

    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(args.output_dir / "results.jsonl", results)
    for name, report in (
        ("summary", summary_report),
        ("continuation", continuation_report),
    ):
        write_json_report(args.output_dir / f"{name}-report.json", report)
        (args.output_dir / f"{name}-report.md").write_text(
            render_markdown(report) + "\n",
            encoding="utf-8",
        )
    print("# In-conversation Compaction Eval")
    print()
    print(
        f"- 摘要通过：{summary_report['summary']['passed_count']}/{len(cases)}"
    )
    print(
        "- 继续回答通过："
        f"{continuation_report['summary']['passed_count']}/{len(cases)}"
    )
    print(
        "- 边界通过："
        f"{sum(not item['boundary_failures'] for item in results)}/{len(cases)}"
    )
    failed = (
        summary_report["summary"]["hard_failure_count"] > 0
        or continuation_report["summary"]["hard_failure_count"] > 0
        or any(item["boundary_failures"] for item in results)
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
