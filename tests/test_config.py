import json

import pytest

from noval.config import Config


def test_persistence_config_defaults(tmp_path):
    cfg = Config.load(tmp_path / "missing.json")
    assert cfg.persist_sessions is True
    assert cfg.sessions_dir().name == "sessions"


def test_persistence_config_can_be_overridden(tmp_path):
    settings = tmp_path / "settings.json"
    sessions = tmp_path / "my-sessions"
    settings.write_text(json.dumps({
        "persist_sessions": False,
        "sessions_dir": str(sessions),
    }), encoding="utf-8")

    cfg = Config.load(settings)

    assert cfg.persist_sessions is False
    assert cfg.sessions_dir() == sessions


def test_persistence_config_rejects_bad_types(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"persist_sessions": "yes"}), encoding="utf-8")

    with pytest.raises(SystemExit, match="persist_sessions"):
        Config.load(settings)

    settings.write_text(json.dumps({"sessions_dir": ["bad"]}), encoding="utf-8")
    with pytest.raises(SystemExit, match="sessions_dir"):
        Config.load(settings)
