import json

import pytest

from noval.config import Config


def test_persistence_config_defaults(tmp_path):
    cfg = Config.load(tmp_path / "missing.json")
    assert cfg.persist_sessions is True
    assert cfg.sessions_dir().name == "sessions"
    assert cfg.persist_logs is True
    assert cfg.logs_dir().name == "logs"
    assert cfg.log_retention_days == 14
    assert cfg.persist_usage is True
    assert cfg.usage_dir().name == "usage"
    assert cfg.context_budget_tokens == 256000
    assert cfg.judge_model == "deepseek-v4-flash"
    assert cfg.request_timeout_seconds == 120.0
    assert cfg.request_max_retries == 2


def test_persistence_config_can_be_overridden(tmp_path):
    settings = tmp_path / "settings.json"
    sessions = tmp_path / "my-sessions"
    logs = tmp_path / "my-logs"
    usage = tmp_path / "my-usage"
    settings.write_text(json.dumps({
        "persist_sessions": False,
        "sessions_dir": str(sessions),
        "persist_logs": False,
        "logs_dir": str(logs),
        "log_retention_days": 30,
        "persist_usage": False,
        "usage_dir": str(usage),
        "context_budget_tokens": 512000,
        "judge_model": "judge-x",
        "request_timeout_seconds": 45.5,
        "request_max_retries": 0,
    }), encoding="utf-8")

    cfg = Config.load(settings)

    assert cfg.persist_sessions is False
    assert cfg.sessions_dir() == sessions
    assert cfg.persist_logs is False
    assert cfg.logs_dir() == logs
    assert cfg.log_retention_days == 30
    assert cfg.persist_usage is False
    assert cfg.usage_dir() == usage
    assert cfg.context_budget_tokens == 512000
    assert cfg.judge_model == "judge-x"
    assert cfg.request_timeout_seconds == 45.5
    assert cfg.request_max_retries == 0


def test_persistence_config_rejects_bad_types(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"persist_sessions": "yes"}), encoding="utf-8")

    with pytest.raises(SystemExit, match="persist_sessions"):
        Config.load(settings)

    settings.write_text(json.dumps({"sessions_dir": ["bad"]}), encoding="utf-8")
    with pytest.raises(SystemExit, match="sessions_dir"):
        Config.load(settings)

    settings.write_text(json.dumps({"persist_logs": "yes"}), encoding="utf-8")
    with pytest.raises(SystemExit, match="persist_logs"):
        Config.load(settings)

    settings.write_text(json.dumps({"log_retention_days": 0}), encoding="utf-8")
    with pytest.raises(SystemExit, match="log_retention_days"):
        Config.load(settings)

    settings.write_text(json.dumps({"persist_usage": "yes"}), encoding="utf-8")
    with pytest.raises(SystemExit, match="persist_usage"):
        Config.load(settings)

    settings.write_text(json.dumps({"usage_dir": ["bad"]}), encoding="utf-8")
    with pytest.raises(SystemExit, match="usage_dir"):
        Config.load(settings)

    settings.write_text(json.dumps({"context_budget_tokens": "many"}), encoding="utf-8")
    with pytest.raises(SystemExit, match="context_budget_tokens"):
        Config.load(settings)

    settings.write_text(json.dumps({"context_budget_tokens": 999}), encoding="utf-8")
    with pytest.raises(SystemExit, match="context_budget_tokens"):
        Config.load(settings)

    settings.write_text(json.dumps({"judge_model": ""}), encoding="utf-8")
    with pytest.raises(SystemExit, match="judge_model"):
        Config.load(settings)

    settings.write_text(json.dumps({"request_timeout_seconds": "slow"}), encoding="utf-8")
    with pytest.raises(SystemExit, match="request_timeout_seconds"):
        Config.load(settings)

    settings.write_text(json.dumps({"request_timeout_seconds": 0}), encoding="utf-8")
    with pytest.raises(SystemExit, match="request_timeout_seconds"):
        Config.load(settings)

    settings.write_text(json.dumps({"request_max_retries": "many"}), encoding="utf-8")
    with pytest.raises(SystemExit, match="request_max_retries"):
        Config.load(settings)

    settings.write_text(json.dumps({"request_max_retries": -1}), encoding="utf-8")
    with pytest.raises(SystemExit, match="request_max_retries"):
        Config.load(settings)


def test_removed_auto_approve_setting_is_silently_ignored(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"auto_approve": "legacy-value"}), encoding="utf-8")

    cfg = Config.load(settings)

    assert not hasattr(cfg, "auto_approve")
