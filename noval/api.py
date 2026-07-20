"""Stable host-facing contracts for Noval's Headless/Application API."""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple

from .client import TokenUsage
from .messages import ConversationMessage
from .permissions import PermissionMode
from .process import NetworkAccess, SandboxMode

API_SCHEMA_VERSION = 1
JSONValue = Any


class ApiFormatError(ValueError):
    """A host-facing API document does not satisfy the public contract."""


class SessionPersistence(str, Enum):
    DEFAULT = "default"
    PERSISTENT = "persistent"
    EPHEMERAL = "ephemeral"


class TurnStatus(str, Enum):
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    WAITING_USER = "waiting_user"
    BLOCKED = "blocked"
    UNCERTAIN = "uncertain"
    STOPPED = "stopped"
    FAILED = "failed"


class StopReason(str, Enum):
    COMPLETED = "completed"
    MAX_STEPS = "max_steps"
    CANCELLED = "cancelled"
    VALIDATION_STALLED = "validation_stalled"
    ERROR = "error"


class PermissionDecision(str, Enum):
    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"
    DENY = "deny"


class EventType(str, Enum):
    SESSION_OPENED = "session.opened"
    SESSION_CLOSED = "session.closed"
    TURN_STARTED = "turn.started"
    TURN_CANCEL_REQUESTED = "turn.cancel_requested"
    MODEL_STARTED = "model.started"
    MODEL_COMPLETED = "model.completed"
    PERMISSION_REQUESTED = "permission.requested"
    PERMISSION_RESOLVED = "permission.resolved"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    VALIDATION_STARTED = "validation.started"
    VALIDATION_COMPLETED = "validation.completed"
    TURN_COMPLETED = "turn.completed"
    TURN_FAILED = "turn.failed"


class CompletionStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    WAITING_USER = "waiting_user"
    BLOCKED = "blocked"
    UNCERTAIN = "uncertain"


class ReceiptKind(str, Enum):
    OBSERVATION = "observation"
    ACTION = "action"


class ReceiptOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NOT_EXECUTED = "not_executed"


