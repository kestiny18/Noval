"""Append-only provenance journal for reconstructing model requests."""
from __future__ import annotations

import base64
import contextvars
import copy
import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Set
from uuid import uuid4

from .api import RequestInspection
from .client import LLMClient, LLMResponse, ProviderIdentity, ToolDefinition
from .messages import ConversationMessage
from .redaction import redact_sensitive_data
from .runtime_log import runtime_log_context


log = logging.getLogger("noval.requests")
_CURRENT_REQUEST_ID: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "noval_request_id", default=None
)
_JOURNAL_SCHEMA_VERSION = 2
_JOURNAL_MARKER = "_noval_request_journal"


def current_request_id() -> Optional[str]:
    """Return the request id visible to the innermost Provider adapter."""
    return _CURRENT_REQUEST_ID.get()


@dataclass(frozen=True)
class RequestContext:
    session_id: str
    turn_id: Optional[str]
    metadata: Dict[str, object] = field(default_factory=dict)


class RequestSequence:
    def __init__(self):
        self._steps: Dict[str, int] = {}
        self._lock = threading.Lock()

    def next(self, context: RequestContext, purpose: str) -> int:
        key = context.turn_id or f"session:{context.session_id}:{purpose}"
        with self._lock:
            step = self._steps.get(key, 0) + 1
            self._steps[key] = step
            return step


class RequestJournal(Protocol):
    def append(self, inspection: RequestInspection) -> None: ...
    def get(self, request_id: str) -> Optional[RequestInspection]: ...


class InMemoryRequestJournal:
    def __init__(self):
        self._items: Dict[str, RequestInspection] = {}
        self._lock = threading.Lock()

    def append(self, inspection: RequestInspection) -> None:
        with self._lock:
            self._items[inspection.request_id] = inspection

    def get(self, request_id: str) -> Optional[RequestInspection]:
        with self._lock:
            return self._items.get(request_id)


