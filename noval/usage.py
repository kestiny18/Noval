"""Side-channel model token metering and daily aggregation."""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional, Protocol, Sequence
from uuid import uuid4

from .api import (
    UsageAnalytics,
    UsageDailyPoint,
    UsageModelSummary,
    UsageModelTokens,
)
from .client import (
    LLMClient,
    LLMResponse,
    LLMStreamObserver,
    TokenUsage,
    ToolDefinition,
)
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
    """Append per-process events and aggregate daily files without shared-counter races."""

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
            "schema_version": 2,
            "event_type": "model_usage",
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
        return self._append(timestamp, event)

    def record_turn(self, model: str, duration_ms: float) -> Path:
        timestamp = self._now()
        event: Dict[str, Any] = {
            "schema_version": 2,
            "event_type": "turn",
            "timestamp": timestamp.isoformat(timespec="seconds"),
            "model": model or "unknown",
            "duration_ms": max(0, round(duration_ms)),
        }
        return self._append(timestamp, event)

    def _append(self, timestamp: datetime, event: Dict[str, Any]) -> Path:
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

    def analytics(self, days: int = 364) -> UsageAnalytics:
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 3660:
            raise ValueError("days must be an integer between 1 and 3660")
        generated_at = self._now()
        window_end = generated_at.date()
        window_start = window_end - timedelta(days=days - 1)
        tokens_by_day: Dict[date, int] = {}
        tokens_by_model: Dict[str, int] = {}
        model_tokens_by_day: Dict[date, Dict[str, int]] = {}
        longest_turn_duration_ms = 0
        longest_turn_by_model: Dict[str, int] = {}

        if self.root.is_dir():
            for day_dir in self.root.iterdir():
                if not day_dir.is_dir():
                    continue
                try:
                    event_day = date.fromisoformat(day_dir.name)
                except ValueError:
                    continue
                if event_day > window_end:
                    continue
                for path in day_dir.glob("*.jsonl"):
                    for kind, event in self._iter_file(path):
                        model = event["model"]
                        if kind == "model_usage":
                            total = event["total_tokens"]
                            tokens_by_day[event_day] = tokens_by_day.get(event_day, 0) + total
                            tokens_by_model[model] = tokens_by_model.get(model, 0) + total
                            daily_models = model_tokens_by_day.setdefault(event_day, {})
                            daily_models[model] = daily_models.get(model, 0) + total
                        else:
                            duration = event["duration_ms"]
                            longest_turn_duration_ms = max(
                                longest_turn_duration_ms,
                                duration,
                            )
                            longest_turn_by_model[model] = max(
                                longest_turn_by_model.get(model, 0),
                                duration,
                            )

        model_names = sorted(set(tokens_by_model) | set(longest_turn_by_model))
        model_summaries = tuple(
            UsageModelSummary(
                model=model,
                total_tokens=tokens_by_model.get(model, 0),
                peak_daily_tokens=max(
                    (
                        totals.get(model, 0)
                        for totals in model_tokens_by_day.values()
                    ),
                    default=0,
                ),
                longest_turn_duration_ms=longest_turn_by_model.get(model, 0),
            )
            for model in model_names
        )
        daily_points = tuple(
            UsageDailyPoint(
                day=current.isoformat(),
                total_tokens=tokens_by_day.get(current, 0),
                by_model=tuple(
                    UsageModelTokens(model=model, total_tokens=total)
                    for model, total in sorted(
                        model_tokens_by_day.get(current, {}).items()
                    )
                ),
            )
            for current in (
                window_start + timedelta(days=offset)
                for offset in range(days)
            )
        )
        return UsageAnalytics(
            generated_at=generated_at.isoformat(timespec="seconds"),
            window_start=window_start.isoformat(),
            window_end=window_end.isoformat(),
            total_tokens=sum(tokens_by_day.values()),
            peak_daily_tokens=max(tokens_by_day.values(), default=0),
            longest_turn_duration_ms=longest_turn_duration_ms,
            models=model_summaries,
            days=daily_points,
        )

    @staticmethod
    def _read_file(path: Path, summary: UsageSummary) -> None:
        for kind, event in JsonlUsageStore._iter_file(path):
            if kind != "model_usage":
                continue
            summary.total.add(event)
            model = event.get("model") or "unknown"
            summary.by_model.setdefault(model, UsageBreakdown()).add(event)
            purpose = event.get("purpose") or "agent"
            summary.by_purpose.setdefault(purpose, UsageBreakdown()).add(event)

    @staticmethod
    def _iter_file(path: Path) -> Iterator[tuple[str, Dict[str, Any]]]:
        try:
            file = path.open("r", encoding="utf-8")
        except OSError:
            log.warning("failed to read token usage file: %s", path, exc_info=True)
            return
        with file:
            for line_number, line in enumerate(file, 1):
                try:
                    event = json.loads(line)
                    kind = _event_kind(event)
                    if kind is None:
                        raise ValueError("invalid usage event")
                except (json.JSONDecodeError, ValueError, TypeError):
                    log.warning("skipping corrupt token usage record: %s:%s", path, line_number)
                    continue
                yield kind, event


def _event_kind(event: Any) -> Optional[str]:
    if not isinstance(event, dict):
        return None
    schema_version = event.get("schema_version")
    event_type = event.get("event_type")
    if schema_version == 1 and event_type is None:
        return "model_usage" if _valid_usage_event(event) else None
    if schema_version != 2:
        return None
    if event_type == "model_usage":
        return "model_usage" if _valid_usage_event(event) else None
    if event_type == "turn":
        return "turn" if _valid_turn_event(event) else None
    return None


def _valid_identity(event: Dict[str, Any]) -> bool:
    if not isinstance(event.get("model"), str) or not event["model"]:
        return False
    timestamp = event.get("timestamp")
    if timestamp is not None:
        if not isinstance(timestamp, str):
            return False
        try:
            parsed = datetime.fromisoformat(timestamp)
        except ValueError:
            return False
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return False
    return True


def _valid_usage_event(event: Dict[str, Any]) -> bool:
    if not _valid_identity(event):
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


def _valid_turn_event(event: Dict[str, Any]) -> bool:
    return _valid_identity(event) and _is_token_count(event.get("duration_ms"))


def _is_token_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class MeteredLLMClient:
    """Meter any Provider adapter without allowing accounting failures to affect results."""

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
        return self._record(response)

    def stream_complete(
        self,
        messages: Sequence[ConversationMessage],
        tools: Sequence[ToolDefinition],
        on_event: LLMStreamObserver,
    ) -> LLMResponse:
        streamer = getattr(self.inner, "stream_complete", None)
        if callable(streamer):
            response = streamer(messages, tools, on_event)
        else:
            response = self.inner.complete(messages, tools)
        return self._record(response)

    def _record(self, response: LLMResponse) -> LLMResponse:
        if response.usage is None:
            return response
        response_model = response.provider.model or self.model
        try:
            self.store.record(str(response_model), response.usage, purpose=self.purpose)
        except Exception:
            log.warning("failed to persist token usage; skipping this record", exc_info=True)
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
