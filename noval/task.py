"""Task completion judging.

The task layer is deliberately narrow: the main model executes the work and
talks to the user; the judge model only decides whether the final visible reply
completed the recent user request. It does not guard tools, infer action scope,
or maintain a task plan.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .client import LLMClient

log = logging.getLogger("noval.task")

TASK_EVENT_SCHEMA_VERSION = 1
TASK_JUDGE_PROMPT_VERSION = "task-completion-judge-v2"
MAX_RECENT_USER_INPUTS = 3
MAX_USER_INPUT_CHARS = 1200


class TaskStatus(str, Enum):
    ACTIVE = "active"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    WAITING_USER = "waiting_user"
    BLOCKED = "blocked"
    UNCERTAIN = "uncertain"


@dataclass
class CompletionVerdict:
    status: TaskStatus
    confidence: float = 0.0
    reason: str = ""
    missing: List[str] = field(default_factory=list)
    source: str = "judge"
    prompt_version: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "status": self.status.value,
            "confidence": self.confidence,
            "reason": self.reason,
            "missing": list(self.missing),
            "source": self.source,
        }
        if self.prompt_version:
            data["prompt_version"] = self.prompt_version
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CompletionVerdict":
        return cls(
            status=_enum_value(TaskStatus, data.get("status"), TaskStatus.UNCERTAIN),
            confidence=_float_between_zero_one(data.get("confidence")),
            reason=_reason_from_dict(data),
            missing=_string_list(data.get("missing")),
            source=str(data.get("source") or "judge"),
            prompt_version=str(data["prompt_version"]) if data.get("prompt_version") else None,
        )


@dataclass
class TaskState:
    status: TaskStatus = TaskStatus.ACTIVE
    recent_user_inputs: List[str] = field(default_factory=list)
    last_verdict: Optional[CompletionVerdict] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "recent_user_inputs": list(self.recent_user_inputs),
            "last_verdict": self.last_verdict.to_dict() if self.last_verdict else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskState":
        verdict_data = data.get("last_verdict")
        recent = _string_list(data.get("recent_user_inputs"))
        if not recent:
            # Old task snapshots stored the current objective under spec. Task
            # state is derived, so this tiny migration is enough for graceful
            # resume without keeping the old model alive.
            spec = data.get("spec")
            if isinstance(spec, dict) and isinstance(spec.get("objective"), str):
                recent = [_compact_text(spec["objective"])]
        return cls(
            status=_enum_value(TaskStatus, data.get("status"), TaskStatus.ACTIVE),
            recent_user_inputs=recent[-MAX_RECENT_USER_INPUTS:],
            last_verdict=(
                CompletionVerdict.from_dict(verdict_data)
                if isinstance(verdict_data, dict) else None
            ),
        )


class TaskEventStore:
    """Append-only task-state snapshots."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def append_state(
        self,
        state: TaskState,
        *,
        reason: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        event = {
            "schema_version": TASK_EVENT_SCHEMA_VERSION,
            "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "type": "state_snapshot",
            "reason": reason,
            "state": state.to_dict(),
            "payload": dict(payload or {}),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def load_latest(self) -> TaskState:
        latest: Optional[TaskState] = None
        if not self.path.exists():
            return TaskState()
        try:
            file = self.path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            log.warning("failed to read task event store: %s", self.path, exc_info=True)
            return TaskState()
        with file:
            for line_number, line in enumerate(file, 1):
                try:
                    raw = json.loads(line)
                    if raw.get("schema_version") != TASK_EVENT_SCHEMA_VERSION:
                        raise ValueError("unsupported task event schema")
                    state = raw.get("state")
                    if not isinstance(state, dict):
                        raise ValueError("task event missing state")
                    latest = TaskState.from_dict(state)
                except (json.JSONDecodeError, TypeError, ValueError):
                    log.warning("skipping corrupt task event: %s:%s", self.path, line_number)
        return latest or TaskState()


class CompletionVerifier:
    def __init__(self, semantic_judge: Optional["SemanticJudge"] = None):
        self.semantic_judge = semantic_judge

    def verify(self, state: TaskState, candidate_reply: str) -> CompletionVerdict:
        if not state.recent_user_inputs:
            return CompletionVerdict(
                status=TaskStatus.UNCERTAIN,
                confidence=0.0,
                reason="no recent user input to judge",
                source="no_task",
            )
        if self.semantic_judge is None:
            return CompletionVerdict(
                status=TaskStatus.UNCERTAIN,
                confidence=0.0,
                reason="completion judge unavailable",
                source="judge_unavailable",
            )
        try:
            return self.semantic_judge.judge(state.recent_user_inputs, candidate_reply)
        except Exception:
            log.warning("semantic task judge failed", exc_info=True)
            return CompletionVerdict(
                status=TaskStatus.UNCERTAIN,
                confidence=0.0,
                reason="completion judge failed",
                source="judge_unavailable",
            )


class SemanticJudge:
    def __init__(self, client: LLMClient, *, model: str = "unknown"):
        self.client = client
        self.model = model

    def judge(self, recent_user_inputs: List[str], assistant_final_reply: str) -> CompletionVerdict:
        packet = self._packet(recent_user_inputs, assistant_final_reply)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是 Noval 的独立任务完成判定模型。"
                    "你不执行任务，不调用工具，不给主模型建议，不补充事实，也不评价文风。"
                    "只根据给定的最近用户输入和助手最后可见回复，判断最后回复是否完成了当前用户请求。"
                    "如果信息不足以判断，返回 uncertain。只输出严格 JSON。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(packet, ensure_ascii=False, separators=(",", ":")),
            },
        ]
        response = self.client.complete(messages, [])
        data = _load_json_object(response.content or "")
        return self._verdict_from_json(data)

    def _packet(self, recent_user_inputs: List[str], assistant_final_reply: str) -> Dict[str, Any]:
        return {
            "prompt_version": TASK_JUDGE_PROMPT_VERSION,
            "recent_user_inputs": _last_unique(recent_user_inputs),
            "assistant_final_reply": assistant_final_reply,
            "allowed_status": [
                "completed",
                "incomplete",
                "waiting_user",
                "blocked",
                "uncertain",
            ],
            "instruction": (
                "Return JSON only: {"
                "\"status\":\"completed|incomplete|waiting_user|blocked|uncertain\","
                "\"confidence\":0.0,"
                "\"reason\":\"short reason\","
                "\"missing\":[\"optional missing item\"]"
                "}."
            ),
        }

    def _verdict_from_json(self, data: Dict[str, Any]) -> CompletionVerdict:
        status = _enum_value(TaskStatus, data.get("status"), TaskStatus.UNCERTAIN)
        if status not in {
            TaskStatus.COMPLETED,
            TaskStatus.INCOMPLETE,
            TaskStatus.WAITING_USER,
            TaskStatus.BLOCKED,
            TaskStatus.UNCERTAIN,
        }:
            status = TaskStatus.UNCERTAIN
        return CompletionVerdict(
            status=status,
            confidence=_float_between_zero_one(data.get("confidence")),
            reason=_reason_from_dict(data),
            missing=_string_list(data.get("missing")),
            source=f"judge:{self.model}",
            prompt_version=TASK_JUDGE_PROMPT_VERSION,
        )


