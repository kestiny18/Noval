"""Stable host-facing contracts for Noval's Headless/Application API."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
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

    def __post_init__(self) -> None:
        _string(self.text, "text")
        if self.client_request_id is not None:
            _string(self.client_request_id, "client_request_id")

    def to_dict(self) -> Dict[str, JSONValue]:
        return {
            "schema_version": API_SCHEMA_VERSION,
            "text": self.text,
            "client_request_id": self.client_request_id,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "TurnRequest":
        obj = _request_fields(
            data,
            "turn_request",
            allowed={"schema_version", "text", "client_request_id"},
        )
        _schema(obj, "turn_request")
        return cls(
            text=_string(obj.get("text"), "text") or "",
            client_request_id=_string(
                obj.get("client_request_id"), "client_request_id", optional=True
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
        }

    @classmethod
    def from_dict(cls, data: Any) -> "TurnResult":
        obj = _object(data, "turn_result")
        _schema(obj, "turn_result")
        message = obj.get("message")
        error = obj.get("error")
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
