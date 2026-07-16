"""运行 context checkpoint Eval。

默认只校验用例资产，零网络、零模型费用。传入 ``--generate`` 时，使用
~/.noval/settings.json 配置的模型生成候选摘要；也可以用 ``--summaries``
离线重放已保存的候选结果。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from noval.context import (
    COMPACTION_PROMPT_VERSION,
    SUMMARY_HEADINGS,
    build_compaction_messages,
)
from noval.client import LLMClient, create_provider_client
from noval.config import Config
from noval.messages import (
    ConversationMessage, MessageRole, ToolCallBlock, assistant_message,
    system_message, tool_result_message, user_message,
)
from noval.session import SessionRecord

from .report import render_markdown, write_json_report


DEFAULT_CASES_PATH = Path(__file__).with_name("cases.jsonl")
SEQ_REFERENCE_RE = re.compile(r"(?i)\bseq\s*(?:[:=#]\s*|\s+)(\d+)")
CATEGORY_WEIGHTS = {
    "current_goal": 20,
    "user_decisions": 25,
    "confirmed_facts": 15,
    "completed_actions": 10,
    "pending_tasks": 15,
    "stale_information": 10,
}


class CaseFormatError(ValueError):
    """Eval 资产本身不合法。"""


@dataclass(frozen=True)
class Expectation:
    expectation_id: str
    category: str
    statement: str
    match_all: Tuple[str, ...]
    section: Optional[str] = None
    hard_failure: Optional[str] = None

    def matches(self, summary: str) -> bool:
        target = _section_content(summary, self.section) if self.section else summary
        return all(re.search(pattern, target, re.I | re.M) for pattern in self.match_all)


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    title: str
    previous_summary: Optional[str]
    previous_through_seq: int
    records: Tuple[SessionRecord, ...]
    expectations: Tuple[Expectation, ...]
    forbidden: Tuple[Expectation, ...]
    secret_canaries: Tuple[str, ...]
    secret_forbidden_fragments: Tuple[str, ...]
    max_summary_chars: int

    @property
    def through_seq(self) -> int:
        if self.records:
            return self.records[-1].seq
        return self.previous_through_seq


def _expectation(data: Any, *, case_id: str) -> Expectation:
    if not isinstance(data, dict):
        raise CaseFormatError(f"{case_id}: expectation 必须是对象")
    expectation_id = data.get("id")
    category = data.get("category")
    statement = data.get("statement")
    patterns = data.get("match_all")
    if not isinstance(expectation_id, str) or not expectation_id:
        raise CaseFormatError(f"{case_id}: expectation.id 必须是非空字符串")
    if category not in CATEGORY_WEIGHTS:
        raise CaseFormatError(f"{case_id}/{expectation_id}: 未知 category {category!r}")
    if not isinstance(statement, str) or not statement:
        raise CaseFormatError(f"{case_id}/{expectation_id}: statement 必须是非空字符串")
    if not isinstance(patterns, list) or not patterns or not all(
        isinstance(pattern, str) and pattern for pattern in patterns
    ):
        raise CaseFormatError(f"{case_id}/{expectation_id}: match_all 必须是非空字符串数组")
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as error:
            raise CaseFormatError(
                f"{case_id}/{expectation_id}: 非法正则 {pattern!r}: {error}"
            ) from error
    hard_failure = data.get("hard_failure")
    if hard_failure is not None and not isinstance(hard_failure, str):
        raise CaseFormatError(f"{case_id}/{expectation_id}: hard_failure 必须是字符串")
    section = data.get("section")
    if section is not None and section not in SUMMARY_HEADINGS:
        raise CaseFormatError(
            f"{case_id}/{expectation_id}: section 必须是固定章节之一，实际为 {section!r}"
        )
    return Expectation(
        expectation_id=expectation_id,
        category=category,
        statement=statement,
        match_all=tuple(patterns),
        section=section,
        hard_failure=hard_failure,
    )


def _section_content(summary: str, heading: str) -> str:
    """只返回指定固定章节正文，防止有界正则误穿透到下一个章节。"""
    start = summary.find(heading)
    if start < 0:
        return ""
    content_start = start + len(heading)
    following = [
        summary.find(candidate, content_start)
        for candidate in SUMMARY_HEADINGS
        if summary.find(candidate, content_start) >= 0
    ]
    content_end = min(following) if following else len(summary)
    return summary[content_start:content_end]


def _records(data: Dict[str, Any], *, case_id: str, previous_through_seq: int) -> Tuple[SessionRecord, ...]:
    raw_records = data.get("records")
    raw_messages = data.get("messages")
    if (raw_records is None) == (raw_messages is None):
        raise CaseFormatError(f"{case_id}: records 与 messages 必须且只能提供一个")
    if raw_messages is not None:
        if not isinstance(raw_messages, list) or not raw_messages:
            raise CaseFormatError(f"{case_id}: messages 必须是非空数组")
        start = previous_through_seq + 1
        records = [
            SessionRecord(
                seq=start + index,
                ts=f"2026-01-01T00:00:{index:02d}+00:00",
                message=_canonical_fixture_message(message, case_id=case_id),
            )
            for index, message in enumerate(raw_messages)
            if isinstance(message, dict)
        ]
        if len(records) != len(raw_messages):
            raise CaseFormatError(f"{case_id}: messages 中每一项都必须是对象")
    else:
        if not isinstance(raw_records, list) or not raw_records:
            raise CaseFormatError(f"{case_id}: records 必须是非空数组")
        records = []
        for raw in raw_records:
            if not isinstance(raw, dict):
                raise CaseFormatError(f"{case_id}: record 必须是对象")
            seq, ts, msg = raw.get("seq"), raw.get("ts"), raw.get("msg")
            if not isinstance(seq, int) or not isinstance(ts, str) or not isinstance(msg, dict):
                raise CaseFormatError(f"{case_id}: record 需要合法的 seq/ts/msg")
            records.append(SessionRecord(
                seq=seq,
                ts=ts,
                message=_canonical_fixture_message(msg, case_id=case_id),
            ))

    expected_seq = list(range(previous_through_seq + 1, previous_through_seq + 1 + len(records)))
    actual_seq = [record.seq for record in records]
    if actual_seq != expected_seq:
        raise CaseFormatError(
            f"{case_id}: seq 必须从 {previous_through_seq + 1} 连续递增，实际为 {actual_seq}"
        )
    _validate_tool_protocol(case_id, records)
    return tuple(records)


def _validate_tool_protocol(case_id: str, records: Sequence[SessionRecord]) -> None:
    pending: Dict[str, int] = {}
    answered: set[str] = set()
    for record in records:
        message = record.message
        if message.role is MessageRole.ASSISTANT:
            for call in message.tool_calls:
                call_id = call.id
                if call_id in pending or call_id in answered:
                    raise CaseFormatError(f"{case_id}: tool_call id {call_id!r} 重复")
                pending[call_id] = record.seq
        elif message.role is MessageRole.TOOL:
            for result in message.tool_results:
                call_id = result.call_id
                if call_id not in pending:
                    raise CaseFormatError(
                        f"{case_id}: seq {record.seq} 是孤立 tool 结果 {call_id!r}"
                    )
                pending.pop(call_id)
                answered.add(call_id)
    if pending:
        detail = ", ".join(f"{call_id}@seq{seq}" for call_id, seq in pending.items())
        raise CaseFormatError(f"{case_id}: source 拆断 tool-call 协议: {detail}")


def _canonical_fixture_message(data: Dict[str, Any], *, case_id: str) -> ConversationMessage:
    """Translate legacy Eval fixtures; this is not a Session v1 decoder."""
    role = data.get("role")
    content = data.get("content")
    text = content if isinstance(content, str) else None
    if role == "system":
        return system_message(text or "")
    if role == "user":
        return user_message(text or "")
    if role == "assistant":
        calls = []
        for raw_call in data.get("tool_calls") or []:
            function = raw_call.get("function") if isinstance(raw_call, dict) else None
            if not isinstance(function, dict):
                raise CaseFormatError(f"{case_id}: assistant tool_call 格式非法")
            call_id, name, arguments = (
                raw_call.get("id"), function.get("name"), function.get("arguments"),
            )
            if not all(isinstance(value, str) for value in (call_id, name, arguments)):
                raise CaseFormatError(f"{case_id}: assistant tool_call 字段非法")
            calls.append(ToolCallBlock(call_id, name, arguments))
        return assistant_message(text, tool_calls=calls)
    if role == "tool":
        call_id = data.get("tool_call_id")
        if not isinstance(call_id, str) or not call_id:
            raise CaseFormatError(f"{case_id}: tool 结果缺少 tool_call_id")
        return tool_result_message(call_id, text or "")
    raise CaseFormatError(f"{case_id}: 未知消息 role {role!r}")


def load_cases(path: Path = DEFAULT_CASES_PATH) -> List[EvalCase]:
    cases: List[EvalCase] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as error:
            raise CaseFormatError(f"{path}:{line_number}: JSON 非法: {error}") from error
        if not isinstance(data, dict):
            raise CaseFormatError(f"{path}:{line_number}: 用例必须是对象")
        case_id = data.get("id")
        title = data.get("title")
        if not isinstance(case_id, str) or not case_id:
            raise CaseFormatError(f"{path}:{line_number}: id 必须是非空字符串")
        if case_id in seen_ids:
            raise CaseFormatError(f"{path}:{line_number}: 重复 id {case_id!r}")
        seen_ids.add(case_id)
        if not isinstance(title, str) or not title:
            raise CaseFormatError(f"{case_id}: title 必须是非空字符串")
        previous_summary = data.get("previous_summary")
        if previous_summary is not None and not isinstance(previous_summary, str):
            raise CaseFormatError(f"{case_id}: previous_summary 必须是字符串或 null")
        previous_through_seq = data.get("previous_through_seq", -1)
        if not isinstance(previous_through_seq, int) or previous_through_seq < -1:
            raise CaseFormatError(f"{case_id}: previous_through_seq 必须是 >= -1 的整数")
        if (previous_summary is None) != (previous_through_seq == -1):
            raise CaseFormatError(
                f"{case_id}: previous_summary 与 previous_through_seq 必须同时存在或同时缺省"
            )
        records = _records(data, case_id=case_id, previous_through_seq=previous_through_seq)
        expectations = tuple(
            _expectation(item, case_id=case_id)
            for item in data.get("expectations", [])
        )
        forbidden = tuple(
            _expectation(item, case_id=case_id)
            for item in data.get("forbidden", [])
        )
        if not expectations and not forbidden:
            raise CaseFormatError(f"{case_id}: 至少需要一项 expectation 或 forbidden")
        canaries = data.get("secret_canaries", [])
        if not isinstance(canaries, list) or not all(isinstance(item, str) and item for item in canaries):
            raise CaseFormatError(f"{case_id}: secret_canaries 必须是字符串数组")
        fragments = data.get("secret_forbidden_fragments", [])
        if not isinstance(fragments, list) or not all(
            isinstance(item, str) and item for item in fragments
        ):
            raise CaseFormatError(
                f"{case_id}: secret_forbidden_fragments 必须是字符串数组"
            )
        max_summary_chars = data.get("max_summary_chars", 1800)
        if not isinstance(max_summary_chars, int) or max_summary_chars < 1:
            raise CaseFormatError(f"{case_id}: max_summary_chars 必须是正整数")
        cases.append(EvalCase(
            case_id=case_id,
            title=title,
            previous_summary=previous_summary,
            previous_through_seq=previous_through_seq,
            records=records,
            expectations=expectations,
            forbidden=forbidden,
            secret_canaries=tuple(canaries),
            secret_forbidden_fragments=tuple(fragments),
            max_summary_chars=max_summary_chars,
        ))
    if not cases:
        raise CaseFormatError(f"{path}: 没有用例")
    return cases


def load_summaries(path: Path) -> Dict[str, Dict[str, Any]]:
    summaries: Dict[str, Dict[str, Any]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as error:
            raise CaseFormatError(f"{path}:{line_number}: JSON 非法: {error}") from error
        if not isinstance(data, dict):
            raise CaseFormatError(f"{path}:{line_number}: 候选结果必须是对象")
        case_id, summary = data.get("case_id"), data.get("summary")
        if not isinstance(case_id, str) or not isinstance(summary, str):
            raise CaseFormatError(f"{path}:{line_number}: 需要字符串 case_id/summary")
        if case_id in summaries:
            raise CaseFormatError(f"{path}:{line_number}: 重复 case_id {case_id!r}")
        summaries[case_id] = data
    return summaries


def _source_chars(case: EvalCase) -> int:
    payload = {
        "previous_summary": case.previous_summary,
        "records": [
            {"seq": record.seq, "ts": record.ts, "message": record.message.semantic_dict()}
            for record in case.records
        ],
    }
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def evaluate_case(case: EvalCase, candidate: Dict[str, Any]) -> Dict[str, Any]:
    summary = candidate["summary"].strip()
    hard_failures: List[Dict[str, str]] = []
    structural_failures: List[Dict[str, str]] = []

    def fail(code: str, message: str, *, hard: bool = True) -> None:
        item = {"code": code, "message": message}
        structural_failures.append(item)
        if hard:
            hard_failures.append(item)

    if not summary:
        fail("empty_summary", "摘要为空")
    heading_positions: List[int] = []
    for heading in SUMMARY_HEADINGS:
        count = summary.count(heading)
        if count != 1:
            fail("heading_count", f"{heading} 出现 {count} 次，期望 1 次")
        heading_positions.append(summary.find(heading))
    if all(position >= 0 for position in heading_positions) and heading_positions != sorted(heading_positions):
        fail("heading_order", "八个固定章节顺序错误")
    if summary and not summary.startswith(SUMMARY_HEADINGS[0]):
        fail("leading_content", "摘要在第一个固定章节前包含额外内容")

    invalid_seq = sorted({
        int(match.group(1))
        for match in SEQ_REFERENCE_RE.finditer(summary)
        if int(match.group(1)) < 0 or int(match.group(1)) > case.through_seq
    })
    if invalid_seq:
        fail("out_of_source_seq", f"出现来源范围之外的 seq: {invalid_seq}")

    leaked = [canary for canary in case.secret_canaries if canary in summary]
    if leaked:
        fail("secret_leak", f"摘要泄露 {len(leaked)} 个合成凭据 canary")
    leaked_fragments = [
        fragment for fragment in case.secret_forbidden_fragments
        if fragment in summary
    ]
    if leaked_fragments:
        fail("secret_fragment_leak", f"摘要泄露 {len(leaked_fragments)} 个凭据片段")

    assertions: List[Dict[str, Any]] = []
    for expectation in case.expectations:
        matched = expectation.matches(summary)
        item = {
            "id": expectation.expectation_id,
            "category": expectation.category,
            "statement": expectation.statement,
            "kind": "must_preserve",
            "passed": matched,
        }
        assertions.append(item)
        if not matched and expectation.hard_failure:
            hard_failures.append({
                "code": expectation.hard_failure,
                "message": f"未保留关键状态: {expectation.statement}",
            })
    for expectation in case.forbidden:
        matched = expectation.matches(summary)
        item = {
            "id": expectation.expectation_id,
            "category": expectation.category,
            "statement": expectation.statement,
            "kind": "must_not_claim",
            "passed": not matched,
        }
        assertions.append(item)
        if matched and expectation.hard_failure:
            hard_failures.append({
                "code": expectation.hard_failure,
                "message": f"出现禁止状态: {expectation.statement}",
            })

    applicable_weights = 5
    earned_weights = 5 if len(summary) <= case.max_summary_chars else 0
    category_scores: Dict[str, Dict[str, Any]] = {}
    for category, weight in CATEGORY_WEIGHTS.items():
        relevant = [item for item in assertions if item["category"] == category]
        if not relevant:
            continue
        passed = sum(1 for item in relevant if item["passed"])
        ratio = passed / len(relevant)
        applicable_weights += weight
        earned_weights += weight * ratio
        category_scores[category] = {
            "passed": passed,
            "total": len(relevant),
            "score": round(ratio * 100, 1),
        }
    score = round(earned_weights / applicable_weights * 100, 1)
    source_chars = _source_chars(case)
    return {
        "case_id": case.case_id,
        "title": case.title,
        "passed": not hard_failures and all(item["passed"] for item in assertions),
        "score": score,
        "hard_failures": hard_failures,
        "structural_failures": structural_failures,
        "assertions": assertions,
        "category_scores": category_scores,
        "metrics": {
            "source_chars": source_chars,
            "summary_chars": len(summary),
            "compression_ratio": round(len(summary) / max(1, source_chars), 3),
            "max_summary_chars": case.max_summary_chars,
        },
        "candidate_meta": {
            key: value for key, value in candidate.items()
            if key not in {"summary", "case_id"}
        },
    }


def _generate(cases: Sequence[EvalCase]) -> Tuple[Dict[str, Dict[str, Any]], str]:
    config = Config.load()
    client = configured_client(config, config.model)
    candidates: Dict[str, Dict[str, Any]] = {}
    for index, case in enumerate(cases, 1):
        print(f"[{index}/{len(cases)}] {case.case_id}", file=sys.stderr, flush=True)
        started = time.perf_counter()
        response = client.complete(
            build_compaction_messages(case.previous_summary, case.records),
            [],
        )
        if not response.message.text:
            raise RuntimeError(f"{case.case_id}: 模型返回空摘要")
        usage = None
        if response.usage is not None:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
        candidates[case.case_id] = {
            "case_id": case.case_id,
            "summary": response.message.text.strip(),
            "model": config.model,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            "usage": usage,
        }
    return candidates, config.model


def configured_client(config: Config, model: str) -> LLMClient:
    return create_provider_client(
        config.provider,
        api_key=config.resolve_api_key(),
        model=model,
        base_url=config.base_url,
        anthropic_base_url=config.anthropic_base_url,
        timeout=config.request_timeout_seconds,
        max_retries=config.request_max_retries,
        anthropic_max_tokens=config.anthropic_max_tokens,
    )


def _write_candidates(path: Path, candidates: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in candidates)
    path.write_text(text, encoding="utf-8")


def _git_commit() -> Optional[str]:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            encoding="utf-8",
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _git_dirty() -> Optional[bool]:
    try:
        status = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            text=True,
            encoding="utf-8",
            stderr=subprocess.DEVNULL,
        )
        return bool(status.strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def _file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _prompt_hash() -> str:
    payload = json.dumps(
        [message.to_dict() for message in build_compaction_messages(None, [])],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_report(
    cases: Sequence[EvalCase],
    results: Sequence[Dict[str, Any]],
    *,
    cases_path: Path,
    model: Optional[str],
) -> Dict[str, Any]:
    hard_failure_count = sum(len(result["hard_failures"]) for result in results)
    passed_count = sum(1 for result in results if result["passed"])
    all_assertions = [
        assertion
        for result in results
        for assertion in result["assertions"]
    ]
    category_scores: Dict[str, Dict[str, Any]] = {}
    applicable_weight = 5
    concise_count = sum(
        result["metrics"]["summary_chars"] <= result["metrics"]["max_summary_chars"]
        for result in results
    )
    earned_weight = 5 * concise_count / max(1, len(results))
    for category, weight in CATEGORY_WEIGHTS.items():
        relevant = [item for item in all_assertions if item["category"] == category]
        if not relevant:
            continue
        passed = sum(item["passed"] for item in relevant)
        ratio = passed / len(relevant)
        applicable_weight += weight
        earned_weight += weight * ratio
        category_scores[category] = {
            "passed": passed,
            "total": len(relevant),
            "score": round(ratio * 100, 1),
            "weight": weight,
        }
    weighted_score = round(earned_weight / applicable_weight * 100, 1)
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metadata": {
            "model": model,
            "prompt_version": COMPACTION_PROMPT_VERSION,
            "prompt_hash": _prompt_hash(),
            "cases_hash": _file_hash(cases_path),
            "git_commit": _git_commit(),
            "git_dirty": _git_dirty(),
            "temperature": None,
            "semantic_method": "deterministic_regex_smoke_checks",
        },
        "summary": {
            "case_count": len(cases),
            "evaluated_count": len(results),
            "passed_count": passed_count,
            "hard_failure_count": hard_failure_count,
            "weighted_score": weighted_score,
            "category_scores": category_scores,
            "concision": {
                "passed": concise_count,
                "total": len(results),
                "weight": 5,
            },
        },
        "results": list(results),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Noval context checkpoint Eval")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--case", action="append", dest="case_ids")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--summaries", type=Path, help="离线候选摘要 JSONL")
    source.add_argument("--generate", action="store_true", help="调用当前配置的真实模型")
    parser.add_argument("--output", type=Path, help="--generate 时保存候选摘要 JSONL")
    parser.add_argument("--json-report", type=Path, help="保存机器可读报告")
    parser.add_argument("--markdown-report", type=Path, help="保存 Markdown 报告")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        all_cases = load_cases(args.cases)
        by_id = {case.case_id: case for case in all_cases}
        selected_ids = args.case_ids or [case.case_id for case in all_cases]
        unknown_cases = sorted(set(selected_ids) - set(by_id))
        if unknown_cases:
            raise CaseFormatError(f"未知用例: {unknown_cases}")
        cases = [by_id[case_id] for case_id in selected_ids]
        if not args.generate and args.summaries is None:
            print(
                f"PASS: {len(cases)} 个 context Eval 用例资产有效；"
                "使用 --generate 调用真实模型，或 --summaries 重放候选摘要。"
            )
            return 0
        if args.generate:
            candidates, model = _generate(cases)
            if args.output:
                _write_candidates(args.output, (candidates[case.case_id] for case in cases))
        else:
            candidates = load_summaries(args.summaries)
            model = next((item.get("model") for item in candidates.values() if item.get("model")), None)
        missing = [case.case_id for case in cases if case.case_id not in candidates]
        unknown = sorted(set(candidates) - {case.case_id for case in cases})
        if missing or unknown:
            raise CaseFormatError(f"候选集合不匹配: missing={missing}, unknown={unknown}")
        results = [evaluate_case(case, candidates[case.case_id]) for case in cases]
        report = build_report(cases, results, cases_path=args.cases, model=model)
        markdown = render_markdown(report)
        print(markdown)
        if args.json_report:
            write_json_report(args.json_report, report)
        if args.markdown_report:
            args.markdown_report.parent.mkdir(parents=True, exist_ok=True)
            args.markdown_report.write_text(markdown + "\n", encoding="utf-8")
        return 1 if report["summary"]["hard_failure_count"] else 0
    except (CaseFormatError, OSError, RuntimeError) as error:
        print(f"Eval 失败: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
