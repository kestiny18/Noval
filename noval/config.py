"""Stable Runtime preferences backed by settings schema v2."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from .model_config import (
    BUILTIN_PROFILE_BY_ID,
    ModelConfiguration,
    ModelConfigurationError,
    load_settings_document,
    parse_model_configuration,
)

# The system prompt is agent behavior defined in code, not a stable user
# preference, so settings.json cannot override DEFAULT_SYSTEM_PROMPT.


class ConfigurationError(SystemExit):
    """Typed startup failure that remains compatible with CLI SystemExit."""

    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.error_code = code
        self.safe_message = safe_message


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
    api_key: str = field(default="", repr=False)
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
    model_configuration: Optional[ModelConfiguration] = field(
        default=None, repr=False
    )
    settings_path_setting: str = ""
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        p = path or settings_path()
        try:
            merged = load_settings_document(p)
            model_configuration = parse_model_configuration(merged["models"])
        except ModelConfigurationError as exc:
            raise ConfigurationError(exc.code, str(exc)) from None
        configured = model_configuration.configured_model(
            model_configuration.default_model_id
        )
        connection = model_configuration.connection(configured.connection_id)
        profile_models = {
            model.model: model
            for model in model_configuration.configured
            if model.connection_id == connection.id
        }
        judge_model = configured.model
        profile = BUILTIN_PROFILE_BY_ID.get(connection.profile_id)
        if profile is not None and profile.judge_model in profile_models:
            judge_model = profile.judge_model

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
            model=configured.model,
            judge_model=judge_model,
            base_url=connection.base_url,
            api_key_env=connection.api_key_env,
            max_steps=merged["max_steps"],
            max_tool_output_chars=merged["max_tool_output_chars"],
            api_key=connection.api_key,
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
            provider=connection.adapter,
            anthropic_base_url="",
            anthropic_max_tokens=merged["anthropic_max_tokens"],
            model_configuration=model_configuration,
            settings_path_setting=str(p),
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
        """Resolve the default Connection credential without exposing it."""
        if self.api_key:
            return self.api_key
        key = os.environ.get(self.api_key_env)
        if key:
            return key
        connection_id = "the default Connection"
        if self.model_configuration is not None:
            configured = self.model_configuration.configured_model(
                self.model_configuration.default_model_id
            )
            connection_id = repr(configured.connection_id)
        raise SystemExit(
            f"API key not found for {connection_id}. Update the Connection "
            "credential through the Noval configuration API or set environment "
            f"variable {self.api_key_env}."
        )

    def api_key_configured(self) -> bool:
        """Report credential availability without exposing credential content."""
        return bool(self.api_key or os.environ.get(self.api_key_env))