class TaskController:
    def __init__(
        self,
        *,
        event_store: Optional[TaskEventStore] = None,
        completion_verifier: Optional[CompletionVerifier] = None,
        state: Optional[TaskState] = None,
    ):
        self.event_store = event_store
        self.completion_verifier = completion_verifier or CompletionVerifier()
        self.state = state or (event_store.load_latest() if event_store else TaskState())

    def observe_user_input(self, user_input: str) -> None:
        text = _strip_context(user_input).strip()
        if not text:
            return
        updated_inputs = _remember_recent_unique(self.state.recent_user_inputs, text)
        if updated_inputs == self.state.recent_user_inputs and self.state.status is TaskStatus.ACTIVE:
            return
        self.state.recent_user_inputs = updated_inputs
        self.state.status = TaskStatus.ACTIVE
        self._persist("user_input_observed")

    def verify_completion(self, candidate_reply: str) -> CompletionVerdict:
        self.state.status = TaskStatus.VERIFYING
        self._persist("completion_verifying")
        verdict = self.completion_verifier.verify(self.state, candidate_reply)
        self.state.last_verdict = verdict
        self.state.status = verdict.status
        self._persist("completion_verdict", {"verdict": verdict.to_dict()})
        return verdict

    def _persist(self, reason: str, payload: Optional[Dict[str, Any]] = None) -> None:
        if self.event_store is None:
            return
        try:
            self.event_store.append_state(self.state, reason=reason, payload=payload)
        except Exception:
            log.warning("task event persistence failed", exc_info=True)


def _remember_recent_unique(current: Iterable[str], text: str) -> List[str]:
    compact = _compact_text(text)
    items = [item for item in current if item != compact]
    items.append(compact)
    return items[-MAX_RECENT_USER_INPUTS:]


def _last_unique(items: Iterable[str]) -> List[str]:
    result: List[str] = []
    for item in items:
        compact = _compact_text(str(item))
        result = [existing for existing in result if existing != compact]
        result.append(compact)
    return result[-MAX_RECENT_USER_INPUTS:]


def _strip_context(text: str) -> str:
    return re.sub(r"^<context>.*?</context>\s*", "", text, flags=re.S)


def _compact_text(text: str, limit: int = MAX_USER_INPUT_CHARS) -> str:
    one_line = re.sub(r"\s+", " ", text).strip()
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, (str, int, float))]


def _float_between_zero_one(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    return 0.0


def _enum_value(enum_cls: Any, value: Any, default: Any) -> Any:
    try:
        return enum_cls(value)
    except (TypeError, ValueError):
        return default


def _reason_from_dict(data: Dict[str, Any]) -> str:
    reason = data.get("reason")
    if isinstance(reason, str):
        return reason
    reasons = data.get("reasons")
    if isinstance(reasons, list) and reasons:
        return "; ".join(str(item) for item in reasons if isinstance(item, (str, int, float)))
    return ""


def _load_json_object(text: str) -> Dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"judge returned invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("judge returned non-object JSON")
    return data
