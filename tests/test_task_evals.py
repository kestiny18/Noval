import json

from evals.task.run import (
    DEFAULT_CASES_PATH,
    TaskEvalFormatError,
    evaluate_case,
    load_cases,
    main,
)


def test_bundled_task_eval_assets_are_valid():
    cases = load_cases(DEFAULT_CASES_PATH)

    assert len(cases) == 8
    assert len({case.case_id for case in cases}) == 8
    assert any(case.expected.get("status") == "violated" for case in cases)
    assert any(case.expected.get("action_mode") == "read_only" for case in cases)


def test_default_task_eval_runs_offline(capsys):
    assert main([]) == 0

    out = capsys.readouterr().out
    assert "# Task Eval Report" in out
    assert "Passed: 8" in out
    assert "Failed: 0" in out


def test_task_eval_detects_wrong_expected_status(tmp_path):
    case = {
        "id": "wrong",
        "title": "wrong",
        "events": [
            {"type": "user", "input": "只查询原因"},
            {"type": "tool", "name": "write_file", "risk": "write", "arguments": {}},
        ],
        "expected": {"status": "completed"},
    }
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")

    assert main(["--cases", str(path)]) == 1


def test_task_eval_rejects_invalid_case(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps({
        "id": "bad",
        "title": "bad",
        "events": [{"type": "tool", "name": "x", "risk": "unknown"}],
        "expected": {"status": "active"},
    }) + "\n", encoding="utf-8")

    try:
        load_cases(path)
    except TaskEvalFormatError as error:
        assert "unknown risk" in str(error)
    else:
        raise AssertionError("expected TaskEvalFormatError")


def test_task_eval_preserves_ack_objective():
    case = next(
        item for item in load_cases(DEFAULT_CASES_PATH)
        if item.case_id == "ack_does_not_replace_objective"
    )

    result = evaluate_case(case)

    assert result["passed"] is True
    assert result["state"]["spec"]["objective"] == "排查重复订单数据"
    assert result["state"]["spec"]["revision"] == 1