class JsonlRequestJournal:
    def __init__(self, path: Path, session_id: str):
        self.path = Path(path)
        self.session_id = session_id
        self._lock = threading.Lock()
        self._known_object_ids: Optional[Set[str]] = None

    def append(self, inspection: RequestInspection) -> None:
        if inspection.session_id != self.session_id:
            raise ValueError("request journal session mismatch")
        with self._lock:
            known = self._load_known_object_ids_locked()
            object_records: List[Dict[str, object]] = []
            payload = inspection.to_dict()
            canonical_messages = payload.pop("canonical_messages")
            tools = payload.pop("tools")
            adapter_request = payload.pop("adapter_request")
            record = {
                _JOURNAL_MARKER: {
                    "schema_version": _JOURNAL_SCHEMA_VERSION,
                    "type": "request",
                },
                "inspection": payload,
                "canonical_message_refs": [
                    self._reference(value, known, object_records)
                    for value in canonical_messages
                ],
                "tool_refs": [
                    self._reference(value, known, object_records)
                    for value in tools
                ],
                "adapter_request": self._compact_adapter_request(
                    adapter_request, known, object_records
                ),
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as file:
                for item in object_records:
                    file.write(_encode_json(item) + "\n")
                file.write(_encode_json(record) + "\n")
            try:
                self.path.chmod(0o600)
            except OSError:
                pass

    def get(self, request_id: str) -> Optional[RequestInspection]:
        if not self.path.exists():
            return None
        objects: Dict[str, Any] = {}
        candidates: List[object] = []
        try:
            file = self.path.open(encoding="utf-8", errors="replace")
        except OSError:
            return None
        with file:
            for line_number, line in enumerate(file, 1):
                try:
                    raw = json.loads(line)
                except (json.JSONDecodeError, TypeError, ValueError):
                    self._warn_corrupt(line_number)
                    continue
                marker = raw.get(_JOURNAL_MARKER) if isinstance(raw, dict) else None
                if isinstance(marker, dict):
                    record_type = marker.get("type")
                    if record_type == "object":
                        if _is_valid_object_record(raw):
                            objects[raw["object_id"]] = raw.get("value")
                        else:
                            self._warn_corrupt(line_number)
                    elif record_type == "request":
                        inspection = raw.get("inspection")
                        if (
                            isinstance(inspection, dict)
                            and inspection.get("request_id") == request_id
                        ):
                            candidates.append(raw)
                    else:
                        self._warn_corrupt(line_number)
                    continue
                try:
                    candidate = RequestInspection.from_dict(raw)
                except (TypeError, ValueError):
                    self._warn_corrupt(line_number)
                    continue
                if candidate.request_id == request_id:
                    candidates.append(candidate)
        for candidate in reversed(candidates):
            if isinstance(candidate, RequestInspection):
                return candidate
            try:
                return self._inflate_request(candidate, objects)
            except (KeyError, TypeError, ValueError):
                log.warning(
                    "skipping incomplete request journal record request=%s path=%s",
                    request_id,
                    self.path,
                )
        return None

    def _load_known_object_ids_locked(self) -> Set[str]:
        if self._known_object_ids is not None:
            return self._known_object_ids
        known: Set[str] = set()
        if self.path.exists():
            try:
                lines = self.path.open(encoding="utf-8", errors="replace")
            except OSError:
                lines = None
            if lines is not None:
                with lines:
                    for line in lines:
                        try:
                            raw = json.loads(line)
                        except (json.JSONDecodeError, TypeError, ValueError):
                            continue
                        marker = (
                            raw.get(_JOURNAL_MARKER)
                            if isinstance(raw, dict) else None
                        )
                        if (
                            isinstance(marker, dict)
                            and marker.get("type") == "object"
                            and _is_valid_object_record(raw)
                        ):
                            known.add(raw["object_id"])
        self._known_object_ids = known
        return known

    def _reference(
        self,
        value: Any,
        known: Set[str],
        records: List[Dict[str, object]],
    ) -> str:
        object_id = _object_id(value)
        if object_id not in known:
            records.append({
                _JOURNAL_MARKER: {
                    "schema_version": _JOURNAL_SCHEMA_VERSION,
                    "type": "object",
                },
                "object_id": object_id,
                "value": copy.deepcopy(value),
            })
            known.add(object_id)
        return object_id

    def _compact_adapter_request(
        self,
        value: object,
        known: Set[str],
        records: List[Dict[str, object]],
    ) -> Optional[Dict[str, object]]:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise TypeError("adapter request must be an object")
        static = copy.deepcopy(value)
        compact: Dict[str, object] = {}
        for field in ("messages", "tools"):
            items = static.get(field)
            if isinstance(items, list):
                del static[field]
                compact[f"{field}_refs"] = [
                    self._reference(item, known, records) for item in items
                ]
        compact["static_ref"] = self._reference(static, known, records)
        return compact

    def _inflate_request(
        self,
        record: object,
        objects: Dict[str, Any],
    ) -> RequestInspection:
        if not isinstance(record, dict):
            raise TypeError("request record must be an object")
        payload = copy.deepcopy(record["inspection"])
        if not isinstance(payload, dict):
            raise TypeError("request inspection metadata must be an object")
        payload["canonical_messages"] = [
            copy.deepcopy(objects[object_id])
            for object_id in record["canonical_message_refs"]
        ]
        payload["tools"] = [
            copy.deepcopy(objects[object_id])
            for object_id in record["tool_refs"]
        ]
        adapter = record.get("adapter_request")
        if adapter is None:
            payload["adapter_request"] = None
        else:
            if not isinstance(adapter, dict):
                raise TypeError("compact adapter request must be an object")
            inflated = copy.deepcopy(objects[adapter["static_ref"]])
            if not isinstance(inflated, dict):
                raise TypeError("adapter request static value must be an object")
            for field in ("messages", "tools"):
                refs = adapter.get(f"{field}_refs")
                if refs is not None:
                    inflated[field] = [
                        copy.deepcopy(objects[object_id])
                        for object_id in refs
                    ]
            payload["adapter_request"] = inflated
        return RequestInspection.from_dict(payload)

    def _warn_corrupt(self, line_number: int) -> None:
        log.warning(
            "skipping corrupt request journal line %s:%s",
            self.path,
            line_number,
        )


def _encode_json(value: object, *, sort_keys: bool = False) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=sort_keys,
        allow_nan=False,
    )


def _object_id(value: object) -> str:
    encoded = _encode_json(value, sort_keys=True).encode("utf-8")
    digest = base64.urlsafe_b64encode(hashlib.sha256(encoded).digest())
    return "sha256:" + digest.rstrip(b"=").decode("ascii")


