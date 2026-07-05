"""context Eval 报告输出。只消费普通 dict，不依赖 Noval 运行时。"""
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
        f"- 用例：{summary['evaluated_count']}/{summary['case_count']}",
        f"- 通过：{summary['passed_count']}",
        f"- 硬失败：{summary['hard_failure_count']}",
        f"- 加权分：{summary['weighted_score']}",
        f"- 模型：{metadata.get('model') or '离线候选'}",
        f"- Prompt version：{metadata['prompt_version']}",
        f"- Prompt hash：{metadata.get('prompt_hash') or 'unknown'}",
        f"- Commit：{metadata.get('git_commit') or 'unknown'}",
        f"- 工作树：{'dirty' if metadata.get('git_dirty') else 'clean'}",
        "",
        "## 分项得分",
        "",
        "| 维度 | 权重 | 通过 | 得分 |",
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
        "## 用例结果",
        "",
        "| 用例 | 分数 | 结果 | 硬失败 |",
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
        lines.extend(["", "## 失败详情", ""])
        for result in failed:
            lines.append(f"### {result['case_id']} — {result['title']}")
            lines.append("")
            for failure in result["hard_failures"]:
                lines.append(f"- **{failure['code']}**：{failure['message']}")
            for assertion in result["assertions"]:
                if not assertion["passed"]:
                    lines.append(
                        f"- {assertion['kind']} / {assertion['category']}："
                        f"{assertion['statement']}"
                    )
            lines.append("")
    return "\n".join(lines).rstrip()
