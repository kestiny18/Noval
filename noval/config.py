"""Configuration loading.

Built-in defaults are overridden by ``~/.noval/settings.json``. Missing files
fall back to defaults. API keys are resolved at runtime rather than embedded in
the repository.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

# Default configuration; settings.json may override any field.
DEFAULTS: Dict[str, Any] = {
    "provider": "openai-compatible",
    "model": "deepseek-v4-pro",
    "judge_model": "deepseek-v4-flash",
    "base_url": "https://api.deepseek.com",
    "api_key_env": "DEEPSEEK_API_KEY",        # Environment variable containing the key.
    "max_steps": 40,                          # Maximum tool-loop steps per user turn.
    "max_tool_output_chars": 8000,            # Truncate tool output beyond this length.
    "persist_sessions": True,                 # Persist sessions by default.
    "sessions_dir": "",                       # Empty means ~/.noval/sessions.
    "persist_logs": True,                     # Persist redacted runtime logs by default.
    "logs_dir": "",                           # Empty means ~/.noval/logs.
    "log_retention_days": 14,                 # Delete expired daily log directories.
    "persist_usage": True,                    # Persist Provider-reported token usage.
    "usage_dir": "",                         # Empty means ~/.noval/usage.
    "context_budget_tokens": 256000,          # Active-context working budget.
    "request_timeout_seconds": 120,           # Prevent Provider requests from hanging the loop.
    "request_max_retries": 2,                 # Provider retries; zero disables retries.
    "anthropic_base_url": "",                # Empty uses the Anthropic SDK default.
    "anthropic_max_tokens": 8192,
}
# The system prompt is agent behavior defined in code, not a stable user
# preference, so settings.json cannot override DEFAULT_SYSTEM_PROMPT.


def settings_path() -> Path:
    return Path.home() / ".noval" / "settings.json"


def default_sessions_dir() -> Path:
    return Path.home() / ".noval" / "sessions"


def default_logs_dir() -> Path:
    return Path.home() / ".noval" / "logs"


def default_usage_dir() -> Path:
    return Path.home() / ".noval" / "usage"


@dataclass
class Config:
    model: str
    base_url: str
    api_key_env: str
    max_steps: int
    max_tool_output_chars: int
    api_key: str = ""          # Optional plaintext key in the user-local settings file.
    persist_sessions: bool = True
    sessions_dir_setting: str = ""
    persist_logs: bool = True
    logs_dir_setting: str = ""
    log_retention_days: int = 14
    persist_usage: bool = True
    usage_dir_setting: str = ""
    context_budget_tokens: int = 256000
    request_timeout_seconds: float = 120.0
    request_max_retries: int = 2
    judge_model: str = "deepseek-v4-flash"
    provider: str = "openai-compatible"
    anthropic_base_url: str = ""
    anthropic_max_tokens: int = 8192
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        merged = dict(DEFAULTS)
        p = path or settings_path()
        if p.exists():
            try:
                user = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise SystemExit(f"settings.json is not valid JSON: {e}")
            merged.update(user)  # A shallow merge is sufficient for the flat schema.

        for key in ("provider", "model", "judge_model", "base_url", "api_key_env"):
            if not isinstance(merged[key], str) or not merged[key].strip():
                raise SystemExit(f"settings.json: {key} must be a non-empty string")
        if merged["provider"] not in {"openai-compatible", "anthropic"}:
            raise SystemExit(
                "settings.json: provider must be openai-compatible or anthropic"
            )
        if not isinstance(merged["anthropic_base_url"], str):
            raise SystemExit("settings.json: anthropic_base_url must be a string")

        # Invalid configuration must fail clearly instead of silently drifting.
        if not isinstance(merged["persist_sessions"], bool):
            raise SystemExit("settings.json: persist_sessions must be true or false")
        if not isinstance(merged["sessions_dir"], str):
            raise SystemExit('settings.json: sessions_dir must be a path string such as "D:/noval-sessions"')
        if not isinstance(merged["persist_logs"], bool):
            raise SystemExit("settings.json: persist_logs must be true or false")
        if not isinstance(merged["logs_dir"], str):
            raise SystemExit('settings.json: logs_dir must be a path string such as "D:/noval-logs"')
        if not isinstance(merged["persist_usage"], bool):
            raise SystemExit("settings.json: persist_usage must be true or false")
        if not isinstance(merged["usage_dir"], str):
            raise SystemExit('settings.json: usage_dir must be a path string such as "D:/noval-usage"')
        for key in (
            "max_steps", "max_tool_output_chars", "log_retention_days", "context_budget_tokens",
            "anthropic_max_tokens",
        ):
            try:
                merged[key] = int(merged[key])
            except (TypeError, ValueError):
                raise SystemExit(f"settings.json: {key} must be an integer")
        if merged["log_retention_days"] < 1:
            raise SystemExit("settings.json: log_retention_days must be at least 1")
        if merged["context_budget_tokens"] < 1000:
            raise SystemExit("settings.json: context_budget_tokens must be at least 1000")
        if merged["anthropic_max_tokens"] < 1:
            raise SystemExit("settings.json: anthropic_max_tokens must be at least 1")
        try:
            merged["request_timeout_seconds"] = float(merged["request_timeout_seconds"])
        except (TypeError, ValueError):
            raise SystemExit("settings.json: request_timeout_seconds must be a number")
        if merged["request_timeout_seconds"] <= 0:
            raise SystemExit("settings.json: request_timeout_seconds must be greater than 0")
        try:
            merged["request_max_retries"] = int(merged["request_max_retries"])
        except (TypeError, ValueError):
            raise SystemExit("settings.json: request_max_retries must be an integer")
        if merged["request_max_retries"] < 0:
            raise SystemExit("settings.json: request_max_retries must be at least 0")

        return cls(
            model=merged["model"],
            judge_model=merged["judge_model"],
            base_url=merged["base_url"],
            api_key_env=merged["api_key_env"],
            max_steps=merged["max_steps"],
            max_tool_output_chars=merged["max_tool_output_chars"],
            api_key=merged.get("api_key", ""),
            persist_sessions=merged["persist_sessions"],
            sessions_dir_setting=merged["sessions_dir"],
            persist_logs=merged["persist_logs"],
            logs_dir_setting=merged["logs_dir"],
            log_retention_days=merged["log_retention_days"],
            persist_usage=merged["persist_usage"],
            usage_dir_setting=merged["usage_dir"],
            context_budget_tokens=merged["context_budget_tokens"],
            request_timeout_seconds=merged["request_timeout_seconds"],
            request_max_retries=merged["request_max_retries"],
            provider=merged["provider"],
            anthropic_base_url=merged["anthropic_base_url"],
            anthropic_max_tokens=merged["anthropic_max_tokens"],
            raw=merged,
        )

    def sessions_dir(self) -> Path:
        """Return the session root, defaulting outside the project repository."""
        if not self.sessions_dir_setting.strip():
            return default_sessions_dir()
        return Path(self.sessions_dir_setting).expanduser()

    def logs_dir(self) -> Path:
        """Return the runtime-log root, defaulting outside the project repository."""
        if not self.logs_dir_setting.strip():
            return default_logs_dir()
        return Path(self.logs_dir_setting).expanduser()

    def usage_dir(self) -> Path:
        """Return the user-level token-usage root shared across projects."""
        if not self.usage_dir_setting.strip():
            return default_usage_dir()
        return Path(self.usage_dir_setting).expanduser()

    def resolve_api_key(self) -> str:
        """Resolve an API key from settings.json, then the configured environment variable.

        The user-local settings file is outside the repository, but a key stored
        there is still plaintext and must never be copied into project files.
        """
        if self.api_key:
            return self.api_key
        key = os.environ.get(self.api_key_env)
        if key:
            return key
        raise SystemExit(
            "API key not found. Choose one of these options:\n"
            f"  1) Add \"api_key\": \"sk-...\" to {settings_path()}\n"
            f"  2) Set environment variable {self.api_key_env} "
            f"(PowerShell: $env:{self.api_key_env}=\"sk-...\")"
        )

    def api_key_configured(self) -> bool:
        """Report credential availability without exposing credential content."""
        return bool(self.api_key or os.environ.get(self.api_key_env))