def _is_valid_object_record(value: Dict[str, object]) -> bool:
    object_id = value.get("object_id")
    if not isinstance(object_id, str):
        return False
    try:
        return object_id == _object_id(value.get("value"))
    except (TypeError, ValueError):
        return False


RequestContextProvider = Callable[[], RequestContext]


class RequestRecordingClient:
    """LLMClient decorator that records safe, provider-owned request input."""

    def __init__(
        self,
        inner: LLMClient,
        journal: RequestJournal,
        context_provider: RequestContextProvider,
        *,
        purpose: str,
        identity: ProviderIdentity,
        sequence: Optional[RequestSequence] = None,
    ):
        self.inner = inner
        self.journal = journal
        self.context_provider = context_provider
        self.purpose = purpose
        self.identity = identity
        self.sequence = sequence or RequestSequence()

    def complete(
        self,
        messages: Sequence[ConversationMessage],
        tools: Sequence[ToolDefinition],
    ) -> LLMResponse:
        return self.complete_with_request(
            messages,
            tools,
            request_id="request-" + uuid4().hex,
        )

    def complete_with_request(
        self,
        messages: Sequence[ConversationMessage],
        tools: Sequence[ToolDefinition],
        *,
        request_id: str,
    ) -> LLMResponse:
        context = self.context_provider()
        step = self.sequence.next(context, self.purpose)
        adapter_request = self._render_adapter_request(messages, tools)
        inspection = RequestInspection(
            request_id=request_id,
            session_id=context.session_id,
            turn_id=context.turn_id,
            purpose=self.purpose,
            step=step,
            timestamp=datetime.now(timezone.utc).isoformat(
                timespec="milliseconds"
            ).replace("+00:00", "Z"),
            provider={
                "provider": self.identity.provider,
                "model": self.identity.model,
                "adapter": self.identity.adapter,
                "adapter_schema_version": self.identity.adapter_schema_version,
            },
            context=copy.deepcopy(context.metadata),
            canonical_messages=tuple(
                message.semantic_dict() for message in messages
            ),
            tools=tuple({
                "name": tool.name,
                "description": tool.description,
                "input_schema": copy.deepcopy(tool.input_schema),
            } for tool in tools),
            adapter_request=adapter_request,
        )
        safe_payload = redact_sensitive_data(inspection.to_dict())
        if not isinstance(safe_payload, dict):
            raise TypeError("redacted request inspection must remain an object")
        inspection = RequestInspection.from_dict(safe_payload)
        try:
            self.journal.append(inspection)
        except Exception:
            log.warning("request provenance persistence failed", exc_info=True)
        token = _CURRENT_REQUEST_ID.set(request_id)
        try:
            with runtime_log_context(request_id=request_id):
                started = time.perf_counter()
                log.info(
                    "model request started purpose=%s step=%s provider=%s "
                    "model=%s messages=%s tools=%s",
                    self.purpose,
                    step,
                    self.identity.provider,
                    self.identity.model,
                    len(messages),
                    len(tools),
                )
                try:
                    response = self.inner.complete(messages, tools)
                except Exception:
                    log.warning(
                        "model request failed purpose=%s step=%s dur=%.1fms",
                        self.purpose,
                        step,
                        (time.perf_counter() - started) * 1000,
                    )
                    raise
                log.info(
                    "model request completed purpose=%s step=%s dur=%.1fms",
                    self.purpose,
                    step,
                    (time.perf_counter() - started) * 1000,
                )
        finally:
            _CURRENT_REQUEST_ID.reset(token)
        response.meta = dict(response.meta)
        response.meta["request_id"] = request_id
        return response

    def _render_adapter_request(
        self,
        messages: Sequence[ConversationMessage],
        tools: Sequence[ToolDefinition],
    ) -> Optional[Dict[str, object]]:
        renderer = getattr(self.inner, "render_request", None)
        if renderer is None:
            return None
        try:
            rendered = renderer(messages, tools)
            json.dumps(rendered, ensure_ascii=False, allow_nan=False)
            return copy.deepcopy(rendered) if isinstance(rendered, dict) else None
        except Exception:
            log.warning("adapter request inspection rendering failed", exc_info=True)
            return None
