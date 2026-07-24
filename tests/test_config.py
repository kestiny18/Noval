import json

import pytest

from noval.config import Config, ConfigurationError
from noval.model_config import packaged_settings


def write_settings(path, **overrides):
    document = packaged_settings()
    document.update(overrides)
    path.write_text(json.dumps(document), encoding="utf-8")


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
    assert cfg.model == "deepseek-v4-pro"
    assert cfg.judge_model == "deepseek-v4-flash"
    assert cfg.request_timeout_seconds == 120.0
    assert cfg.request_max_retries == 2
    assert cfg.provider == "openai-compatible"
    assert cfg.base_url == "https://api.deepseek.com"
    assert cfg.api_key_env == "DEEPSEEK_API_KEY"
    assert cfg.anthropic_base_url == ""
    assert cfg.anthropic_max_tokens == 8192
    assert cfg.model_configuration is not None


def test_persistence_config_can_be_overridden(tmp_path):
    settings = tmp_path / "settings.json"
    sessions = tmp_path / "my-sessions"
    logs = tmp_path / "my-logs"
    usage = tmp_path / "my-usage"
    write_settings(
        settings,
        persist_sessions=False,
        sessions_dir=str(sessions),
        persist_logs=False,
        logs_dir=str(logs),
        log_retention_days=30,
        persist_usage=False,
        usage_dir=str(usage),
        context_budget_tokens=512000,
        request_timeout_seconds=45.5,
        request_max_retries=0,
        anthropic_max_tokens=4096,
    )

    cfg = Config.load(settings)

    assert cfg.persist_sessions is False
    assert cfg.sessions_dir() == sessions
    assert cfg.persist_logs is False
    assert cfg.logs_dir() == logs
    assert cfg.log_retention_days == 30
    assert cfg.persist_usage is False
    assert cfg.usage_dir() == usage
    assert cfg.context_budget_tokens == 512000
    assert cfg.request_timeout_seconds == 45.5
    assert cfg.request_max_retries == 0
    assert cfg.anthropic_max_tokens == 4096


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"persist_sessions": "yes"}, "persist_sessions"),
        ({"sessions_dir": ["bad"]}, "sessions_dir"),
        ({"persist_logs": "yes"}, "persist_logs"),
        ({"logs_dir": ["bad"]}, "logs_dir"),
        ({"log_retention_days": 0}, "log_retention_days"),
        ({"persist_usage": "yes"}, "persist_usage"),
        ({"usage_dir": ["bad"]}, "usage_dir"),
        ({"context_budget_tokens": "many"}, "context_budget_tokens"),
        ({"context_budget_tokens": 999}, "context_budget_tokens"),
        ({"request_timeout_seconds": "slow"}, "request_timeout_seconds"),
        ({"request_timeout_seconds": 0}, "request_timeout_seconds"),
        ({"request_max_retries": "many"}, "request_max_retries"),
        ({"request_max_retries": -1}, "request_max_retries"),
        ({"anthropic_max_tokens": 0}, "anthropic_max_tokens"),
    ],
)
def test_persistence_config_rejects_bad_types(tmp_path, override, field):
    settings = tmp_path / "settings.json"
    write_settings(settings, **override)

    with pytest.raises(SystemExit, match=field):
        Config.load(settings)


def test_config_rejects_legacy_flat_settings_without_rewriting(tmp_path):
    settings = tmp_path / "settings.json"
    original = json.dumps(
        {
            "provider": "openai-compatible",
            "model": "legacy-model",
            "base_url": "https://example.test",
        }
    )
    settings.write_text(original, encoding="utf-8")

    with pytest.raises(
        ConfigurationError, match="unsupported_settings_schema"
    ) as unsupported:
        Config.load(settings)

    assert unsupported.value.error_code == "unsupported_settings_schema"
    assert settings.read_text(encoding="utf-8") == original


def test_config_rejects_removed_flat_fields_even_with_schema_v2(tmp_path):
    settings = tmp_path / "settings.json"
    write_settings(settings, model="legacy-model")

    with pytest.raises(SystemExit, match="legacy_settings_fields"):
        Config.load(settings)


def test_config_repr_and_missing_credential_error_do_not_expose_secrets(
    tmp_path, monkeypatch
):
    settings = tmp_path / "settings.json"
    document = packaged_settings()
    document["models"]["connections"][0]["api_key"] = "stored-secret-value"
    write_settings(settings, models=document["models"])

    cfg = Config.load(settings)

    assert "stored-secret-value" not in repr(cfg)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    cfg.api_key = ""
    with pytest.raises(SystemExit) as raised:
        cfg.resolve_api_key()
    assert "stored-secret-value" not in str(raised.value)
    assert "model-deepseek" not in str(raised.value)


def test_removed_auto_approve_setting_is_silently_ignored(tmp_path):
    settings = tmp_path / "settings.json"
    write_settings(settings, auto_approve="legacy-value")

    cfg = Config.load(settings)

    assert not hasattr(cfg, "auto_approve")
