import logging
from datetime import date, datetime

from noval.config import Config
from noval.runtime_log import (
    cleanup_old_logs, redact_text, runtime_log_context, setup_runtime_logging,
)


def _config(logs_dir, **overrides):
    values = dict(
        model="m", base_url="u", api_key_env="K", max_steps=5,
        max_tool_output_chars=8000,
        logs_dir_setting=str(logs_dir),
    )
    values.update(overrides)
    return Config(**values)


def test_runtime_log_is_daily_and_redacted(tmp_path):
    now = datetime(2026, 6, 29, 12, 30, 0)
    path = setup_runtime_logging(_config(tmp_path), "session-1", now=now)
    assert path is not None
    assert path.parent.name == "2026-06-29"
    assert path.name.startswith("noval-session-1-")

    logging.getLogger("noval.test").info(
        "authorization=Bearer-secret token=my-token sk-abcdefgh12345678"
    )
    for handler in logging.getLogger().handlers:
        handler.flush()

    content = path.read_text(encoding="utf-8")
    assert "Bearer-secret" not in content
    assert "my-token" not in content
    assert "sk-abcdefgh12345678" not in content
    assert content.count("<redacted>") == 3


def test_redact_text_covers_bearer_header():
    assert redact_text("Authorization: Bearer abc.def") == "Authorization=<redacted> <redacted>"


def test_runtime_log_omits_exception_value(tmp_path):
    path = setup_runtime_logging(
        _config(tmp_path), "session-2", now=datetime(2026, 6, 29, 13, 0, 0)
    )
    try:
        raise RuntimeError("private tool argument")
    except RuntimeError:
        logging.getLogger("noval.test").exception("operation failed")
    for handler in logging.getLogger().handlers:
        handler.flush()

    content = path.read_text(encoding="utf-8")
    assert "private tool argument" not in content
    assert "RuntimeError: <details redacted>" in content


def test_runtime_log_carries_session_turn_and_request_context(tmp_path):
    path = setup_runtime_logging(
        _config(tmp_path), now=datetime(2026, 6, 29, 14, 0, 0)
    )
    with runtime_log_context(
        session_id="session-a",
        turn_id="turn-b",
        request_id="request-c",
    ):
        logging.getLogger("noval.test").info("correlated")
    for handler in logging.getLogger().handlers:
        handler.flush()

    content = path.read_text(encoding="utf-8")
    assert "session=session-a" in content
    assert "turn=turn-b" in content
    assert "request=request-c" in content


def test_cleanup_old_logs_only_removes_expired_date_dirs(tmp_path):
    old = tmp_path / "2026-06-01"
    recent = tmp_path / "2026-06-20"
    unrelated = tmp_path / "custom"
    for directory in (old, recent, unrelated):
        directory.mkdir()

    cleanup_old_logs(tmp_path, retention_days=14, today=date(2026, 6, 29))

    assert not old.exists()
    assert recent.exists()
    assert unrelated.exists()
