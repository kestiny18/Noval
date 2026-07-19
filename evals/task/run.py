"""Run deterministic task-completion judge Eval cases.

The default command is offline and zero-cost: it replays recent user inputs and
synthetic judge verdicts through ``noval.task`` to keep the task layer contract
stable without calling a real model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from noval.task import CompletionVerdict, CompletionVerifier, TaskController, TaskState, TaskStatus


DEFAULT_CASES_PATH = Path(__file__).with_name("cases.jsonl")


class TaskEvalFormatError(ValueError):
    """The Eval asset itself is invalid."""


@dataclass(frozen=True)
class TaskEvalEvent:
    event_type: str
    data: Dict[str, Any]


@dataclass(frozen=True)
class TaskEvalCase:
    case_id: str
    title: str
    events: Tuple[TaskEvalEvent, ...]
    expected: Dict[str, Any]


class QueueJudge:
    def __init__(self, verdicts: Sequence[Dict[str, Any]]):
        self.verdicts = list(verdicts)
        self.calls: List[Dict[str, Any]] = []

    def judge(self, recent_user_inputs: List[str], assistant_final_reply: str) -> CompletionVerdict:
        self.calls.append({
            "recent_user_inputs": list(recent_user_inputs),
            "assistant_final_reply": assistant_final_reply,
        })
        if not self.verdicts:
            raise AssertionError("case reply event is missing a judge verdict")
        data = self.verdicts.pop(0)
        return CompletionVerdict(
            status=_parse_status(str(data.get("status", "uncertain")), label="judge.status"),
            confidence=_float_between_zero_one(data.get("confidence")),
            reason=str(data.get("reason") or ""),
            missing=_string_list(data.get("missing")),
            source="eval_judge",
            prompt_version="task-eval",
        )


def load_cases(path: Path = DEFAULT_CASES_PATH) -> List[TaskEvalCase]:
    cases: List[TaskEvalCase] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as error:
            raise TaskEvalFormatError(f"{path}:{line_number}: invalid JSON: {error}") from error
        if not isinstance(data, dict):
            raise TaskEvalFormatError(f"{path}:{line_number}: case must be an object")
        case_id = _required_str(data, "id", label=f"{path}:{line_number}")
        if case_id in seen:
            raise TaskEvalFormatError(f"{path}:{line_number}: duplicate id {case_id!r}")
        seen.add(case_id)
        title = _required_str(data, "title", label=case_id)
        raw_events = data.get("events")
        if not isinstance(raw_events, list) or not raw_events:
            raise TaskEvalFormatError(f"{case_id}: events must be a non-empty array")
        events = tuple(_event(case_id, index, item) for index, item in enumerate(raw_events))
        expected = data.get("expected")
        if not isinstance(expected, dict) or not expected:
            raise TaskEvalFormatError(f"{case_id}: expected must be a non-empty object")
        _validate_expected(case_id, expected)
        cases.append(TaskEvalCase(case_id=case_id, title=title, events=events, expected=expected))
    if not cases:
        raise TaskEvalFormatError(f"{path}: no cases")
    return cases


def _event(case_id: str, index: int, data: Any) -> TaskEvalEvent:
    if not isinstance(data, dict):
        raise TaskEvalFormatError(f"{case_id}: event[{index}] must be an object")
    event_type = _required_str(data, "type", label=f"{case_id}: event[{index}]")
    if event_type not in {"user", "reply"}:
        raise TaskEvalFormatError(f"{case_id}: event[{index}] has unknown type {event_type!r}")
    if event_type == "user":
        _required_str(data, "input", label=f"{case_id}: event[{index}]")
    else:
        _required_str(data, "content", label=f"{case_id}: event[{index}]")
        verdict = data.get("judge")
        if verdict is not None:
            if not isinstance(verdict, dict):
                raise TaskEvalFormatError(f"{case_id}: event[{index}].judge must be an object")
            _parse_status(str(verdict.get("status", "uncertain")), label=f"{case_id}: event[{index}].judge.status")
    return TaskEvalEvent(event_type=event_type, data=dict(data))


def _validate_expected(case_id: str, expected: Dict[str, Any]) -> None:
    if "status" in expected:
        _parse_status(str(expected["status"]), label=f"{case_id}.expected.status")
    if "last_verdict_status" in expected:
        _parse_status(str(expected["last_verdict_status"]), label=f"{case_id}.expected.last_verdict_status")
    for key in (
        "last_verdict_source_match",
        "reason_match",
        "assistant_final_reply_match",
    ):
        if key in expected:
            _compile_pattern(expected[key], label=f"{case_id}.expected.{key}")
    for key in (
        "recent_user_inputs",
        "missing",
        "judge_recent_user_inputs",
    ):
        if key in expected and not _all_strings(expected[key]):
            raise TaskEvalFormatError(f"{case_id}.expected.{key} must be an array of strings")
    if "judge_call_count" in expected and not _non_negative_int(expected["judge_call_count"]):
        raise TaskEvalFormatError(f"{case_id}.expected.judge_call_count must be a non-negative integer")


def run_case(case: TaskEvalCase) -> Dict[str, Any]:
    verdicts = [
        event.data["judge"]
        for event in case.events
        if event.event_type == "reply" and isinstance(event.data.get("judge"), dict)
    ]
    judge = QueueJudge(verdicts)
    controller = TaskController(completion_verifier=CompletionVerifier(judge))
    timeline: List[Dict[str, Any]] = []
    for event in case.events:
        data = event.data
        if event.event_type == "user":
            controller.observe_user_input(data["input"])
            timeline.append({
                "type": "user",
                "status": controller.state.status.value,
                "recent_user_inputs": list(controller.state.recent_user_inputs),
            })
        else:
            verdict = controller.verify_completion(data["content"])
            timeline.append({
                "type": "reply",
                "status": controller.state.status.value,
                "verdict": verdict.status.value,
            })
    return {
        "state": controller.state.to_dict(),
        "timeline": timeline,
        "judge_calls": judge.calls,
    }


def evaluate_case(case: TaskEvalCase) -> Dict[str, Any]:
    execution = run_case(case)
    state = TaskState.from_dict(execution["state"])
    failures: List[Dict[str, str]] = []

    def fail(code: str, message: str) -> None:
        failures.append({"code": code, "message": message})

    expected = case.expected
    if "status" in expected:
        wanted = _parse_status(str(expected["status"]), label=f"{case.case_id}.expected.status")
        if state.status is not wanted:
            fail("wrong_status", f"expected {wanted.value}, got {state.status.value}")
    if "recent_user_inputs" in expected and state.recent_user_inputs != expected["recent_user_inputs"]:
        fail("wrong_recent_user_inputs", f"expected {expected['recent_user_inputs']!r}, got {state.recent_user_inputs!r}")
    if "last_verdict_status" in expected:
        actual = state.last_verdict.status if state.last_verdict else None
        wanted = _parse_status(str(expected["last_verdict_status"]), label=f"{case.case_id}.expected.last_verdict_status")
        if actual is not wanted:
            fail("wrong_verdict_status", f"expected verdict {wanted.value}, got {actual.value if actual else None}")
    if "last_verdict_source_match" in expected:
        source = state.last_verdict.source if state.last_verdict else ""
        if not re.search(str(expected["last_verdict_source_match"]), source, re.I | re.M):
            fail("wrong_verdict_source", f"source {source!r} did not match")
    if "reason_match" in expected:
        reason = state.last_verdict.reason if state.last_verdict else ""
        if not re.search(str(expected["reason_match"]), reason, re.I | re.M):
            fail("wrong_reason", f"reason {reason!r} did not match")
    if "missing" in expected:
        missing = state.last_verdict.missing if state.last_verdict else []
        if missing != expected["missing"]:
            fail("wrong_missing", f"expected missing {expected['missing']!r}, got {missing!r}")
    if "judge_call_count" in expected and len(execution["judge_calls"]) != expected["judge_call_count"]:
        fail("wrong_judge_call_count", f"expected {expected['judge_call_count']}, got {len(execution['judge_calls'])}")
    if "judge_recent_user_inputs" in expected:
        actual = execution["judge_calls"][-1]["recent_user_inputs"] if execution["judge_calls"] else []
        if actual != expected["judge_recent_user_inputs"]:
            fail("wrong_judge_inputs", f"expected judge inputs {expected['judge_recent_user_inputs']!r}, got {actual!r}")
    if "assistant_final_reply_match" in expected:
        actual = execution["judge_calls"][-1]["assistant_final_reply"] if execution["judge_calls"] else ""
        if not re.search(str(expected["assistant_final_reply_match"]), actual, re.I | re.M):
            fail("wrong_judge_reply", f"assistant_final_reply {actual!r} did not match")

    return {
        "case_id": case.case_id,
        "title": case.title,
        "passed": not failures,
        "failures": failures,
        "state": execution["state"],
        "timeline": execution["timeline"],
        "judge_calls": execution["judge_calls"],
    }


def build_report(cases: Sequence[TaskEvalCase], results: Sequence[Dict[str, Any]], *, cases_path: Path) -> Dict[str, Any]:
    passed = sum(1 for result in results if result["passed"])
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metadata": {
            "cases_hash": _file_hash(cases_path),
            "git_commit": _git_commit(),
            "git_dirty": _git_dirty(),
            "method": "task_completion_judge_contract_replay",
        },
        "summary": {
            "case_count": len(cases),
            "passed_count": passed,
            "failed_count": len(cases) - passed,
        },
        "results": list(results),
    }


def render_markdown(report: Dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Task Eval Report",
        "",
        f"- Cases: {summary['case_count']}",
        f"- Passed: {summary['passed_count']}",
        f"- Failed: {summary['failed_count']}",
        "",
        "## Results",
        "",
    ]
    for result in report["results"]:
        mark = "PASS" if result["passed"] else "FAIL"
        lines.append(f"- {mark} `{result['case_id']}` - {result['title']}")
        for failure in result["failures"]:
            lines.append(f"  - {failure['code']}: {failure['message']}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        cases = load_cases(args.cases)
        if args.case:
            selected = set(args.case)
            unknown = sorted(selected - {case.case_id for case in cases})
            if unknown:
                raise TaskEvalFormatError(f"unknown case(s): {unknown}")
            cases = [case for case in cases if case.case_id in selected]
        results = [evaluate_case(case) for case in cases]
        report = build_report(cases, results, cases_path=args.cases)
        markdown = render_markdown(report)
        print(markdown)
        if args.json_report:
            args.json_report.parent.mkdir(parents=True, exist_ok=True)
            args.json_report.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        if args.markdown_report:
            args.markdown_report.parent.mkdir(parents=True, exist_ok=True)
            args.markdown_report.write_text(markdown + "\n", encoding="utf-8")
        return 1 if report["summary"]["failed_count"] else 0
    except (OSError, TaskEvalFormatError) as error:
        print(f"Task Eval failed: {error}", file=sys.stderr)
        return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Noval task-completion judge Eval")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--case", action="append")
    parser.add_argument("--json-report", type=Path)
    parser.add_argument("--markdown-report", type=Path)
    return parser


def _required_str(data: Dict[str, Any], key: str, *, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TaskEvalFormatError(f"{label}: {key} must be a non-empty string")
    return value


def _parse_status(value: str, *, label: str) -> TaskStatus:
    try:
        return TaskStatus(value)
    except ValueError as error:
        raise TaskEvalFormatError(f"{label}: unknown status {value!r}") from error


def _compile_pattern(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise TaskEvalFormatError(f"{label} must be a non-empty regex string")
    try:
        re.compile(value)
    except re.error as error:
        raise TaskEvalFormatError(f"{label}: invalid regex {value!r}: {error}") from error
    return value


def _all_strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, (str, int, float))]


def _non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _float_between_zero_one(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    return 0.0


def _file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


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


if __name__ == "__main__":
    raise SystemExit(main())
