"""模型 token 用量的旁路计量与按日汇总。"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Protocol, Sequence
from uuid import uuid4

from .client import LLMClient, LLMResponse, TokenUsage, ToolDefinition
from .messages import ConversationMessage

log = logging.getLogger("noval.usage")


@dataclass
class UsageBreakdown:
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    reasoning_tokens: int = 0
    cache_reported: bool = False
    reasoning_reported: bool = False

    def add(self, event: Dict[str, Any]) -> None:
        self.requests += 1
        self.prompt_tokens += event["prompt_tokens"]
        self.completion_tokens += event["completion_tokens"]
        self.total_tokens += event["total_tokens"]
        if event.get("cache_hit_tokens") is not None:
            self.cache_hit_tokens += event["cache_hit_tokens"]
            self.cache_reported = True
        if event.get("cache_miss_tokens") is not None:
            self.cache_miss_tokens += event["cache_miss_tokens"]
            self.cache_reported = True
        if event.get("reasoning_tokens") is not None:
            self.reasoning_tokens += event["reasoning_tokens"]
            self.reasoning_reported = True


@dataclass
class UsageSummary:
    day: date
    total: UsageBreakdown = field(default_factory=UsageBreakdown)
    by_model: Dict[str, UsageBreakdown] = field(default_factory=dict)
    by_purpose: Dict[str, UsageBreakdown] = field(default_factory=dict)


class UsageRecorder(Protocol):
    def record(self, model: str, usage: TokenUsage, *, purpose: str = "agent") -> Path:
        ...


class JsonlUsageStore:
    """每进程追加事件文件，汇总时读取当天所有文件，避免共享计数文件竞争。"""

    def __init__(
        self,
        root: Path,
        session_id: Optional[str] = None,
        *,
        now: Optional[Callable[[], datetime]] = None,
    ):
        self.root = Path(root)
        identity = session_id or f"invocation-{uuid4().hex[:8]}"
        self.identity = re.sub(r"[^A-Za-z0-9._-]+", "-", identity).strip("-") or "invocation"
        self._now = now or (lambda: datetime.now().astimezone())

    def record(self, model: str, usage: TokenUsage, *, purpose: str = "agent") -> Path:
        timestamp = self._now()
        event: Dict[str, Any] = {
            "schema_version": 1,
            "timestamp": timestamp.isoformat(timespec="seconds"),
            "model": model or "unknown",
            "purpose": _safe_purpose(purpose),
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
        }
        for name in ("cache_hit_tokens", "cache_miss_tokens", "reasoning_tokens"):
            value = getattr(usage, name)
            if value is not None:
                event[name] = value

        day_dir = self.root / timestamp.date().isoformat()
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"noval-{self.identity}-{os.getpid()}.jsonl"
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return path

    def summarize(self, day: Optional[date] = None) -> UsageSummary:
        selected = day or self._now().date()
        summary = UsageSummary(day=selected)
        day_dir = self.root / selected.isoformat()
        if not day_dir.is_dir():
            return summary

        for path in day_dir.glob("*.jsonl"):
            self._read_file(path, summary)
        return summary

    @staticmethod
    def _read_file(path: Path, summary: UsageSummary) -> None:
        try:
            file = path.open("r", encoding="utf-8")
        except OSError:
            log.warning("读取 token 用量文件失败: %s", path, exc_info=True)
            return
        with file:
            for line_number, line in enumerate(file, 1):
                try:
                    event = json.loads(line)
                    if not _valid_event(event):
                        raise ValueError("invalid usage event")
                except (json.JSONDecodeError, ValueError, TypeError):
                    log.warning("跳过损坏的 token 用量记录: %s:%s", path, line_number)
                    continue
                summary.total.add(event)
                model = event.get("model") or "unknown"
                summary.by_model.setdefault(model, UsageBreakdown()).add(event)
                purpose = event.get("purpose") or "agent"
                summary.by_purpose.setdefault(purpose, UsageBreakdown()).add(event)


def _valid_event(event: Any) -> bool:
    if not isinstance(event, dict):
        return False
    if event.get("schema_version") != 1:
        return False
    if not isinstance(event.get("model"), str) or not event["model"]:
        return False
    if "purpose" in event and (
        not isinstance(event["purpose"], str) or not event["purpose"]
    ):
        return False
    required = ("prompt_tokens", "completion_tokens", "total_tokens")
    optional = ("cache_hit_tokens", "cache_miss_tokens", "reasoning_tokens")
    if not all(_is_token_count(event.get(name)) for name in required):
        return False
    return all(name not in event or _is_token_count(event[name]) for name in optional)


def _is_token_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class MeteredLLMClient:
    """给任意 Provider 适配器增加用量计量，不让统计故障影响模型结果。"""

    def __init__(
        self,
        inner: LLMClient,
        store: UsageRecorder,
        model: str,
        *,
        purpose: str = "agent",
    ):
        self.inner = inner
        self.store = store
        self.model = model
        self.purpose = _safe_purpose(purpose)

    def complete(
        self,
        messages: Sequence[ConversationMessage],
        tools: Sequence[ToolDefinition],
    ) -> LLMResponse:
        response = self.inner.complete(messages, tools)
        if response.usage is None:
            return response
        response_model = response.provider.model or self.model
        try:
            self.store.record(str(response_model), response.usage, purpose=self.purpose)
        except Exception:
            log.warning("token 用量持久化失败，已跳过本次记录", exc_info=True)
        return response

    def render_request(
        self,
        messages: Sequence[ConversationMessage],
        tools: Sequence[ToolDefinition],
    ) -> Optional[Dict[str, Any]]:
        renderer = getattr(self.inner, "render_request", None)
        if renderer is None:
            return None
        return renderer(messages, tools)


def _safe_purpose(value: str) -> str:
    text = str(value or "agent").strip().lower().replace("-", "_")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", text):
        return "agent"
    return text
