"""Append-only provenance journal for reconstructing model requests."""
from __future__ import annotations

import copy
import contextvars
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Optional, Protocol, Sequence
from uuid import uuid4

from .api import RequestInspection
from .client import LLMClient, LLMResponse, ProviderIdentity, ToolDefinition
from .messages import ConversationMessage
from .runtime_log import runtime_log_context


log = logging.getLogger("noval.requests")
_CURRENT_REQUEST_ID: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "noval_request_id", default=None
)


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

    def append(self, inspection: RequestInspection) -> None:
        if inspection.session_id != self.session_id:
            raise ValueError("request journal session mismatch")
        encoded = json.dumps(
            inspection.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as file:
                file.write(encoded + "\n")
            try:
                self.path.chmod(0o600)
            except OSError:
                pass

    def get(self, request_id: str) -> Optional[RequestInspection]:
        if not self.path.exists():
            return None
        selected = None
        try:
            file = self.path.open(encoding="utf-8", errors="replace")
        except OSError:
            return None
        with file:
            for line_number, line in enumerate(file, 1):
                try:
                    raw = json.loads(line)
                    candidate = RequestInspection.from_dict(raw)
                except (json.JSONDecodeError, TypeError, ValueError):
                    log.warning(
                        "skipping corrupt request journal line %s:%s",
                        self.path,
                        line_number,
                    )
                    continue
                if candidate.request_id == request_id:
                    selected = candidate
        return selected


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
        try:
            self.journal.append(inspection)
        except Exception:
            log.warning("request provenance persistence failed", exc_info=True)
        token = _CURRENT_REQUEST_ID.set(request_id)
        try:
            with runtime_log_context(request_id=request_id):
                response = self.inner.complete(messages, tools)
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
