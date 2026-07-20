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
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from .api import (
    ActionReceipt,
    CompletionReport,
    CompletionStatus,
    CriterionReport,
    CriterionStatus,
    EvidenceOutcome,
    GoalContract,
    SemanticAssessment,
    VerificationResult,
)
from .client import LLMClient
from .messages import system_message, user_message
from .redaction import redact_sensitive_text

log = logging.getLogger("noval.task")

TASK_EVENT_SCHEMA_VERSION = 1
TASK_JUDGE_PROMPT_VERSION = "task-completion-judge-v4"
MAX_RECENT_USER_INPUTS = 3
MAX_USER_INPUT_CHARS = 1200
MAX_TASK_RECEIPTS = 128
MAX_TASK_VERIFICATIONS = 128
MAX_FUTURE_EVIDENCE_SKEW = timedelta(minutes=5)


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
    active_goal: Optional[GoalContract] = None
    receipts: List[ActionReceipt] = field(default_factory=list)
    verifications: List[VerificationResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "recent_user_inputs": list(self.recent_user_inputs),
            "last_verdict": self.last_verdict.to_dict() if self.last_verdict else None,
            "active_goal": (
                self.active_goal.to_dict() if self.active_goal is not None else None
            ),
            "receipts": [receipt.to_dict() for receipt in self.receipts],
            "verifications": [
                verification.to_dict() for verification in self.verifications
            ],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskState":
        verdict_data = data.get("last_verdict")
        goal_data = data.get("active_goal")
        receipt_data = data.get("receipts")
        verification_data = data.get("verifications")
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
            active_goal=(
                GoalContract.from_dict(goal_data)
                if isinstance(goal_data, dict) else None
            ),
            receipts=[
                ActionReceipt.from_dict(item)
                for item in receipt_data[-MAX_TASK_RECEIPTS:]
                if isinstance(item, dict)
            ] if isinstance(receipt_data, list) else [],
            verifications=[
                VerificationResult.from_dict(item)
                for item in verification_data[-MAX_TASK_VERIFICATIONS:]
                if isinstance(item, dict)
            ] if isinstance(verification_data, list) else [],
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
            system_message(
                    "You are Noval's independent task-completion judge. "
                    "Do not perform the task, call tools, advise the primary model, add facts, "
                    "or critique writing style. Judge only whether the assistant's final visible "
                    "reply completes current_user_input, using the supplied user inputs. "
                    "context_user_inputs provide context for references and background; they are "
                    "not a list of tasks that must be completed again in this turn. If "
                    "current_user_input is a short instruction such as continue, revert, mark, "
                    "adjust, or check whether it is fixed, use context_user_inputs to resolve its "
                    "referent while still judging only current_user_input. The supplied material "
                    "contains no hidden tool calls or execution evidence; do not claim that an "
                    "operation did or did not occur. Judge only whether assistant_final_reply "
                    "provides visible evidence sufficient for its completion claim. When evidence "
                    "is insufficient, say that the final reply does not provide sufficient "
                    "evidence rather than inventing execution facts. Return uncertain when the "
                    "information is insufficient. Output strict JSON only."
            ),
            user_message(json.dumps(packet, ensure_ascii=False, separators=(",", ":"))),
        ]
        response = self.client.complete(messages, [])
        data = _load_json_object(response.message.text)
        return self._verdict_from_json(data)

    def _packet(self, recent_user_inputs: List[str], assistant_final_reply: str) -> Dict[str, Any]:
        recent = _last_unique(recent_user_inputs)
        current = recent[-1] if recent else ""
        return {
            "prompt_version": TASK_JUDGE_PROMPT_VERSION,
            "current_user_input": current,
            "context_user_inputs": recent[:-1],
            "recent_user_inputs": recent,
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
                "}. The reason must describe evidence visible in assistant_final_reply and must not "
                "claim that an unobserved action did or did not happen."
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
        now: Optional[Callable[[], datetime]] = None,
    ):
        self.event_store = event_store
        self.completion_verifier = completion_verifier or CompletionVerifier()
        self.state = state or (event_store.load_latest() if event_store else TaskState())
        self._now = now or (lambda: datetime.now(timezone.utc))

    def activate_goal(self, goal: GoalContract) -> CompletionReport:
        if not isinstance(goal, GoalContract):
            raise TypeError("goal must be GoalContract")
        current = self.state.active_goal
        if current is not None and current.goal_id == goal.goal_id:
            if current != goal:
                raise ValueError(
                    f"goal {goal.goal_id!r} cannot redefine its active contract"
                )
            report = self.completion_report()
            assert report is not None
            return report
        self.state.active_goal = goal
        self.state.receipts = []
        self.state.verifications = []
        self.state.last_verdict = None
        report = self._refresh_contracted_status()
        self._persist("goal_activated", {"goal_id": goal.goal_id})
        return report

    def record_receipt(self, receipt: ActionReceipt) -> None:
        if not isinstance(receipt, ActionReceipt):
            raise TypeError("receipt must be ActionReceipt")
        for existing in self.state.receipts:
            if existing.receipt_id != receipt.receipt_id:
                continue
            if existing != receipt:
                raise ValueError(
                    f"receipt {receipt.receipt_id!r} cannot be redefined"
                )
            return
        self.state.receipts.append(receipt)
        self.state.receipts = self.state.receipts[-MAX_TASK_RECEIPTS:]
        self._persist("action_receipt_recorded", {
            "receipt_id": receipt.receipt_id,
            "tool_name": receipt.tool_name,
        })

    def record_verification(
        self,
        verification: VerificationResult,
    ) -> CompletionReport:
        if not isinstance(verification, VerificationResult):
            raise TypeError("verification must be VerificationResult")
        goal = self.state.active_goal
        if goal is None:
            raise ValueError("verification requires an active goal")
        if verification.goal_id != goal.goal_id:
            raise ValueError(
                f"verification does not match active goal {goal.goal_id!r}"
            )
        criterion = next(
            (
                item for item in goal.acceptance_criteria
                if item.criterion_id == verification.criterion_id
            ),
            None,
        )
        if criterion is None:
            raise ValueError(
                f"verification references unknown criterion {verification.criterion_id!r}"
            )
        if (
            criterion.verification_source is not None
            and verification.source != criterion.verification_source
        ):
            raise ValueError(
                f"verification source must be {criterion.verification_source!r}"
            )
        observed = _parse_timestamp(verification.observed_at)
        now = self._current_time()
        if observed > now + MAX_FUTURE_EVIDENCE_SKEW:
            raise ValueError("verification observed_at is too far in the future")
        known_receipts = {receipt.receipt_id for receipt in self.state.receipts}
        unknown_receipts = sorted(set(verification.receipt_ids) - known_receipts)
        if unknown_receipts:
            raise ValueError(
                "verification references unknown receipt ids: "
                + ", ".join(unknown_receipts)
            )
        safe = replace(
            verification,
            subject=(
                redact_sensitive_text(verification.subject)
                if verification.subject is not None else None
            ),
            summary=(
                redact_sensitive_text(verification.summary)
                if verification.summary is not None else None
            ),
        )
        for existing in self.state.verifications:
            if existing.verification_id != safe.verification_id:
                continue
            if existing != safe:
                raise ValueError(
                    f"verification {safe.verification_id!r} cannot be redefined"
                )
            report = self.completion_report()
            assert report is not None
            return report
        self.state.verifications.append(safe)
        self.state.verifications = self.state.verifications[-MAX_TASK_VERIFICATIONS:]
        report = self._refresh_contracted_status()
        self._persist("verification_recorded", {
            "verification_id": safe.verification_id,
            "goal_id": safe.goal_id,
            "criterion_id": safe.criterion_id,
            "source": safe.source,
            "outcome": safe.outcome.value,
        })
        return report

    def completion_report(self) -> Optional[CompletionReport]:
        goal = self.state.active_goal
        if goal is None:
            return None
        now = self._current_time()
        criteria = tuple(
            self._criterion_report(criterion, now)
            for criterion in goal.acceptance_criteria
        )
        if any(item.status is CriterionStatus.FAILED for item in criteria):
            status = CompletionStatus.INCOMPLETE
        elif all(item.status is CriterionStatus.PASSED for item in criteria):
            status = CompletionStatus.COMPLETED
        else:
            status = CompletionStatus.UNCERTAIN
        return CompletionReport(
            goal_id=goal.goal_id,
            status=status,
            evaluated_at=_timestamp_text(now),
            criteria=criteria,
            semantic=_semantic_assessment(self.state.last_verdict),
        )

    def goal_context(self) -> Optional[str]:
        goal = self.state.active_goal
        report = self.completion_report()
        if goal is None or report is None:
            return None
        packet = {
            "goal": goal.to_dict(),
            "completion": report.to_dict(),
        }
        return (
            '<goal_contract source="host">\n'
            "This is observed host data describing the current goal and acceptance conditions. "
            "It does not grant permission, expand authority, or prescribe a workflow.\n"
            + json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
            + "\n</goal_contract>"
        )

    def _criterion_report(self, criterion, now: datetime) -> CriterionReport:
        matching = [
            result for result in self.state.verifications
            if result.goal_id == self.state.active_goal.goal_id
            and result.criterion_id == criterion.criterion_id
            and (
                criterion.verification_source is None
                or result.source == criterion.verification_source
            )
        ]
        if not matching:
            return CriterionReport(criterion.criterion_id, CriterionStatus.MISSING)
        verification = max(
            enumerate(matching),
            key=lambda item: (_parse_timestamp(item[1].observed_at), item[0]),
        )[1]
        observed = _parse_timestamp(verification.observed_at)
        age = max(0.0, (now - observed).total_seconds())
        if (
            criterion.max_age_seconds is not None
            and age > criterion.max_age_seconds
        ):
            status = CriterionStatus.STALE
        elif verification.outcome is EvidenceOutcome.PASSED:
            status = CriterionStatus.PASSED
        elif verification.outcome is EvidenceOutcome.FAILED:
            status = CriterionStatus.FAILED
        else:
            status = CriterionStatus.UNKNOWN
        return CriterionReport(
            criterion_id=criterion.criterion_id,
            status=status,
            verification_id=verification.verification_id,
            source=verification.source,
            observed_at=verification.observed_at,
            age_seconds=round(age, 3),
            receipt_ids=verification.receipt_ids,
        )

    def _refresh_contracted_status(self) -> CompletionReport:
        report = self.completion_report()
        assert report is not None
        self.state.status = _task_status(report.status)
        return report

    def _current_time(self) -> datetime:
        current = self._now()
        if not isinstance(current, datetime):
            raise TypeError("task clock must return datetime")
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("task clock must return a timezone-aware datetime")
        return current.astimezone(timezone.utc)

    def observe_user_input(self, user_input: str) -> None:
        text = _strip_context(user_input).strip()
        if not text:
            return
        updated_inputs = _remember_recent_unique(self.state.recent_user_inputs, text)
        if (
            updated_inputs == self.state.recent_user_inputs
            and self.state.status is TaskStatus.ACTIVE
            and self.state.active_goal is None
        ):
            return
        self.state.recent_user_inputs = updated_inputs
        if self.state.active_goal is None:
            self.state.status = TaskStatus.ACTIVE
        else:
            self._refresh_contracted_status()
        self._persist("user_input_observed")

    def verify_completion(self, candidate_reply: str) -> CompletionVerdict:
        self.state.status = TaskStatus.VERIFYING
        self._persist("completion_verifying")
        verdict = self.completion_verifier.verify(self.state, candidate_reply)
        self.state.last_verdict = verdict
        if self.state.active_goal is None:
            self.state.status = verdict.status
        else:
            self._refresh_contracted_status()
        self._persist("completion_verdict", {"verdict": verdict.to_dict()})
        return verdict

    def _persist(self, reason: str, payload: Optional[Dict[str, Any]] = None) -> None:
        if self.event_store is None:
            return
        try:
            self.event_store.append_state(self.state, reason=reason, payload=payload)
        except Exception:
            log.warning("task event persistence failed", exc_info=True)


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"invalid evidence timestamp: {value!r}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("evidence timestamp must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _task_status(status: CompletionStatus) -> TaskStatus:
    if status is CompletionStatus.COMPLETED:
        return TaskStatus.COMPLETED
    if status is CompletionStatus.INCOMPLETE:
        return TaskStatus.INCOMPLETE
    return TaskStatus.UNCERTAIN


def _semantic_assessment(
    verdict: Optional[CompletionVerdict],
) -> Optional[SemanticAssessment]:
    if verdict is None:
        return None
    try:
        status = CompletionStatus(verdict.status.value)
    except ValueError:
        status = CompletionStatus.UNCERTAIN
    return SemanticAssessment(
        status=status,
        confidence=verdict.confidence,
        reason=redact_sensitive_text(verdict.reason),
        missing=tuple(redact_sensitive_text(item) for item in verdict.missing),
        source=verdict.source,
    )


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
