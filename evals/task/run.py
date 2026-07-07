"""Run deterministic task-state Eval cases.

The default command is offline and zero-cost: it replays small task scenarios
through ``noval.task`` and checks the resulting structured state.
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
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from noval.task import ActionMode, TaskController, TaskState, TaskStatus
from noval.tools import Risk, Tool, ToolResult


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
    if event_type not in {"user", "tool", "reply"}:
        raise TaskEvalFormatError(f"{case_id}: event[{index}] has unknown type {event_type!r}")
    if event_type == "user":
        _required_str(data, "input", label=f"{case_id}: event[{index}]")
    elif event_type == "reply":
        _required_str(data, "content", label=f"{case_id}: event[{index}]")
    else:
        _required_str(data, "name", label=f"{case_id}: event[{index}]")
        risk = _required_str(data, "risk", label=f"{case_id}: event[{index}]")
        _parse_risk(risk, label=f"{case_id}: event[{index}]")
        if "arguments" in data and not isinstance(data["arguments"], dict):
            raise TaskEvalFormatError(f"{case_id}: event[{index}].arguments must be an object")
        result = data.get("result", {})
        if result is not None and not isinstance(result, dict):
            raise TaskEvalFormatError(f"{case_id}: event[{index}].result must be an object")
    return TaskEvalEvent(event_type=event_type, data=dict(data))


def _validate_expected(case_id: str, expected: Dict[str, Any]) -> None:
    if "status" in expected:
        _parse_status(str(expected["status"]), label=f"{case_id}.expected.status")
    if "action_mode" in expected:
        _parse_action_mode(str(expected["action_mode"]), label=f"{case_id}.expected.action_mode")
    if "last_verdict_status" in expected:
        _parse_status(str(expected["last_verdict_status"]), label=f"{case_id}.expected.last_verdict_status")
    for key in (
        "objective_match",
        "last_verdict_source_match",
    ):
        if key in expected:
            _compile_pattern(expected[key], label=f"{case_id}.expected.{key}")
    for key in (
        "violations_match",
        "forbidden_violations_match",
        "evidence_match",
        "blockers_match",
        "remaining_match",
    ):
        if key in expected:
            _pattern_list(expected[key], label=f"{case_id}.expected.{key}")
    if "revision" in expected and not _non_negative_int(expected["revision"]):
        raise TaskEvalFormatError(f"{case_id}.expected.revision must be a non-negative integer")
    if "min_evidence" in expected and not _non_negative_int(expected["min_evidence"]):
        raise TaskEvalFormatError(f"{case_id}.expected.min_evidence must be a non-negative integer")


def run_case(case: TaskEvalCase) -> Dict[str, Any]:
    controller = TaskController()
    timeline: List[Dict[str, Any]] = []
    for event in case.events:
        data = event.data
        if event.event_type == "user":
            controller.observe_user_input(data["input"])
            timeline.append({"type": "user", "status": controller.state.status.value})
        elif event.event_type == "tool":
            risk = _parse_risk(data["risk"], label=f"{case.case_id}.tool.risk")
            tool = _fake_tool(data["name"], risk)
            arguments = dict(data.get("arguments") or {})
            violation = controller.guard_action(tool, arguments, risk)
            timeline.append({
                "type": "tool",
                "name": tool.name,
                "risk": risk.value,
                "blocked": violation is not None,
            })
            if violation is None:
                result_data = data.get("result") or {}
                result = ToolResult(
                    content=str(result_data.get("content", "")),
                    is_error=bool(result_data.get("is_error", False)),
                    truncated=bool(result_data.get("truncated", False)),
                    meta={"tool": tool.name, "effective_risk": risk.value},
                )
                controller.observe_tool_result(
                    tool_name=tool.name,
                    raw_arguments=json.dumps(arguments, ensure_ascii=False),
                    result=result,
                )
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
    if "action_mode" in expected:
        actual = state.spec.action_mode if state.spec else ActionMode.UNSPECIFIED
        wanted = _parse_action_mode(str(expected["action_mode"]), label=f"{case.case_id}.expected.action_mode")
        if actual is not wanted:
            fail("wrong_action_mode", f"expected {wanted.value}, got {actual.value}")
    if "objective_match" in expected:
        objective = state.spec.objective if state.spec else ""
        if not re.search(str(expected["objective_match"]), objective, re.I | re.M):
            fail("objective_mismatch", f"objective {objective!r} did not match")
    if "revision" in expected:
        revision = state.spec.revision if state.spec else 0
        if revision != expected["revision"]:
            fail("wrong_revision", f"expected revision {expected['revision']}, got {revision}")
    if "min_evidence" in expected and len(state.evidence) < expected["min_evidence"]:
        fail("missing_evidence", f"expected at least {expected['min_evidence']} evidence item(s)")
    if "last_verdict_status" in expected:
        actual = state.last_verdict.status if state.last_verdict else None
        wanted = _parse_status(str(expected["last_verdict_status"]), label=f"{case.case_id}.expected.last_verdict_status")
        if actual is not wanted:
            fail("wrong_verdict_status", f"expected verdict {wanted.value}, got {actual.value if actual else None}")
    if "last_verdict_source_match" in expected:
        source = state.last_verdict.source if state.last_verdict else ""
        if not re.search(str(expected["last_verdict_source_match"]), source, re.I | re.M):
            fail("wrong_verdict_source", f"source {source!r} did not match")

    _check_patterns("violations_match", state.violations, expected, fail, required=True)
    _check_patterns("forbidden_violations_match", state.violations, expected, fail, required=False)
    evidence_text = [
        item.summary + "\n" + json.dumps(item.meta, ensure_ascii=False, sort_keys=True)
        for item in state.evidence
    ]
    _check_patterns("evidence_match", evidence_text, expected, fail, required=True)
    _check_patterns("blockers_match", state.blockers, expected, fail, required=True)
    _check_patterns("remaining_match", state.remaining, expected, fail, required=True)

    return {
        "case_id": case.case_id,
        "title": case.title,
        "passed": not failures,
        "failures": failures,
        "state": execution["state"],
        "timeline": execution["timeline"],
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
            "method": "deterministic_task_state_replay",
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
        lines.append(f"- {mark} `{result['case_id']}` — {result['title']}")
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
    parser = argparse.ArgumentParser(description="Noval task-state Eval")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--case", action="append")
    parser.add_argument("--json-report", type=Path)
    parser.add_argument("--markdown-report", type=Path)
    return parser


def _fake_tool(name: str, risk: Risk) -> Tool:
    def _noop() -> str:
        return ""

    return Tool(
        name=name,
        description="task eval synthetic tool",
        parameters={"type": "object", "properties": {}, "required": []},
        func=_noop,
        risk=risk,
    )


def _check_patterns(
    key: str,
    values: Sequence[str],
    expected: Dict[str, Any],
    fail: Any,
    *,
    required: bool,
) -> None:
    if key not in expected:
        return
    patterns = _pattern_list(expected[key], label=key)
    haystack = "\n".join(values)
    for pattern in patterns:
        matched = re.search(pattern, haystack, re.I | re.M) is not None
        if required and not matched:
            fail(f"{key}_missing", f"{pattern!r} did not match")
        if not required and matched:
            fail(f"{key}_present", f"{pattern!r} unexpectedly matched")


def _required_str(data: Dict[str, Any], key: str, *, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TaskEvalFormatError(f"{label}: {key} must be a non-empty string")
    return value


def _parse_risk(value: str, *, label: str) -> Risk:
    try:
        return Risk(value)
    except ValueError as error:
        raise TaskEvalFormatError(f"{label}: unknown risk {value!r}") from error


def _parse_status(value: str, *, label: str) -> TaskStatus:
    try:
        return TaskStatus(value)
    except ValueError as error:
        raise TaskEvalFormatError(f"{label}: unknown status {value!r}") from error


def _parse_action_mode(value: str, *, label: str) -> ActionMode:
    try:
        return ActionMode(value)
    except ValueError as error:
        raise TaskEvalFormatError(f"{label}: unknown action_mode {value!r}") from error


def _compile_pattern(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise TaskEvalFormatError(f"{label} must be a non-empty regex string")
    try:
        re.compile(value)
    except re.error as error:
        raise TaskEvalFormatError(f"{label}: invalid regex {value!r}: {error}") from error
    return value


def _pattern_list(value: Any, *, label: str) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise TaskEvalFormatError(f"{label} must be an array of non-empty regex strings")
    return [_compile_pattern(item, label=f"{label}[]") for item in value]


def _non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


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
