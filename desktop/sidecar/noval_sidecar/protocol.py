from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from . import PROTOCOL_VERSION

MAX_LINE_BYTES = 1024 * 1024


class ProtocolError(ValueError):
    def __init__(self, code: str, message: str, *, request_id: str | None = None):
        self.code = code
        self.safe_message = message
        self.request_id = request_id
        super().__init__(message)


@dataclass(frozen=True)
class Request:
    request_id: str
    method: str
    params: dict[str, Any]


def parse_request(line: bytes) -> Request:
    if len(line) > MAX_LINE_BYTES:
        raise ProtocolError("message_too_large", "Protocol message exceeds the size limit.")
    try:
        value = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProtocolError("invalid_json", "Protocol message is not valid UTF-8 JSON.") from error
    if not isinstance(value, Mapping):
        raise ProtocolError("invalid_request", "Protocol request must be a JSON object.")
    request_id = value.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        raise ProtocolError("invalid_request", "Protocol request_id must be a non-empty string.")
    if value.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError(
            "protocol_mismatch",
            f"Desktop protocol version {PROTOCOL_VERSION} is required.",
            request_id=request_id,
        )
    if value.get("kind") != "request":
        raise ProtocolError("invalid_request", "Protocol kind must be request.", request_id=request_id)
    method = value.get("method")
    params = value.get("params", {})
    if not isinstance(method, str) or not method.strip():
        raise ProtocolError("invalid_request", "Protocol method must be a non-empty string.", request_id=request_id)
    if not isinstance(params, dict):
        raise ProtocolError("invalid_request", "Protocol params must be an object.", request_id=request_id)
    return Request(request_id=request_id, method=method, params=params)


def response(request_id: str, result: Any) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "kind": "response",
        "request_id": request_id,
        "ok": True,
        "result": result,
    }


def error_response(request_id: str | None, code: str, safe_message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "kind": "response",
        "request_id": request_id,
        "ok": False,
        "error": {"code": code, "safe_message": safe_message, "retryable": retryable},
    }


def event(name: str, event_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "kind": "event",
        "event_id": event_id,
        "event": name,
        "payload": dict(payload),
    }
