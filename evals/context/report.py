"""Render context Eval reports from plain dictionaries without runtime dependencies."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def write_json_report(path: Path, report: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def render_markdown(report: Dict[str, Any]) -> str:
    summary = report["summary"]
    metadata = report["metadata"]
    lines = [
        "# Context Eval Report",
        "",
        f"- Cases: {summary['evaluated_count']}/{summary['case_count']}",
        f"- Passed: {summary['passed_count']}",
        f"- Hard failures: {summary['hard_failure_count']}",
        f"- Weighted score: {summary['weighted_score']}",
        f"- Model: {metadata.get('model') or 'offline candidate'}",
        f"- Prompt version: {metadata['prompt_version']}",
        f"- Prompt hash: {metadata.get('prompt_hash') or 'unknown'}",
        f"- Commit: {metadata.get('git_commit') or 'unknown'}",
        f"- Worktree: {'dirty' if metadata.get('git_dirty') else 'clean'}",
        "",
        "## Category Scores",
        "",
        "| Category | Weight | Passed | Score |",
        "|---|---:|---:|---:|",
    ]
    for category, detail in summary["category_scores"].items():
        lines.append(
            f"| {category} | {detail['weight']} | "
            f"{detail['passed']}/{detail['total']} | {detail['score']} |"
        )
    concision = summary["concision"]
    lines.extend([
        f"| concision | {concision['weight']} | "
        f"{concision['passed']}/{concision['total']} | "
        f"{round(concision['passed'] / max(1, concision['total']) * 100, 1)} |",
        "",
        "## Case Results",
        "",
        "| Case | Score | Result | Hard Failures |",
        "|---|---:|---|---|",
    ])
    for result in report["results"]:
        failures = "; ".join(item["code"] for item in result["hard_failures"]) or "—"
        status = "PASS" if result["passed"] else "FAIL"
        lines.append(
            f"| {result['case_id']} | {result['score']} | {status} | {failures} |"
        )
    failed = [result for result in report["results"] if not result["passed"]]
    if failed:
        lines.extend(["", "## Failure Details", ""])
        for result in failed:
            lines.append(f"### {result['case_id']} — {result['title']}")
            lines.append("")
            for failure in result["hard_failures"]:
                lines.append(f"- **{failure['code']}**: {failure['message']}")
            for assertion in result["assertions"]:
                if not assertion["passed"]:
                    lines.append(
                        f"- {assertion['kind']} / {assertion['category']}: "
                        f"{assertion['statement']}"
                    )
            lines.append("")
    return "\n".join(lines).rstrip()