class EvidenceOutcome(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class CriterionStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    MISSING = "missing"
    STALE = "stale"
    UNKNOWN = "unknown"


_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SOURCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_MAX_CONTRACT_TEXT = 4000
_MAX_CONTRACT_ITEMS = 64


def _object(data: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(data, dict):
        raise ApiFormatError(f"{label} must be an object")
    return data


def _request_fields(
    data: Any,
    label: str,
    *,
    allowed: set[str],
) -> Mapping[str, Any]:
    obj = _object(data, label)
    unknown = set(obj) - allowed
    if unknown:
        raise ApiFormatError(
            f"{label} contains unknown field(s): {', '.join(sorted(unknown))}"
        )
    return obj


def _schema(data: Mapping[str, Any], label: str) -> None:
    version = data.get("schema_version", API_SCHEMA_VERSION)
    if version != API_SCHEMA_VERSION:
        raise ApiFormatError(
            f"{label}.schema_version must be {API_SCHEMA_VERSION}"
        )


def _string(value: Any, label: str, *, optional: bool = False) -> Optional[str]:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        suffix = " or null" if optional else ""
        raise ApiFormatError(f"{label} must be a non-empty string{suffix}")
    return value


def _bounded_string(
    value: Any,
    label: str,
    *,
    optional: bool = False,
    maximum: int = _MAX_CONTRACT_TEXT,
) -> Optional[str]:
    parsed = _string(value, label, optional=optional)
    if parsed is not None and len(parsed) > maximum:
        raise ApiFormatError(f"{label} must not exceed {maximum} characters")
    return parsed


def _identifier(value: Any, label: str, *, source: bool = False) -> str:
    parsed = _bounded_string(value, label, maximum=128) or ""
    pattern = _SOURCE_PATTERN if source else _ID_PATTERN
    if not pattern.fullmatch(parsed):
        kind = "source identifier" if source else "identifier"
        raise ApiFormatError(f"{label} must be a bounded {kind}")
    return parsed


def _timestamp(value: Any, label: str) -> str:
    parsed = _bounded_string(value, label, maximum=64) or ""
    try:
        moment = datetime.fromisoformat(parsed.replace("Z", "+00:00"))
    except ValueError as error:
        raise ApiFormatError(f"{label} must be an ISO-8601 timestamp") from error
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ApiFormatError(f"{label} must include a timezone offset")
    return parsed


def _string_tuple(
    value: Any,
    label: str,
    *,
    identifiers: bool = False,
    maximum: int = _MAX_CONTRACT_ITEMS,
) -> Tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ApiFormatError(f"{label} must be an immutable tuple")
    if len(value) > maximum:
        raise ApiFormatError(f"{label} must not contain more than {maximum} items")
    if identifiers:
        return tuple(_identifier(item, f"{label} item") for item in value)
    return tuple(_bounded_string(item, f"{label} item") or "" for item in value)


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ApiFormatError(f"{label} must be boolean")
    return value


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ApiFormatError(f"{label} must be an integer >= {minimum}")
    return value


def _number(value: Any, label: str, *, minimum: float = 0.0) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ApiFormatError(f"{label} must be a number")
    parsed = float(value)
    if parsed < minimum:
        raise ApiFormatError(f"{label} must be >= {minimum}")
    return parsed


def _enum(enum_type, value: Any, label: str):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as error:
        raise ApiFormatError(f"{label} has an unsupported value: {value!r}") from error


def _json_object(value: Any, label: str) -> Dict[str, JSONValue]:
    if not isinstance(value, dict):
        raise ApiFormatError(f"{label} must be an object")
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ApiFormatError(f"{label} must contain only JSON values") from error
    return copy.deepcopy(value)


@dataclass(frozen=True)
class AcceptanceCriterion:
    criterion_id: str
    description: str
    verification_source: Optional[str] = None
    max_age_seconds: Optional[int] = None

    def __post_init__(self) -> None:
        _identifier(self.criterion_id, "criterion_id")
        _bounded_string(self.description, "description")
        if self.verification_source is not None:
            _identifier(
                self.verification_source,
                "verification_source",
                source=True,
            )
        if self.max_age_seconds is not None:
            _integer(self.max_age_seconds, "max_age_seconds", minimum=1)

    def to_dict(self) -> Dict[str, JSONValue]:
        return {
            "criterion_id": self.criterion_id,
            "description": self.description,
            "verification_source": self.verification_source,
            "max_age_seconds": self.max_age_seconds,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "AcceptanceCriterion":
        obj = _request_fields(
            data,
            "acceptance_criterion",
            allowed={
                "criterion_id", "description", "verification_source",
                "max_age_seconds",
            },
        )
        max_age = obj.get("max_age_seconds")
        return cls(
            criterion_id=_identifier(obj.get("criterion_id"), "criterion_id"),
            description=_bounded_string(obj.get("description"), "description") or "",
            verification_source=(
                _identifier(
                    obj.get("verification_source"),
                    "verification_source",
                    source=True,
                )
                if obj.get("verification_source") is not None else None
            ),
            max_age_seconds=(
                _integer(max_age, "max_age_seconds", minimum=1)
                if max_age is not None else None
            ),
        )


@dataclass(frozen=True)
class GoalContract:
    goal_id: str
    objective: str
    scope: Tuple[str, ...] = ()
    authority: Tuple[str, ...] = ()
    acceptance_criteria: Tuple[AcceptanceCriterion, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.goal_id, "goal_id")
        _bounded_string(self.objective, "objective")
        _string_tuple(self.scope, "scope")
        _string_tuple(self.authority, "authority")
        if not isinstance(self.acceptance_criteria, tuple):
            raise ApiFormatError("acceptance_criteria must be an immutable tuple")
        if not self.acceptance_criteria:
            raise ApiFormatError("acceptance_criteria must contain at least one item")
        if len(self.acceptance_criteria) > _MAX_CONTRACT_ITEMS:
            raise ApiFormatError(
                f"acceptance_criteria must not contain more than {_MAX_CONTRACT_ITEMS} items"
            )
        identifiers = []
        for criterion in self.acceptance_criteria:
            if not isinstance(criterion, AcceptanceCriterion):
                raise ApiFormatError(
                    "acceptance_criteria items must be AcceptanceCriterion"
                )
            identifiers.append(criterion.criterion_id)
        if len(identifiers) != len(set(identifiers)):
            raise ApiFormatError("acceptance_criteria criterion ids must be unique")

    def to_dict(self) -> Dict[str, JSONValue]:
        return {
            "goal_id": self.goal_id,
            "objective": self.objective,
            "scope": list(self.scope),
            "authority": list(self.authority),
            "acceptance_criteria": [
                criterion.to_dict() for criterion in self.acceptance_criteria
            ],
        }

    @classmethod
    def from_dict(cls, data: Any) -> "GoalContract":
        obj = _request_fields(
            data,
            "goal_contract",
            allowed={
                "goal_id", "objective", "scope", "authority",
                "acceptance_criteria",
            },
        )
        scope = obj.get("scope", [])
        authority = obj.get("authority", [])
        criteria = obj.get("acceptance_criteria", [])
        if not isinstance(scope, list):
            raise ApiFormatError("scope must be an array")
        if not isinstance(authority, list):
            raise ApiFormatError("authority must be an array")
        if not isinstance(criteria, list):
            raise ApiFormatError("acceptance_criteria must be an array")
        return cls(
            goal_id=_identifier(obj.get("goal_id"), "goal_id"),
            objective=_bounded_string(obj.get("objective"), "objective") or "",
            scope=tuple(scope),
            authority=tuple(authority),
            acceptance_criteria=tuple(
                AcceptanceCriterion.from_dict(item) for item in criteria
            ),
        )


@dataclass(frozen=True)
class ActionReceipt:
    receipt_id: str
    call_id: str
    tool_name: str
    target: str
    kind: ReceiptKind
    risk: str
    outcome: ReceiptOutcome
    executed: bool
    started_at: str
    completed_at: str
    argument_keys: Tuple[str, ...] = ()
    duration_ms: Optional[float] = None
    truncated: bool = False
    redacted: bool = False
    result_digest: Optional[str] = None

    def __post_init__(self) -> None:
        _identifier(self.receipt_id, "receipt_id")
        _bounded_string(self.call_id, "call_id", maximum=256)
        _bounded_string(self.tool_name, "tool_name", maximum=128)
        _bounded_string(self.target, "target", maximum=256)
        if not isinstance(self.kind, ReceiptKind):
            raise ApiFormatError("kind must be ReceiptKind")
        _identifier(self.risk, "risk", source=True)
        if not isinstance(self.outcome, ReceiptOutcome):
            raise ApiFormatError("outcome must be ReceiptOutcome")
        _boolean(self.executed, "executed")
        _timestamp(self.started_at, "started_at")
        _timestamp(self.completed_at, "completed_at")
        _string_tuple(self.argument_keys, "argument_keys")
        if self.duration_ms is not None:
            _number(self.duration_ms, "duration_ms")
        _boolean(self.truncated, "truncated")
        _boolean(self.redacted, "redacted")
        if self.result_digest is not None:
            _bounded_string(self.result_digest, "result_digest", maximum=128)

    def to_dict(self) -> Dict[str, JSONValue]:
        return {
            "receipt_id": self.receipt_id,
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "target": self.target,
            "kind": self.kind.value,
            "risk": self.risk,
            "outcome": self.outcome.value,
            "executed": self.executed,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "argument_keys": list(self.argument_keys),
            "duration_ms": self.duration_ms,
            "truncated": self.truncated,
            "redacted": self.redacted,
            "result_digest": self.result_digest,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ActionReceipt":
        obj = _object(data, "action_receipt")
        keys = obj.get("argument_keys", [])
        if not isinstance(keys, list):
            raise ApiFormatError("argument_keys must be an array")
        duration = obj.get("duration_ms")
        return cls(
            receipt_id=_identifier(obj.get("receipt_id"), "receipt_id"),
            call_id=_bounded_string(
                obj.get("call_id"), "call_id", maximum=256
            ) or "",
            tool_name=_bounded_string(
                obj.get("tool_name"), "tool_name", maximum=128
            ) or "",
            target=_bounded_string(obj.get("target"), "target", maximum=256) or "",
            kind=_enum(ReceiptKind, obj.get("kind"), "kind"),
            risk=_identifier(obj.get("risk"), "risk", source=True),
            outcome=_enum(ReceiptOutcome, obj.get("outcome"), "outcome"),
            executed=_boolean(obj.get("executed"), "executed"),
            started_at=_timestamp(obj.get("started_at"), "started_at"),
            completed_at=_timestamp(obj.get("completed_at"), "completed_at"),
            argument_keys=tuple(keys),
            duration_ms=(
                _number(duration, "duration_ms") if duration is not None else None
            ),
            truncated=_boolean(obj.get("truncated", False), "truncated"),
            redacted=_boolean(obj.get("redacted", False), "redacted"),
            result_digest=_bounded_string(
                obj.get("result_digest"),
                "result_digest",
                optional=True,
                maximum=128,
            ),
        )


@dataclass(frozen=True)
class VerificationResult:
    verification_id: str
    goal_id: str
    criterion_id: str
    source: str
    outcome: EvidenceOutcome
    observed_at: str
    subject: Optional[str] = None
    summary: Optional[str] = None
    receipt_ids: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("verification_id", "goal_id", "criterion_id"):
            _identifier(getattr(self, name), name)
        _identifier(self.source, "source", source=True)
        if not isinstance(self.outcome, EvidenceOutcome):
            raise ApiFormatError("outcome must be EvidenceOutcome")
        _timestamp(self.observed_at, "observed_at")
        for name in ("subject", "summary"):
            value = getattr(self, name)
            if value is not None:
                _bounded_string(value, name)
        _string_tuple(self.receipt_ids, "receipt_ids", identifiers=True)

    def to_dict(self) -> Dict[str, JSONValue]:
        return {
            "verification_id": self.verification_id,
            "goal_id": self.goal_id,
            "criterion_id": self.criterion_id,
            "source": self.source,
            "outcome": self.outcome.value,
            "observed_at": self.observed_at,
            "subject": self.subject,
            "summary": self.summary,
            "receipt_ids": list(self.receipt_ids),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "VerificationResult":
        obj = _request_fields(
            data,
            "verification_result",
            allowed={
                "verification_id", "goal_id", "criterion_id", "source",
                "outcome", "observed_at", "subject", "summary", "receipt_ids",
            },
        )
        receipt_ids = obj.get("receipt_ids", [])
        if not isinstance(receipt_ids, list):
            raise ApiFormatError("receipt_ids must be an array")
        return cls(
            verification_id=_identifier(
                obj.get("verification_id"), "verification_id"
            ),
            goal_id=_identifier(obj.get("goal_id"), "goal_id"),
            criterion_id=_identifier(obj.get("criterion_id"), "criterion_id"),
            source=_identifier(obj.get("source"), "source", source=True),
            outcome=_enum(EvidenceOutcome, obj.get("outcome"), "outcome"),
            observed_at=_timestamp(obj.get("observed_at"), "observed_at"),
            subject=_bounded_string(
                obj.get("subject"), "subject", optional=True
            ),
            summary=_bounded_string(
                obj.get("summary"), "summary", optional=True
            ),
            receipt_ids=tuple(receipt_ids),
        )


@dataclass(frozen=True)
class SemanticAssessment:
    status: CompletionStatus
    confidence: float
    reason: str
    missing: Tuple[str, ...]
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, CompletionStatus):
            raise ApiFormatError("semantic status must be CompletionStatus")
        if self.status is CompletionStatus.ACTIVE:
            raise ApiFormatError("semantic status must be terminal")
        confidence = _number(self.confidence, "semantic confidence")
        if confidence > 1.0:
            raise ApiFormatError("semantic confidence must be <= 1.0")
        _bounded_string(self.reason, "semantic reason")
        _string_tuple(self.missing, "semantic missing")
        _identifier(self.source, "semantic source", source=True)

    def to_dict(self) -> Dict[str, JSONValue]:
        return {
            "status": self.status.value,
            "confidence": self.confidence,
            "reason": self.reason,
            "missing": list(self.missing),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "SemanticAssessment":
        obj = _object(data, "semantic_assessment")
        missing = obj.get("missing", [])
        if not isinstance(missing, list):
            raise ApiFormatError("semantic missing must be an array")
        return cls(
            status=_enum(
                CompletionStatus, obj.get("status"), "semantic status"
            ),
            confidence=_number(
                obj.get("confidence", 0.0), "semantic confidence"
            ),
            reason=_bounded_string(
                obj.get("reason", ""), "semantic reason"
            ) or "",
            missing=tuple(missing),
            source=_identifier(
                obj.get("source"), "semantic source", source=True
            ),
        )


@dataclass(frozen=True)
class CriterionReport:
    criterion_id: str
    status: CriterionStatus
    verification_id: Optional[str] = None
    source: Optional[str] = None
    observed_at: Optional[str] = None
    age_seconds: Optional[float] = None
    receipt_ids: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.criterion_id, "criterion_id")
        if not isinstance(self.status, CriterionStatus):
            raise ApiFormatError("criterion status must be CriterionStatus")
        if self.verification_id is not None:
            _identifier(self.verification_id, "verification_id")
        if self.source is not None:
            _identifier(self.source, "source", source=True)
        if self.observed_at is not None:
            _timestamp(self.observed_at, "observed_at")
        if self.age_seconds is not None:
            _number(self.age_seconds, "age_seconds")
        _string_tuple(self.receipt_ids, "receipt_ids", identifiers=True)

    def to_dict(self) -> Dict[str, JSONValue]:
        return {
            "criterion_id": self.criterion_id,
            "status": self.status.value,
            "verification_id": self.verification_id,
            "source": self.source,
            "observed_at": self.observed_at,
            "age_seconds": self.age_seconds,
            "receipt_ids": list(self.receipt_ids),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "CriterionReport":
        obj = _object(data, "criterion_report")
        receipt_ids = obj.get("receipt_ids", [])
        if not isinstance(receipt_ids, list):
            raise ApiFormatError("receipt_ids must be an array")
        age = obj.get("age_seconds")
        return cls(
            criterion_id=_identifier(obj.get("criterion_id"), "criterion_id"),
            status=_enum(CriterionStatus, obj.get("status"), "criterion status"),
            verification_id=(
                _identifier(obj.get("verification_id"), "verification_id")
                if obj.get("verification_id") is not None else None
            ),
            source=(
                _identifier(obj.get("source"), "source", source=True)
                if obj.get("source") is not None else None
            ),
            observed_at=(
                _timestamp(obj.get("observed_at"), "observed_at")
                if obj.get("observed_at") is not None else None
            ),
            age_seconds=_number(age, "age_seconds") if age is not None else None,
            receipt_ids=tuple(receipt_ids),
        )


@dataclass(frozen=True)
class CompletionReport:
    goal_id: str
    status: CompletionStatus
    evaluated_at: str
    criteria: Tuple[CriterionReport, ...] = ()
    semantic: Optional[SemanticAssessment] = None

    def __post_init__(self) -> None:
        _identifier(self.goal_id, "goal_id")
        if not isinstance(self.status, CompletionStatus):
            raise ApiFormatError("completion status must be CompletionStatus")
        if self.status not in {
            CompletionStatus.COMPLETED,
            CompletionStatus.INCOMPLETE,
            CompletionStatus.UNCERTAIN,
        }:
            raise ApiFormatError(
                "contracted completion status must be completed, incomplete, or uncertain"
            )
        _timestamp(self.evaluated_at, "evaluated_at")
        if not isinstance(self.criteria, tuple):
            raise ApiFormatError("criteria must be an immutable tuple")
        if len(self.criteria) > _MAX_CONTRACT_ITEMS:
            raise ApiFormatError(
                f"criteria must not contain more than {_MAX_CONTRACT_ITEMS} items"
            )
        for criterion in self.criteria:
            if not isinstance(criterion, CriterionReport):
                raise ApiFormatError("criteria items must be CriterionReport")
        if self.semantic is not None and not isinstance(
            self.semantic, SemanticAssessment
        ):
            raise ApiFormatError("semantic must be SemanticAssessment")

    def to_dict(self) -> Dict[str, JSONValue]:
        return {
            "goal_id": self.goal_id,
            "status": self.status.value,
            "evaluated_at": self.evaluated_at,
            "criteria": [criterion.to_dict() for criterion in self.criteria],
            "semantic": self.semantic.to_dict() if self.semantic else None,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "CompletionReport":
        obj = _object(data, "completion_report")
        criteria = obj.get("criteria", [])
        if not isinstance(criteria, list):
            raise ApiFormatError("criteria must be an array")
        semantic = obj.get("semantic")
        return cls(
            goal_id=_identifier(obj.get("goal_id"), "goal_id"),
            status=_enum(
                CompletionStatus, obj.get("status"), "completion status"
            ),
            evaluated_at=_timestamp(obj.get("evaluated_at"), "evaluated_at"),
            criteria=tuple(CriterionReport.from_dict(item) for item in criteria),
            semantic=(
                SemanticAssessment.from_dict(semantic)
                if semantic is not None else None
            ),
        )


@dataclass(frozen=True)
class RuntimeOptions:
    settings_path: Optional[str] = None

    def __post_init__(self) -> None:
        if self.settings_path is not None:
            _string(self.settings_path, "settings_path")

    def to_dict(self) -> Dict[str, JSONValue]:
        return {
            "schema_version": API_SCHEMA_VERSION,
            "settings_path": self.settings_path,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "RuntimeOptions":
        obj = _request_fields(
            data,
            "runtime_options",
            allowed={"schema_version", "settings_path"},
        )
        _schema(obj, "runtime_options")
        path = obj.get("settings_path")
        return cls(_string(path, "settings_path", optional=True))


@dataclass(frozen=True)
class SessionOptions:
    workdir: str
    persistence: SessionPersistence = SessionPersistence.DEFAULT
    provider: Optional[str] = None
    model: Optional[str] = None
    judge_model: Optional[str] = None
    sandbox_mode: SandboxMode = SandboxMode.AUTO
    network_access: NetworkAccess = NetworkAccess.INHERIT

    def __post_init__(self) -> None:
        _string(self.workdir, "workdir")
        if not isinstance(self.persistence, SessionPersistence):
            raise ApiFormatError("persistence must be SessionPersistence")
        for name in ("provider", "model", "judge_model"):
            value = getattr(self, name)
            if value is not None:
                _string(value, name)
        if not isinstance(self.sandbox_mode, SandboxMode):
            raise ApiFormatError("sandbox_mode must be SandboxMode")
        if not isinstance(self.network_access, NetworkAccess):
            raise ApiFormatError("network_access must be NetworkAccess")

    def to_dict(self) -> Dict[str, JSONValue]:
        return {
            "schema_version": API_SCHEMA_VERSION,
            "workdir": self.workdir,
            "persistence": self.persistence.value,
            "provider": self.provider,
            "model": self.model,
            "judge_model": self.judge_model,
            "sandbox_mode": self.sandbox_mode.value,
            "network_access": self.network_access.value,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "SessionOptions":
        obj = _request_fields(
            data,
            "session_options",
            allowed={
                "schema_version", "workdir", "persistence", "provider", "model",
                "judge_model", "sandbox_mode", "network_access",
            },
        )
        _schema(obj, "session_options")
        return cls(
            workdir=_string(obj.get("workdir"), "workdir") or "",
            persistence=_enum(
                SessionPersistence,
                obj.get("persistence", SessionPersistence.DEFAULT.value),
                "persistence",
            ),
            provider=_string(obj.get("provider"), "provider", optional=True),
            model=_string(obj.get("model"), "model", optional=True),
            judge_model=_string(obj.get("judge_model"), "judge_model", optional=True),
            sandbox_mode=_enum(
                SandboxMode,
                obj.get("sandbox_mode", SandboxMode.AUTO.value),
                "sandbox_mode",
            ),
            network_access=_enum(
                NetworkAccess,
                obj.get("network_access", NetworkAccess.INHERIT.value),
                "network_access",
            ),
        )


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    workdir: str
    persistence: SessionPersistence
    provider: str
    model: str
    is_open: bool
    title: Optional[str] = None
    message_count: int = 0
    last_active: Optional[str] = None
    compatible: bool = True
    schema_version: Optional[int] = None

    def __post_init__(self) -> None:
        for name in ("session_id", "workdir", "provider", "model"):
            _string(getattr(self, name), name)
        if not isinstance(self.persistence, SessionPersistence):
            raise ApiFormatError("persistence must be SessionPersistence")
        _boolean(self.is_open, "is_open")
        if self.title is not None:
            _string(self.title, "title")
        _integer(self.message_count, "message_count")
        if self.last_active is not None:
            _string(self.last_active, "last_active")
        _boolean(self.compatible, "compatible")
        if self.schema_version is not None:
            _integer(self.schema_version, "schema_version", minimum=1)

    def to_dict(self) -> Dict[str, JSONValue]:
        return {
            "schema_version": API_SCHEMA_VERSION,
            "session_id": self.session_id,
            "workdir": self.workdir,
            "persistence": self.persistence.value,
            "provider": self.provider,
            "model": self.model,
            "is_open": self.is_open,
            "title": self.title,
            "message_count": self.message_count,
            "last_active": self.last_active,
            "compatible": self.compatible,
            "session_schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "SessionInfo":
        obj = _object(data, "session_info")
        _schema(obj, "session_info")
        return cls(
            session_id=_string(obj.get("session_id"), "session_id") or "",
            workdir=_string(obj.get("workdir"), "workdir") or "",
            persistence=_enum(
                SessionPersistence, obj.get("persistence"), "persistence"
            ),
            provider=_string(obj.get("provider"), "provider") or "",
            model=_string(obj.get("model"), "model") or "",
            is_open=_boolean(obj.get("is_open"), "is_open"),
            title=_string(obj.get("title"), "title", optional=True),
            message_count=_integer(
                obj.get("message_count", 0), "message_count"
            ),
            last_active=_string(
                obj.get("last_active"), "last_active", optional=True
            ),
            compatible=_boolean(obj.get("compatible", True), "compatible"),
            schema_version=(
                _integer(
                    obj.get("session_schema_version"),
                    "session_schema_version",
                    minimum=1,
                )
                if obj.get("session_schema_version") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class TurnRequest:
    text: str
    client_request_id: Optional[str] = None
    goal: Optional[GoalContract] = None

    def __post_init__(self) -> None:
        _string(self.text, "text")
        if self.client_request_id is not None:
            _string(self.client_request_id, "client_request_id")
        if self.goal is not None and not isinstance(self.goal, GoalContract):
            raise ApiFormatError("goal must be GoalContract")

    def to_dict(self) -> Dict[str, JSONValue]:
        return {
            "schema_version": API_SCHEMA_VERSION,
            "text": self.text,
            "client_request_id": self.client_request_id,
            "goal": self.goal.to_dict() if self.goal is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "TurnRequest":
        obj = _request_fields(
            data,
            "turn_request",
            allowed={"schema_version", "text", "client_request_id", "goal"},
        )
        _schema(obj, "turn_request")
        return cls(
            text=_string(obj.get("text"), "text") or "",
            client_request_id=_string(
                obj.get("client_request_id"), "client_request_id", optional=True
            ),
            goal=(
                GoalContract.from_dict(obj.get("goal"))
                if obj.get("goal") is not None else None
            ),
        )


@dataclass(frozen=True)
class TurnMetrics:
    model_calls: int = 0
    tool_calls: int = 0
    reasoning_tokens: int = 0
    model_duration_ms: float = 0.0
    duration_ms: float = 0.0

    def __post_init__(self) -> None:
        for name in ("model_calls", "tool_calls", "reasoning_tokens"):
            _integer(getattr(self, name), name)
        for name in ("model_duration_ms", "duration_ms"):
            _number(getattr(self, name), name)

    def to_dict(self) -> Dict[str, JSONValue]:
        return {
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "reasoning_tokens": self.reasoning_tokens,
            "model_duration_ms": self.model_duration_ms,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "TurnMetrics":
        obj = _object(data, "metrics")
        return cls(
            model_calls=_integer(obj.get("model_calls", 0), "model_calls"),
            tool_calls=_integer(obj.get("tool_calls", 0), "tool_calls"),
            reasoning_tokens=_integer(
                obj.get("reasoning_tokens", 0), "reasoning_tokens"
            ),
            model_duration_ms=_number(
                obj.get("model_duration_ms", 0.0), "model_duration_ms"
            ),
            duration_ms=_number(obj.get("duration_ms", 0.0), "duration_ms"),
        )


@dataclass(frozen=True)
class ErrorInfo:
    code: str
    safe_message: str
    retryable: bool = False
    session_id: Optional[str] = None
    turn_id: Optional[str] = None
    details: Dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _string(self.code, "error.code")
        _string(self.safe_message, "error.safe_message")
        _boolean(self.retryable, "error.retryable")
        for name in ("session_id", "turn_id"):
            value = getattr(self, name)
            if value is not None:
                _string(value, f"error.{name}")
        object.__setattr__(self, "details", _json_object(self.details, "error.details"))

    def to_dict(self) -> Dict[str, JSONValue]:
        return {
            "code": self.code,
            "safe_message": self.safe_message,
            "retryable": self.retryable,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "details": copy.deepcopy(self.details),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ErrorInfo":
        obj = _object(data, "error")
        return cls(
            code=_string(obj.get("code"), "error.code") or "",
            safe_message=_string(
                obj.get("safe_message"), "error.safe_message"
            ) or "",
            retryable=_boolean(obj.get("retryable", False), "error.retryable"),
            session_id=_string(
                obj.get("session_id"), "error.session_id", optional=True
            ),
            turn_id=_string(obj.get("turn_id"), "error.turn_id", optional=True),
            details=_json_object(obj.get("details", {}), "error.details"),
        )


class NovalError(Exception):
    """Safe machine-readable failure at the Application API boundary."""

    def __init__(
        self,
        code: str,
        safe_message: str,
        *,
        retryable: bool = False,
        session_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        details: Optional[Dict[str, JSONValue]] = None,
    ):
        self.info = ErrorInfo(
            code=code,
            safe_message=safe_message,
            retryable=retryable,
            session_id=session_id,
            turn_id=turn_id,
            details=details or {},
        )
        super().__init__(self.info.safe_message)

    @property
    def code(self) -> str:
        return self.info.code

    @property
    def safe_message(self) -> str:
        return self.info.safe_message

    @property
    def retryable(self) -> bool:
        return self.info.retryable

    @property
    def session_id(self) -> Optional[str]:
        return self.info.session_id

    @property
    def turn_id(self) -> Optional[str]:
        return self.info.turn_id

    @property
    def details(self) -> Dict[str, JSONValue]:
        return copy.deepcopy(self.info.details)

    def to_dict(self) -> Dict[str, JSONValue]:
        return {
            "schema_version": API_SCHEMA_VERSION,
            **self.info.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "NovalError":
        obj = _object(data, "noval_error")
        _schema(obj, "noval_error")
        info = ErrorInfo.from_dict(obj)
        return cls(
            info.code,
            info.safe_message,
            retryable=info.retryable,
            session_id=info.session_id,
            turn_id=info.turn_id,
            details=info.details,
        )


def _usage_to_dict(usage: Optional[TokenUsage]) -> Optional[Dict[str, JSONValue]]:
    if usage is None:
        return None
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "cache_hit_tokens": usage.cache_hit_tokens,
        "cache_miss_tokens": usage.cache_miss_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
    }


def _optional_usage_int(obj: Mapping[str, Any], name: str) -> Optional[int]:
    value = obj.get(name)
    return None if value is None else _integer(value, f"usage.{name}")


def _usage_from_dict(data: Any) -> Optional[TokenUsage]:
    if data is None:
        return None
    obj = _object(data, "usage")
    return TokenUsage(
        prompt_tokens=_integer(obj.get("prompt_tokens"), "usage.prompt_tokens"),
        completion_tokens=_integer(
            obj.get("completion_tokens"), "usage.completion_tokens"
        ),
        total_tokens=_integer(obj.get("total_tokens"), "usage.total_tokens"),
        cache_hit_tokens=_optional_usage_int(obj, "cache_hit_tokens"),
        cache_miss_tokens=_optional_usage_int(obj, "cache_miss_tokens"),
        reasoning_tokens=_optional_usage_int(obj, "reasoning_tokens"),
    )


@dataclass(frozen=True)
class TurnResult:
    session_id: str
    turn_id: str
    status: TurnStatus
    stop_reason: StopReason
    client_request_id: Optional[str] = None
    message: Optional[ConversationMessage] = None
    usage: Optional[TokenUsage] = None
    metrics: TurnMetrics = field(default_factory=TurnMetrics)
    error: Optional[ErrorInfo] = None
    receipts: Tuple[ActionReceipt, ...] = ()
    completion: Optional[CompletionReport] = None

    def __post_init__(self) -> None:
        _string(self.session_id, "session_id")
        _string(self.turn_id, "turn_id")
        if self.client_request_id is not None:
            _string(self.client_request_id, "client_request_id")
        if not isinstance(self.status, TurnStatus):
            raise ApiFormatError("status must be TurnStatus")
        if not isinstance(self.stop_reason, StopReason):
            raise ApiFormatError("stop_reason must be StopReason")
        if self.message is not None and not isinstance(self.message, ConversationMessage):
            raise ApiFormatError("message must be a canonical ConversationMessage")
        if self.usage is not None:
            _usage_to_dict(self.usage)
        if not isinstance(self.metrics, TurnMetrics):
            raise ApiFormatError("metrics must be TurnMetrics")
        if self.error is not None and not isinstance(self.error, ErrorInfo):
            raise ApiFormatError("error must be ErrorInfo")
        if not isinstance(self.receipts, tuple):
            raise ApiFormatError("receipts must be an immutable tuple")
        for receipt in self.receipts:
            if not isinstance(receipt, ActionReceipt):
                raise ApiFormatError("receipts items must be ActionReceipt")
        if self.completion is not None and not isinstance(
            self.completion, CompletionReport
        ):
            raise ApiFormatError("completion must be CompletionReport")

    def to_dict(self) -> Dict[str, JSONValue]:
        return {
            "schema_version": API_SCHEMA_VERSION,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "client_request_id": self.client_request_id,
            "status": self.status.value,
            "message": self.message.to_dict() if self.message is not None else None,
            "stop_reason": self.stop_reason.value,
            "usage": _usage_to_dict(self.usage),
            "metrics": self.metrics.to_dict(),
            "error": self.error.to_dict() if self.error is not None else None,
            "receipts": [receipt.to_dict() for receipt in self.receipts],
            "completion": (
                self.completion.to_dict() if self.completion is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "TurnResult":
        obj = _object(data, "turn_result")
        _schema(obj, "turn_result")
        message = obj.get("message")
        error = obj.get("error")
        receipts = obj.get("receipts", [])
        if not isinstance(receipts, list):
            raise ApiFormatError("receipts must be an array")
        completion = obj.get("completion")
        return cls(
            session_id=_string(obj.get("session_id"), "session_id") or "",
            turn_id=_string(obj.get("turn_id"), "turn_id") or "",
            client_request_id=_string(
                obj.get("client_request_id"), "client_request_id", optional=True
            ),
            status=_enum(TurnStatus, obj.get("status"), "status"),
            message=(
                ConversationMessage.from_dict(message) if message is not None else None
            ),
            stop_reason=_enum(
                StopReason, obj.get("stop_reason"), "stop_reason"
            ),
            usage=_usage_from_dict(obj.get("usage")),
            metrics=TurnMetrics.from_dict(obj.get("metrics", {})),
            error=ErrorInfo.from_dict(error) if error is not None else None,
            receipts=tuple(ActionReceipt.from_dict(item) for item in receipts),
            completion=(
                CompletionReport.from_dict(completion)
                if completion is not None else None
            ),
        )


@dataclass(frozen=True)
class RuntimeEvent:
    event_id: str
    session_id: str
    sequence: int
    timestamp: str
    type: str
    payload: Dict[str, JSONValue] = field(default_factory=dict)
    turn_id: Optional[str] = None

    def __post_init__(self) -> None:
        for name in ("event_id", "session_id", "timestamp", "type"):
            _string(getattr(self, name), name)
        if self.turn_id is not None:
            _string(self.turn_id, "turn_id")
        _integer(self.sequence, "sequence", minimum=1)
        object.__setattr__(self, "payload", _json_object(self.payload, "payload"))

    def to_dict(self) -> Dict[str, JSONValue]:
        return {
            "schema_version": API_SCHEMA_VERSION,
            "event_id": self.event_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "type": self.type,
            "payload": copy.deepcopy(self.payload),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "RuntimeEvent":
        obj = _object(data, "runtime_event")
        _schema(obj, "runtime_event")
        return cls(
            event_id=_string(obj.get("event_id"), "event_id") or "",
            session_id=_string(obj.get("session_id"), "session_id") or "",
            turn_id=_string(obj.get("turn_id"), "turn_id", optional=True),
            sequence=_integer(obj.get("sequence"), "sequence", minimum=1),
            timestamp=_string(obj.get("timestamp"), "timestamp") or "",
            type=_string(obj.get("type"), "type") or "",
            payload=_json_object(obj.get("payload", {}), "payload"),
        )


@dataclass(frozen=True)
class PermissionStateView:
    mode: PermissionMode
    approved_tools: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.mode, PermissionMode):
            raise ApiFormatError("permission mode must be PermissionMode")
        if not isinstance(self.approved_tools, tuple):
            raise ApiFormatError("approved_tools must be an immutable tuple")
        for tool_name in self.approved_tools:
            _string(tool_name, "approved_tools item")

    def to_dict(self) -> Dict[str, JSONValue]:
        return {
            "schema_version": API_SCHEMA_VERSION,
            "mode": self.mode.value,
            "approved_tools": list(self.approved_tools),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "PermissionStateView":
        obj = _object(data, "permission_state")
        _schema(obj, "permission_state")
        tools = obj.get("approved_tools", [])
        if not isinstance(tools, list):
            raise ApiFormatError("approved_tools must be an array")
        return cls(
            mode=_enum(PermissionMode, obj.get("mode"), "permission mode"),
            approved_tools=tuple(
                _string(value, "approved_tools item") or "" for value in tools
            ),
        )


@dataclass(frozen=True)
class PermissionRequest:
    request_id: str
    session_id: str
    turn_id: str
    tool_name: str
    risk: str
    arguments: Dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("request_id", "session_id", "turn_id", "tool_name", "risk"):
            _string(getattr(self, name), name)
        object.__setattr__(
            self, "arguments", _json_object(self.arguments, "arguments")
        )

    def to_dict(self) -> Dict[str, JSONValue]:
        return {
            "schema_version": API_SCHEMA_VERSION,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "tool_name": self.tool_name,
            "risk": self.risk,
            "arguments": copy.deepcopy(self.arguments),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "PermissionRequest":
        obj = _object(data, "permission_request")
        _schema(obj, "permission_request")
        return cls(
            request_id=_string(obj.get("request_id"), "request_id") or "",
            session_id=_string(obj.get("session_id"), "session_id") or "",
            turn_id=_string(obj.get("turn_id"), "turn_id") or "",
            tool_name=_string(obj.get("tool_name"), "tool_name") or "",
            risk=_string(obj.get("risk"), "risk") or "",
            arguments=_json_object(obj.get("arguments", {}), "arguments"),
        )


@dataclass(frozen=True)
class RequestInspection:
    request_id: str
    session_id: str
    purpose: str
    step: int
    timestamp: str
    provider: Dict[str, JSONValue]
    canonical_messages: Tuple[Dict[str, JSONValue], ...]
    tools: Tuple[Dict[str, JSONValue], ...]
    context: Dict[str, JSONValue] = field(default_factory=dict)
    turn_id: Optional[str] = None
    adapter_request: Optional[Dict[str, JSONValue]] = None

    def __post_init__(self) -> None:
        for name in ("request_id", "session_id", "purpose", "timestamp"):
            _string(getattr(self, name), name)
        if self.turn_id is not None:
            _string(self.turn_id, "turn_id")
        _integer(self.step, "step", minimum=1)
        object.__setattr__(self, "provider", _json_object(self.provider, "provider"))
        object.__setattr__(self, "context", _json_object(self.context, "context"))
        for name in ("canonical_messages", "tools"):
            values = getattr(self, name)
            if not isinstance(values, tuple):
                raise ApiFormatError(f"{name} must be an immutable tuple")
            object.__setattr__(
                self,
                name,
                tuple(_json_object(value, f"{name} item") for value in values),
            )
        if self.adapter_request is not None:
            object.__setattr__(
                self,
                "adapter_request",
                _json_object(self.adapter_request, "adapter_request"),
            )

    def to_dict(self) -> Dict[str, JSONValue]:
        return {
            "schema_version": API_SCHEMA_VERSION,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "purpose": self.purpose,
            "step": self.step,
            "timestamp": self.timestamp,
            "provider": copy.deepcopy(self.provider),
            "context": copy.deepcopy(self.context),
            "canonical_messages": copy.deepcopy(list(self.canonical_messages)),
            "tools": copy.deepcopy(list(self.tools)),
            "adapter_request": copy.deepcopy(self.adapter_request),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "RequestInspection":
        obj = _object(data, "request_inspection")
        _schema(obj, "request_inspection")
        messages = obj.get("canonical_messages", [])
        tools = obj.get("tools", [])
        if not isinstance(messages, list) or not isinstance(tools, list):
            raise ApiFormatError("canonical_messages and tools must be arrays")
        adapter_request = obj.get("adapter_request")
        return cls(
            request_id=_string(obj.get("request_id"), "request_id") or "",
            session_id=_string(obj.get("session_id"), "session_id") or "",
            turn_id=_string(obj.get("turn_id"), "turn_id", optional=True),
            purpose=_string(obj.get("purpose"), "purpose") or "",
            step=_integer(obj.get("step"), "step", minimum=1),
            timestamp=_string(obj.get("timestamp"), "timestamp") or "",
            provider=_json_object(obj.get("provider", {}), "provider"),
            context=_json_object(obj.get("context", {}), "context"),
            canonical_messages=tuple(
                _json_object(value, "canonical_messages item")
                for value in messages
            ),
            tools=tuple(_json_object(value, "tools item") for value in tools),
            adapter_request=(
                _json_object(adapter_request, "adapter_request")
                if adapter_request is not None else None
            ),
        )
