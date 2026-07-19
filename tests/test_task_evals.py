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

    assert len(cases) == 7
    assert len({case.case_id for case in cases}) == 7
    assert any(case.expected.get("status") == "completed" for case in cases)
    assert any(case.expected.get("status") == "uncertain" for case in cases)


def test_default_task_eval_runs_offline(capsys):
    assert main([]) == 0

    out = capsys.readouterr().out
    assert "# Task Eval Report" in out
    assert "Passed: 7" in out
    assert "Failed: 0" in out


def test_task_eval_detects_wrong_expected_status(tmp_path):
    case = {
        "id": "wrong",
        "title": "wrong",
        "events": [
            {"type": "user", "input": "Explain the cause of the error"},
            {
                "type": "reply",
                "content": "The investigation is incomplete.",
                "judge": {"status": "incomplete", "reason": "not done"},
            },
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
        "events": [{"type": "tool", "name": "x"}],
        "expected": {"status": "active"},
    }) + "\n", encoding="utf-8")

    try:
        load_cases(path)
    except TaskEvalFormatError as error:
        assert "unknown type" in str(error)
    else:
        raise AssertionError("expected TaskEvalFormatError")


def test_task_eval_tracks_recent_user_inputs():
    case = next(
        item for item in load_cases(DEFAULT_CASES_PATH)
        if item.case_id == "recent_inputs_keep_last_three_unique"
    )

    result = evaluate_case(case)

    assert result["passed"] is True
    assert result["state"]["recent_user_inputs"] == ["Goal A", "Goal C", "Goal D"]
