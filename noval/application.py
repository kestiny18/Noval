"""Embeddable multi-session Application API for Noval."""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, NoReturn, Optional, Protocol, Tuple
from uuid import uuid4

from .agent import Agent, AgentTurnOutcome, detect_environment, load_project_memory
from .api import (
    CompletionReport,
    CompletionStatus,
    ConfiguredModelInfo,
    ConfiguredModelUpsert,
    ConnectionInfo,
    ConnectionUpsert,
    ErrorInfo,
    EventPage,
    EventType,
    NovalError,
    PermissionDecision,
    PermissionRequest,
    PermissionStateView,
    ModelConfigurationInfo,
    ProviderProfileInfo,
    PersistedProjectInfo,
    RequestInspection,
    RuntimeEvent,
    RuntimeConfiguration,
    RuntimeOptions,
    SESSION_TITLE_MAX_LENGTH,
    SessionInfo,
    SessionOptions,
    SessionPersistence,
    StopReason,
    TurnMetrics,
    TurnRequest,
    TurnResult,
    TurnStatus,
    TranscriptEntry,
    TranscriptHistoryPage,
    TranscriptPage,
    TranscriptToolCall,
    TranscriptToolResult,
    VerificationResult,
)
from .client import (
    ANTHROPIC_ADAPTER,
    OPENAI_ADAPTER,
    LLMClient,
    ProviderError,
    ProviderIdentity,
    create_provider_client,
)
from .config import Config
from .context import ContextManager
from .model_config import (
    BUILTIN_PROFILE_BY_ID,
    CUSTOM_PROFILE_ID,
    OPENAI_COMPATIBLE_ADAPTER,
    Connection,
    ConfiguredModel,
    ModelConfiguration,
    ModelConfigurationError,
    ModelConfigurationStore,
    public_provider_profiles,
)
from .permissions import PermissionController, PermissionMode, PermissionState
from .process import ProcessRuntime, SandboxMode, SandboxPolicy, sandbox_status_text
from .redaction import redact_sensitive_text
from .messages import ConversationMessage, MessageRole, ReplayScope, ToolCallBlock
from .runtime_log import runtime_log_context, setup_runtime_logging
from .requests import (
    InMemoryRequestJournal,
    JsonlRequestJournal,
    RequestContext,
    RequestJournal,
    RequestRecordingClient,
    RequestSequence,
)
from .session import (
    JsonlSessionStore,
    PersistentSessionStore,
    SessionLockedError,
    SessionMetadataError,
    SessionMetadataStore,
    SessionModelSelection,
    list_persisted_projects,
    list_sessions,
)
from .shell import resolve_shell_backend
from .task import (
    CompletionVerifier,
    SemanticJudge,
    TaskContractError,
    TaskController,
    TaskEventStore,
)
from .tools import Tool, all_tools
from .turn import TurnExecution, TurnModelBinding
from .usage import JsonlUsageStore, MeteredLLMClient


log = logging.getLogger("noval.application")

_TRANSCRIPT_PAGE_LIMIT = 200
_TRANSCRIPT_ARGUMENT_KEY_LIMIT = 64
_EVENT_PAGE_LIMIT = 200
_EVENT_BUFFER_MAX_EVENTS = 512
_TURN_CONTEXT_RE = re.compile(r"^<context>.*?</context>\s*", re.DOTALL)


@dataclass(frozen=True)
class ClientSpec:
    """Native dependency-factory input; credentials never enter public DTOs."""

    purpose: str
    provider: str
    model: str
    session_id: str
    replay_scope: ReplayScope


class ClientFactory(Protocol):
    def __call__(self, spec: ClientSpec) -> LLMClient: ...


class _UnboundClient:
    """Construction placeholder; every Provider request requires Turn admission."""

    def complete(self, messages, tools):
        raise RuntimeError("model client is not bound to an admitted Turn")


EventSink = Callable[[RuntimeEvent], None]


class PermissionHandler(Protocol):
    def __call__(self, request: PermissionRequest) -> PermissionDecision: ...


def _clone_tools(tools: Iterable[Tool]) -> Tuple[Tool, ...]:
    return tuple(
        replace(tool, parameters=copy.deepcopy(tool.parameters))
        for tool in tools
    )


def _client_identity(
    client: LLMClient,
    provider: str,
    model: str,
) -> ProviderIdentity:
    candidate = client
    for _ in range(4):
        identity = getattr(candidate, "identity", None)
        if isinstance(identity, ProviderIdentity):
            return identity
        inner = getattr(candidate, "inner", None)
        if inner is None:
            break
        candidate = inner
    adapter = ANTHROPIC_ADAPTER if provider == "anthropic" else OPENAI_ADAPTER
    return ProviderIdentity(provider, model, adapter)


def _permission_controller(
    store: Optional[SessionMetadataStore],
) -> PermissionController:
    metadata = store.load_metadata() if store is not None else {}
    state = PermissionState.from_dict(metadata.get("permissions"))

    def persist(snapshot: Dict[str, object]) -> None:
        if store is not None:
            store.update_metadata({"permissions": snapshot})

    return PermissionController(
        state,
        on_change=persist if store is not None else None,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _public_stop_reason(value: str) -> StopReason:
    if value == "completed":
        return StopReason.COMPLETED
    if value in {"max_steps", "max_steps_validation_failed"}:
        return StopReason.MAX_STEPS
    if value in {"interrupted", "cancelled"}:
        return StopReason.CANCELLED
    if value == "validation_stalled":
        return StopReason.VALIDATION_STALLED
    return StopReason.ERROR


def _public_status(
    reason: StopReason,
    completion: Optional[CompletionReport] = None,
) -> TurnStatus:
    if reason is StopReason.ERROR:
        return TurnStatus.FAILED
    if completion is not None:
        return {
            CompletionStatus.COMPLETED: TurnStatus.COMPLETED,
            CompletionStatus.INCOMPLETE: TurnStatus.INCOMPLETE,
            CompletionStatus.UNCERTAIN: TurnStatus.UNCERTAIN,
        }[completion.status]
    if reason is StopReason.COMPLETED:
        return TurnStatus.COMPLETED
    return TurnStatus.STOPPED


def _redact_arguments(arguments: Dict[str, object]) -> Dict[str, object]:
    """Reuse the executor redactor while preserving a JSON object shape."""
    encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    try:
        decoded = json.loads(redact_sensitive_text(encoded))
    except json.JSONDecodeError:
        return {"argument_keys": sorted(str(key) for key in arguments)}
    return decoded if isinstance(decoded, dict) else {}


def _tool_argument_keys(arguments: str) -> Tuple[str, ...]:
    try:
        parsed = json.loads(arguments)
    except (TypeError, json.JSONDecodeError):
        return ()
    if not isinstance(parsed, dict):
        return ()
    keys = (
        str(key)[:4000]
        for key in parsed
        if str(key).strip()
    )
    return tuple(sorted(keys)[:_TRANSCRIPT_ARGUMENT_KEY_LIMIT])


def _transcript_entry(
    sequence: int,
    timestamp: Optional[str],
    message: ConversationMessage,
) -> Optional[TranscriptEntry]:
    if message.role is MessageRole.SYSTEM:
        return None
    text = message.text
    if message.role is MessageRole.USER:
        text = _TURN_CONTEXT_RE.sub("", text, count=1)
    return TranscriptEntry(
        sequence=sequence,
        timestamp=timestamp,
        role=message.role.value,
        text=text,
        tool_calls=tuple(
            TranscriptToolCall(
                call_id=call.id,
                name=call.name,
                argument_keys=_tool_argument_keys(call.arguments),
            )
            for call in message.tool_calls
        ),
        tool_results=tuple(
            TranscriptToolResult(
                call_id=result.call_id,
                content=result.content,
                is_error=result.is_error,
            )
            for result in message.tool_results
        ),
    )


def _has_incomplete_turn(messages: Iterable[ConversationMessage]) -> bool:
    """Return whether canonical history ends inside a user-initiated turn."""
    in_user_turn = False
    for message in messages:
        if message.role is MessageRole.USER:
            in_user_turn = True
        elif (
            message.role is MessageRole.ASSISTANT
            and in_user_turn
            and not message.tool_calls
        ):
            in_user_turn = False
    return in_user_turn


def _public_message(message: ConversationMessage) -> ConversationMessage:
    """Drop adapter-private state before a message crosses the host boundary."""
    blocks = tuple(
        ToolCallBlock(
            block.id,
            block.name,
            json.dumps(
                {"argument_keys": list(_tool_argument_keys(block.arguments))},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        if isinstance(block, ToolCallBlock) else block
        for block in message.blocks
    )
    return ConversationMessage(message.role, blocks)


def _observed_session_title(value: object) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    title = value.strip()
    if len(title) > SESSION_TITLE_MAX_LENGTH:
        return title[:SESSION_TITLE_MAX_LENGTH - 1] + "…"
    return title


class AgentSession:
    """One isolated live session owned by a :class:`NovalRuntime`."""

    def __init__(
        self,
        *,
        runtime: "NovalRuntime",
        info: SessionInfo,
        agent: Agent,
        store: Optional[PersistentSessionStore],
        permissions: PermissionController,
        process_runtime: ProcessRuntime,
        event_sink: Optional[EventSink],
        permission_handler: Optional[PermissionHandler],
        tool_names: Tuple[str, ...],
        request_journal: RequestJournal,
        selection: SessionModelSelection,
        session_config: Config,
    ):
        self._runtime = runtime
        self._base_info = info
        self._agent = agent
        self._store = store
        self._permissions = permissions
        self._process_runtime = process_runtime
        self._event_sink = event_sink
        self._permission_handler = permission_handler
        self._tool_names = tool_names
        self._request_journal = request_journal
        self._request_sequence = RequestSequence()
        self._selection = selection
        self._session_config = session_config
        self._state_lock = threading.RLock()
        self._closed = False
        self._active_turn_id: Optional[str] = None
        self._active_execution: Optional[TurnExecution] = None
        self._event_sequence = 0
        self._events = deque(maxlen=_EVENT_BUFFER_MAX_EVENTS)

    @property
    def info(self) -> SessionInfo:
        with self._state_lock:
            active = self._active_execution
            return replace(
                self._base_info,
                selected_model_id=self._selection.selected_model_id,
                selected_judge_model_id=(
                    self._selection.selected_judge_model_id
                ),
                active_model_id=(
                    active.agent.configured_model_id if active else None
                ),
                active_judge_model_id=(
                    active.judge.configured_model_id if active else None
                ),
                is_open=not self._closed,
            )

    def permission_state(self) -> PermissionStateView:
        with self._state_lock:
            return self._permission_state_locked()

    def select_models(
        self,
        selected_model_id: str,
        selected_judge_model_id: str,
    ) -> SessionModelSelection:
        """Select configured ids for the next Turn, including while one is active."""
        return self._runtime._select_session_models(
            self,
            selected_model_id,
            selected_judge_model_id,
        )

    def rename(self, title: str) -> SessionInfo:
        """Set bounded display metadata without rewriting conversation history."""
        if not isinstance(title, str):
            raise NovalError(
                "invalid_session_title",
                "Session title must be a string.",
                session_id=self._base_info.session_id,
            )
        normalized = title.strip()
        if not normalized or len(normalized) > SESSION_TITLE_MAX_LENGTH:
            raise NovalError(
                "invalid_session_title",
                f"Session title must contain 1 to {SESSION_TITLE_MAX_LENGTH} characters.",
                session_id=self._base_info.session_id,
            )
        with self._state_lock:
            self._require_idle_locked()
            if self._store is not None:
                self._store.update_metadata({"title": normalized})
            self._base_info = replace(self._base_info, title=normalized)
            info = replace(self._base_info, is_open=True)
            event = self._new_event_locked(
                EventType.SESSION_RENAMED.value,
                payload={"title": normalized},
            )
        self._dispatch_event(event)
        return info

    def _permission_state_locked(self) -> PermissionStateView:
        return PermissionStateView(
            mode=self._permissions.mode,
            approved_tools=tuple(sorted(self._permissions.approved_tools)),
        )

    @property
    def available_tools(self) -> Tuple[str, ...]:
        return self._tool_names

    def set_permission_handler(
        self, handler: Optional[PermissionHandler]
    ) -> None:
        with self._state_lock:
            self._require_idle_locked()
            self._permission_handler = handler

    def set_permission_mode(self, mode: PermissionMode) -> PermissionStateView:
        with self._state_lock:
            self._require_idle_locked()
            self._permissions.set_mode(mode)
            return self._permission_state_locked()

    def allow_tool(self, tool_name: str) -> PermissionStateView:
        with self._state_lock:
            self._require_idle_locked()
            self._permissions.allow_tool(tool_name)
            return self._permission_state_locked()

    def revoke_tool(self, tool_name: str) -> PermissionStateView:
        with self._state_lock:
            self._require_idle_locked()
            self._permissions.revoke_tool(tool_name)
            return self._permission_state_locked()

    def reset_permissions(self) -> PermissionStateView:
        with self._state_lock:
            self._require_idle_locked()
            self._permissions.reset()
            return self._permission_state_locked()

    def completion_report(self) -> Optional[CompletionReport]:
        with self._state_lock:
            self._require_idle_locked()
            return self._agent.completion_report()

    def transcript(
        self,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> TranscriptPage:
        """Return a bounded, safe projection of canonical conversation history."""
        if (
            not isinstance(after_sequence, int)
            or isinstance(after_sequence, bool)
            or after_sequence < 0
        ):
            raise ValueError("after_sequence must be an integer >= 0")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit < 1
            or limit > _TRANSCRIPT_PAGE_LIMIT
        ):
            raise ValueError(
                f"limit must be an integer between 1 and {_TRANSCRIPT_PAGE_LIMIT}"
            )
        with self._state_lock:
            if self._closed:
                raise NovalError(
                    "session_closed",
                    "Session is closed.",
                    session_id=self._base_info.session_id,
                )
            if self._store is not None:
                records, has_more = self._store.load_record_page(
                    after_sequence - 1,
                    limit,
                )
                page_entries = tuple(
                    entry
                    for record in records
                    if (entry := _transcript_entry(
                        record.seq + 1,
                        record.ts,
                        record.message,
                    )) is not None
                )
                return TranscriptPage(
                    entries=page_entries,
                    next_sequence=(
                        page_entries[-1].sequence
                        if page_entries else after_sequence
                    ),
                    has_more=has_more,
                )
            else:
                source = (
                    (sequence, None, message)
                    for sequence, message in enumerate(
                        (
                            item for item in self._agent.messages
                            if item.role is not MessageRole.SYSTEM
                        ),
                        start=1,
                    )
                )
            entries = []
            for sequence, timestamp, message in source:
                if sequence <= after_sequence:
                    continue
                entry = _transcript_entry(sequence, timestamp, message)
                if entry is not None:
                    entries.append(entry)
                if len(entries) > limit:
                    break
        page_entries = tuple(entries[:limit])
        next_sequence = (
            page_entries[-1].sequence if page_entries else after_sequence
        )
        return TranscriptPage(
            entries=page_entries,
            next_sequence=next_sequence,
            has_more=len(entries) > limit,
        )

    def transcript_history(
        self,
        *,
        before_sequence: Optional[int] = None,
        limit: int = 24,
    ) -> TranscriptHistoryPage:
        """Return the newest safe transcript entries before an exclusive cursor."""
        if (
            before_sequence is not None
            and (
                not isinstance(before_sequence, int)
                or isinstance(before_sequence, bool)
                or before_sequence < 1
            )
        ):
            raise ValueError("before_sequence must be null or an integer >= 1")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit < 1
            or limit > _TRANSCRIPT_PAGE_LIMIT
        ):
            raise ValueError(
                f"limit must be an integer between 1 and {_TRANSCRIPT_PAGE_LIMIT}"
            )
        with self._state_lock:
            if self._closed:
                raise NovalError(
                    "session_closed",
                    "Session is closed.",
                    session_id=self._base_info.session_id,
                )
            if self._store is not None:
                records, has_more = self._store.load_record_history(
                    before_sequence - 1 if before_sequence is not None else None,
                    limit,
                )
                page_entries = tuple(
                    entry
                    for record in records
                    if (
                        entry := _transcript_entry(
                            record.seq + 1,
                            record.ts,
                            record.message,
                        )
                    )
                    is not None
                )
            else:
                projected = tuple(
                    entry
                    for sequence, message in enumerate(
                        (
                            item
                            for item in self._agent.messages
                            if item.role is not MessageRole.SYSTEM
                        ),
                        start=1,
                    )
                    if (
                        entry := _transcript_entry(sequence, None, message)
                    )
                    is not None
                    and (
                        before_sequence is None
                        or entry.sequence < before_sequence
                    )
                )
                has_more = len(projected) > limit
                page_entries = projected[-limit:]
        return TranscriptHistoryPage(
            entries=page_entries,
            previous_sequence=(
                page_entries[0].sequence if page_entries else before_sequence
            ),
            has_more=has_more,
        )

    def replay_events(
        self,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> EventPage:
        """Replay a bounded window of observations from this live Session."""
        if (
            not isinstance(after_sequence, int)
            or isinstance(after_sequence, bool)
            or after_sequence < 0
        ):
            raise ValueError("after_sequence must be an integer >= 0")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit < 1
            or limit > _EVENT_PAGE_LIMIT
        ):
            raise ValueError(
                f"limit must be an integer between 1 and {_EVENT_PAGE_LIMIT}"
            )
        with self._state_lock:
            if self._closed:
                raise NovalError(
                    "session_closed",
                    "Session is closed.",
                    session_id=self._base_info.session_id,
                )
            retained = tuple(self._events)
        oldest = retained[0].sequence if retained else 0
        latest = retained[-1].sequence if retained else 0
        gap_detected = bool(retained and after_sequence < oldest - 1)
        candidates = tuple(
            event for event in retained if event.sequence > after_sequence
        )
        events = candidates[:limit]
        return EventPage(
            events=events,
            oldest_sequence=oldest,
            latest_sequence=latest,
            next_sequence=(events[-1].sequence if events else after_sequence),
            gap_detected=gap_detected,
            has_more=len(candidates) > limit,
        )

    def record_verification(
        self,
        verification: VerificationResult,
    ) -> CompletionReport:
        if not isinstance(verification, VerificationResult):
            raise TypeError("verification must be VerificationResult")
        with self._state_lock:
            self._require_idle_locked()
            try:
                report = self._agent.task_controller.record_verification(
                    verification
                )
            except TaskContractError as error:
                raise NovalError(
                    "verification_rejected",
                    str(error),
                    session_id=self._base_info.session_id,
                    details={
                        "goal_id": verification.goal_id,
                        "criterion_id": verification.criterion_id,
                        "verification_id": verification.verification_id,
                    },
                ) from error
            event = self._new_event_locked(
                EventType.VERIFICATION_RECORDED.value,
                payload={
                    "verification": {
                        "verification_id": verification.verification_id,
                        "goal_id": verification.goal_id,
                        "criterion_id": verification.criterion_id,
                        "source": verification.source,
                        "outcome": verification.outcome.value,
                        "observed_at": verification.observed_at,
                        "receipt_ids": list(verification.receipt_ids),
                    },
                    "completion": report.to_dict(),
                },
            )
        self._dispatch_event(event)
        return report

    def _require_idle_locked(self) -> None:
        if self._closed:
            raise NovalError(
                "session_closed",
                "Session is closed.",
                session_id=self._base_info.session_id,
            )
        if self._active_turn_id is not None:
            raise NovalError(
                "session_busy",
                "Session has an active turn.",
                retryable=True,
                session_id=self._base_info.session_id,
                details={"active_turn_id": self._active_turn_id},
            )

    def run_turn(self, request: TurnRequest) -> TurnResult:
        if not isinstance(request, TurnRequest):
            raise TypeError("request must be TurnRequest")
        turn_id = "turn-" + uuid4().hex
        execution = self._runtime._admit_turn(self, turn_id)
        started = time.perf_counter()
        log_scope = runtime_log_context(
            session_id=self._base_info.session_id,
            turn_id=turn_id,
        )
        log_scope_entered = False
        agent_turn_started = False
        try:
            self._process_runtime.begin_turn()
            log_scope.__enter__()
            log_scope_entered = True
            self._emit(
                EventType.TURN_STARTED.value,
                turn_id=turn_id,
                payload={
                    "client_request_id": request.client_request_id,
                    "goal_id": request.goal.goal_id if request.goal else None,
                    "selected_model_id": execution.agent.configured_model_id,
                    "selected_judge_model_id": execution.judge.configured_model_id,
                },
            )
            agent_turn_started = True
            outcome = self._agent.run_turn(request.text, request.goal)
            result = self._result_from_outcome(
                request, turn_id, outcome, started
            )
            self._emit(
                EventType.TURN_COMPLETED.value,
                turn_id=turn_id,
                payload={
                    "status": result.status.value,
                    "stop_reason": result.stop_reason.value,
                    "receipts": [
                        receipt.to_dict() for receipt in result.receipts
                    ],
                    "completion": (
                        result.completion.to_dict()
                        if result.completion is not None else None
                    ),
                },
            )
            return result
        except TaskContractError as error:
            result = self._failure_result(
                request,
                turn_id,
                started,
                code="goal_contract_error",
                safe_message=str(error),
                retryable=False,
                include_agent_state=agent_turn_started,
            )
            self._emit_terminal_failure(result)
            return result
        except ProviderError as error:
            result = self._failure_result(
                request,
                turn_id,
                started,
                code=f"provider_{error.kind.value}",
                safe_message=error.safe_message,
                retryable=error.retryable,
                details={
                    "provider": error.identity.provider,
                    "model": error.identity.model,
                    "adapter": error.identity.adapter,
                },
                include_agent_state=agent_turn_started,
            )
            self._emit_terminal_failure(result)
            return result
        except Exception:
            log.exception(
                "unexpected turn failure session=%s turn=%s",
                self._base_info.session_id,
                turn_id,
            )
            result = self._failure_result(
                request,
                turn_id,
                started,
                code="internal_error",
                safe_message="The turn failed because of an internal error.",
                retryable=False,
                include_agent_state=agent_turn_started,
            )
            self._emit_terminal_failure(result)
            return result
        finally:
            if log_scope_entered:
                log_scope.__exit__(None, None, None)
            with self._state_lock:
                if self._active_turn_id == turn_id:
                    self._active_turn_id = None
                    self._active_execution = None
                self._base_info = replace(
                    self._base_info,
                    message_count=sum(
                        message.role is not MessageRole.SYSTEM
                        for message in self._agent.messages
                    ),
                    last_active=_utc_now(),
                )

    def cancel_active_turn(self) -> bool:
        with self._state_lock:
            turn_id = self._active_turn_id
            if turn_id is None:
                return False
            self._process_runtime.cancel()
            self._emit(
                EventType.TURN_CANCEL_REQUESTED.value,
                turn_id=turn_id,
            )
            return True

    def _active_turn(self) -> Optional[str]:
        with self._state_lock:
            return self._active_turn_id

    def inspect_request(self, request_id: str) -> RequestInspection:
        inspection = self._request_journal.get(request_id)
        if inspection is None:
            raise NovalError(
                "request_not_found",
                "Model request was not found in this session.",
                session_id=self._base_info.session_id,
                details={"request_id": request_id},
            )
        return inspection

    def _approve(self, tool: Tool, arguments: Dict[str, object]) -> str:
        with self._state_lock:
            turn_id = self._active_turn_id
            handler = self._permission_handler
        if turn_id is None:
            return "no"
        request = PermissionRequest(
            request_id="permission-" + uuid4().hex,
            session_id=self._base_info.session_id,
            turn_id=turn_id,
            tool_name=tool.name,
            risk=tool.risk.value,
            arguments=_redact_arguments(arguments),
        )
        self._emit(
            EventType.PERMISSION_REQUESTED.value,
            turn_id=turn_id,
            payload={"request": request.to_dict()},
        )
        decision = PermissionDecision.DENY
        if handler is not None:
            try:
                candidate = handler(request)
                decision = (
                    candidate
                    if isinstance(candidate, PermissionDecision)
                    else PermissionDecision(candidate)
                )
            except Exception:
                log.warning(
                    "permission handler failed session=%s request=%s",
                    self._base_info.session_id,
                    request.request_id,
                    exc_info=True,
                )
        self._emit(
            EventType.PERMISSION_RESOLVED.value,
            turn_id=turn_id,
            payload={
                "request_id": request.request_id,
                "decision": decision.value,
            },
        )
        if decision is PermissionDecision.ALLOW_SESSION:
            return "always"
        if decision is PermissionDecision.ALLOW_ONCE:
            return "yes"
        return "no"

    def _result_from_outcome(
        self,
        request: TurnRequest,
        turn_id: str,
        outcome: AgentTurnOutcome,
        started: float,
    ) -> TurnResult:
        reason = _public_stop_reason(outcome.stop_reason)
        metrics = TurnMetrics(
            model_calls=outcome.metrics.api_calls,
            tool_calls=outcome.metrics.tool_calls,
            reasoning_tokens=outcome.metrics.reasoning_tokens,
            model_duration_ms=outcome.metrics.llm_duration_ms,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return TurnResult(
            session_id=self._base_info.session_id,
            turn_id=turn_id,
            client_request_id=request.client_request_id,
            status=_public_status(reason, outcome.completion),
            message=_public_message(outcome.message),
            stop_reason=reason,
            usage=outcome.usage,
            metrics=metrics,
            receipts=outcome.receipts,
            completion=outcome.completion,
        )

    def _failure_result(
        self,
        request: TurnRequest,
        turn_id: str,
        started: float,
        *,
        code: str,
        safe_message: str,
        retryable: bool,
        details: Optional[Dict[str, object]] = None,
        include_agent_state: bool = False,
    ) -> TurnResult:
        error = ErrorInfo(
            code=code,
            safe_message=safe_message,
            retryable=retryable,
            session_id=self._base_info.session_id,
            turn_id=turn_id,
            details=dict(details or {}),
        )
        return TurnResult(
            session_id=self._base_info.session_id,
            turn_id=turn_id,
            client_request_id=request.client_request_id,
            status=TurnStatus.FAILED,
            stop_reason=StopReason.ERROR,
            metrics=TurnMetrics(
                duration_ms=round((time.perf_counter() - started) * 1000, 1)
            ),
            error=error,
            receipts=(
                self._agent.current_turn_receipts()
                if include_agent_state else ()
            ),
            completion=(
                self._agent.completion_report()
                if include_agent_state else None
            ),
        )

    def _emit_terminal_failure(self, result: TurnResult) -> None:
        self._emit(
            EventType.TURN_FAILED.value,
            turn_id=result.turn_id,
            payload={
                "status": result.status.value,
                "stop_reason": result.stop_reason.value,
                "error": result.error.to_dict() if result.error else None,
                "receipts": [
                    receipt.to_dict() for receipt in result.receipts
                ],
                "completion": (
                    result.completion.to_dict()
                    if result.completion is not None else None
                ),
            },
        )

    def _observe_agent(self, event_type: str, payload: Dict[str, object]) -> None:
        with self._state_lock:
            turn_id = self._active_turn_id
        self._emit(event_type, turn_id=turn_id, payload=payload)

    def _emit(
        self,
        event_type: str,
        *,
        payload: Optional[Dict[str, object]] = None,
        turn_id: Optional[str] = None,
    ) -> None:
        with self._state_lock:
            event = self._new_event_locked(
                event_type,
                payload=payload,
                turn_id=turn_id,
            )
        self._dispatch_event(event)

    def _new_event_locked(
        self,
        event_type: str,
        *,
        payload: Optional[Dict[str, object]] = None,
        turn_id: Optional[str] = None,
    ) -> RuntimeEvent:
        self._event_sequence += 1
        event = RuntimeEvent(
            event_id="event-" + uuid4().hex,
            session_id=self._base_info.session_id,
            turn_id=turn_id,
            sequence=self._event_sequence,
            timestamp=_utc_now(),
            type=event_type,
            payload=dict(payload or {}),
        )
        self._events.append(event)
        return event

    def _dispatch_event(self, event: RuntimeEvent) -> None:
        sink = self._event_sink
        if sink is None:
            return
        try:
            sink(event)
        except Exception:
            log.warning(
                "runtime event sink failed type=%s", event.type, exc_info=True
            )

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            if self._active_turn_id is not None:
                raise NovalError(
                    "session_busy",
                    "Session has an active turn and cannot be closed.",
                    retryable=True,
                    session_id=self._base_info.session_id,
                    details={"active_turn_id": self._active_turn_id},
                )
            self._closed = True
        if self._store is not None:
            self._store.close()
        self._emit(EventType.SESSION_CLOSED.value)
        self._runtime._session_closed(self)

    def __enter__(self) -> "AgentSession":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


class NovalRuntime:
    """Process-scoped owner of immutable defaults and isolated live sessions."""

    def __init__(
        self,
        config: Config,
        *,
        client_factory: Optional[ClientFactory] = None,
        tools: Optional[Iterable[Tool]] = None,
        event_sink: Optional[EventSink] = None,
        configure_logging: bool = False,
    ):
        self._config = copy.deepcopy(config)
        self._model_store: Optional[ModelConfigurationStore] = None
        if (
            self._config.model_configuration is not None
            and self._config.settings_path_setting
        ):
            self._model_store = ModelConfigurationStore(
                Path(self._config.settings_path_setting)
            )
            self._config = replace(
                self._config,
                model_configuration=self._model_store.snapshot(),
            )
        self._tool_catalog = _clone_tools(tools if tools is not None else all_tools())
        self._uses_default_client_factory = client_factory is None
        self._client_factory = client_factory
        self._event_sink = event_sink
        self._sessions: Dict[str, AgentSession] = {}
        self._lock = threading.RLock()
        self._transport_lock = threading.RLock()
        self._transport_pool: Dict[Tuple[object, ...], object] = {}
        self._credential_epoch = uuid4().hex
        self._closed = False
        self.log_path = (
            setup_runtime_logging(self._config)
            if configure_logging else None
        )

    @classmethod
    def from_settings(
        cls,
        options: Optional[RuntimeOptions] = None,
        **kwargs,
    ) -> "NovalRuntime":
        selected = options or RuntimeOptions()
        path = Path(selected.settings_path) if selected.settings_path else None
        return cls(Config.load(path), **kwargs)

    def create_session(
        self,
        options: SessionOptions,
        *,
        event_sink: Optional[EventSink] = None,
        permission_handler: Optional[PermissionHandler] = None,
    ) -> AgentSession:
        return self._open_session(
            options,
            session_id=None,
            event_sink=event_sink,
            permission_handler=permission_handler,
        )

    def resume_session(
        self,
        session_id: str,
        options: SessionOptions,
        *,
        event_sink: Optional[EventSink] = None,
        permission_handler: Optional[PermissionHandler] = None,
    ) -> AgentSession:
        if options.persistence is SessionPersistence.EPHEMERAL:
            raise NovalError(
                "invalid_session_options",
                "An ephemeral session cannot be resumed.",
            )
        return self._open_session(
            options,
            session_id=session_id,
            event_sink=event_sink,
            permission_handler=permission_handler,
        )

    def _open_session(
        self,
        options: SessionOptions,
        *,
        session_id: Optional[str],
        event_sink: Optional[EventSink],
        permission_handler: Optional[PermissionHandler],
    ) -> AgentSession:
        if not isinstance(options, SessionOptions):
            raise TypeError("options must be SessionOptions")
        with self._lock:
            if self._closed:
                raise NovalError("runtime_closed", "Runtime is closed.")
            if session_id is not None and session_id in self._sessions:
                raise NovalError(
                    "session_already_open",
                    "Session is already open in this runtime.",
                    session_id=session_id,
                )

        workdir = Path(options.workdir).expanduser().resolve()
        if not workdir.is_dir():
            raise NovalError(
                "invalid_workdir",
                "Session workdir must be an existing directory.",
                details={"workdir": str(workdir)},
            )
        persistence = options.persistence
        if persistence is SessionPersistence.DEFAULT:
            persistence = (
                SessionPersistence.PERSISTENT
                if self._config.persist_sessions
                else SessionPersistence.EPHEMERAL
            )
        model_configuration = self._config.model_configuration
        if model_configuration is None:
            selected_model_id = (
                options.selected_model_id or self._config.model
            )
            selected_judge_model_id = (
                options.selected_judge_model_id or self._config.judge_model
            )
            provider = self._config.provider
            model = selected_model_id
            judge_model = selected_judge_model_id
        else:
            selected_model_id = (
                options.selected_model_id
                or model_configuration.default_model_id
            )
            if options.selected_judge_model_id is not None:
                selected_judge_model_id = options.selected_judge_model_id
            else:
                judge_matches = [
                    configured.id
                    for configured in model_configuration.configured
                    if configured.model == self._config.judge_model
                ]
                selected_judge_model_id = (
                    judge_matches[0]
                    if len(judge_matches) == 1
                    else selected_model_id
                )
            try:
                selected_model = model_configuration.configured_model(
                    selected_model_id
                )
                selected_judge = model_configuration.configured_model(
                    selected_judge_model_id
                )
                selected_connection = model_configuration.connection(
                    selected_model.connection_id
                )
                judge_connection = model_configuration.connection(
                    selected_judge.connection_id
                )
            except ModelConfigurationError as error:
                raise NovalError(
                    "configured_model_unavailable",
                    "The selected Configured Model does not exist.",
                ) from error
            if selected_connection.adapter != judge_connection.adapter:
                raise NovalError(
                    "configured_model_adapter_mismatch",
                    "Agent and judge Configured Models must use the same Adapter.",
                )
            provider = selected_connection.adapter
            model = selected_model.model
            judge_model = selected_judge.model
        configuration_revision = (
            model_configuration.revision
            if model_configuration is not None
            else 1
        )
        session_selection = SessionModelSelection(
            selected_model_id=selected_model_id,
            selected_judge_model_id=selected_judge_model_id,
            configuration_revision=configuration_revision,
        )

        store: Optional[PersistentSessionStore]
        session_title: Optional[str] = None
        if persistence is SessionPersistence.PERSISTENT:
            if session_id is None:
                store = JsonlSessionStore.create(
                    self._config.sessions_dir(), workdir, model
                )
            else:
                try:
                    store = JsonlSessionStore.open(
                        self._config.sessions_dir(), workdir, session_id, model
                    )
                except SessionLockedError as error:
                    raise NovalError(
                        "session_locked",
                        str(error),
                        retryable=True,
                        session_id=session_id,
                    ) from error
                except (FileNotFoundError, ValueError) as error:
                    raise NovalError(
                        "session_not_found",
                        str(error),
                        session_id=session_id,
                    ) from error
            resolved_session_id = store.session_id
            store_metadata = store.load_metadata()
            session_title = _observed_session_title(store_metadata.get("title"))
            if session_id is not None:
                try:
                    selection = store.load_model_selection()
                except SessionMetadataError as error:
                    raise NovalError(
                        "session_model_selection_invalid",
                        str(error),
                        session_id=session_id,
                    ) from error
                selected_model_id = selection.selected_model_id
                selected_judge_model_id = selection.selected_judge_model_id
                configuration_revision = selection.configuration_revision
                session_selection = selection
                if model_configuration is not None:
                    selected_model = model_configuration.configured_model(
                        selected_model_id
                    )
                    selected_judge = model_configuration.configured_model(
                        selected_judge_model_id
                    )
                    selected_connection = model_configuration.connection(
                        selected_model.connection_id
                    )
                    judge_connection = model_configuration.connection(
                        selected_judge.connection_id
                    )
                    if selected_connection.adapter != judge_connection.adapter:
                        raise NovalError(
                            "session_model_selection_invalid",
                            "Agent and judge Configured Models must use the same Adapter.",
                            session_id=session_id,
                        )
                    provider = selected_connection.adapter
                    model = selected_model.model
                    judge_model = selected_judge.model
                else:
                    model = selected_model_id
                    judge_model = selected_judge_model_id
        else:
            if session_id is not None:
                raise NovalError(
                    "invalid_session_options",
                    "Only persistent sessions can be resumed.",
                    session_id=session_id,
                )
            store = None
            resolved_session_id = "session-" + uuid4().hex

        session_config = replace(
            copy.deepcopy(self._config),
            provider=provider,
            model=model,
            judge_model=judge_model,
        )
        if store is not None and session_id is None:
            store.set_model_selection(session_selection)

        with self._lock:
            if resolved_session_id in self._sessions:
                if store is not None:
                    store.close()
                raise NovalError(
                    "session_already_open",
                    "Session is already open in this runtime.",
                    session_id=resolved_session_id,
                )

        policy = SandboxPolicy.workspace(
            workdir,
            mode=options.sandbox_mode,
            network=options.network_access,
        )
        process_runtime = ProcessRuntime(policy=policy)
        if policy.mode is SandboxMode.REQUIRED and not process_runtime.status.is_hard:
            if store is not None:
                store.close()
            raise NovalError(
                "sandbox_unavailable",
                sandbox_status_text(process_runtime),
                session_id=resolved_session_id,
            )
        shell_backend = resolve_shell_backend(process_runtime)
        permissions = _permission_controller(store)
        request_journal: RequestJournal = (
            JsonlRequestJournal(store.request_path(), resolved_session_id)
            if store is not None else InMemoryRequestJournal()
        )
        session_holder: Dict[str, AgentSession] = {}
        agent_client: LLMClient = _UnboundClient()
        judge_client: LLMClient = _UnboundClient()

        resume_messages = None
        recover_interrupted_turn = False
        resumed_message_count = 0
        context_manager = None
        if store is not None:
            context_manager = ContextManager(
                agent_client,
                store,
                model,
                session_config.context_budget_tokens,
            )
            if session_id is not None:
                persisted_records = store.load_records()
                recover_interrupted_turn = _has_incomplete_turn(
                    record.message for record in persisted_records
                )
                resume_messages = context_manager.restore()
                resumed_message_count = len(persisted_records)
        task_store = TaskEventStore(store.task_path()) if store is not None else None
        task_controller = TaskController(
            event_store=task_store,
            completion_verifier=CompletionVerifier(
                SemanticJudge(judge_client, model=judge_model)
            ),
        )
        info = SessionInfo(
            session_id=resolved_session_id,
            workdir=str(workdir),
            persistence=persistence,
            selected_model_id=selected_model_id,
            selected_judge_model_id=selected_judge_model_id,
            is_open=True,
            title=session_title,
            message_count=resumed_message_count,
        )
        selected_sink = event_sink if event_sink is not None else self._event_sink

        def observer(event_type: str, payload: Dict[str, object]) -> None:
            session_holder["session"]._observe_agent(event_type, payload)

        def approver(tool: Tool, arguments: Dict[str, object]) -> str:
            return session_holder["session"]._approve(tool, arguments)

        agent = Agent(
            agent_client,
            session_config,
            tools=list(_clone_tools(self._tool_catalog)),
            workdir=str(workdir),
            env_context=detect_environment(
                workdir, shell_backend, process_runtime
            ),
            project_memory=load_project_memory(workdir),
            store=store,
            resume_messages=resume_messages,
            recover_interrupted_turn=recover_interrupted_turn,
            shell_backend=shell_backend,
            permissions=permissions,
            process_runtime=process_runtime,
            context_manager=context_manager,
            task_controller=task_controller,
            observer=observer,
            approver=approver,
        )
        session = AgentSession(
            runtime=self,
            info=info,
            agent=agent,
            store=store,
            permissions=permissions,
            process_runtime=process_runtime,
            event_sink=selected_sink,
            permission_handler=permission_handler,
            tool_names=tuple(tool.name for tool in self._tool_catalog),
            request_journal=request_journal,
            selection=session_selection,
            session_config=session_config,
        )
        session_holder["session"] = session
        with self._lock:
            if self._closed:
                session.close()
                raise NovalError("runtime_closed", "Runtime is closed.")
            self._sessions[resolved_session_id] = session
        session._emit(
            EventType.SESSION_OPENED.value,
            payload={"session": session.info.to_dict()},
        )
        return session

    def _make_client(
        self,
        purpose: str,
        provider: str,
        model: str,
        session_id: str,
        config: Config,
        replay_scope: ReplayScope,
    ) -> LLMClient:
        if self._uses_default_client_factory:
            return self._default_client(
                purpose, config, provider, model, replay_scope
            )
        assert self._client_factory is not None
        return self._client_factory(ClientSpec(
            purpose=purpose,
            provider=provider,
            model=model,
            session_id=session_id,
            replay_scope=replay_scope,
        ))

    def _default_client(
        self,
        purpose: str,
        config: Config,
        provider: str,
        model: str,
        replay_scope: ReplayScope,
    ) -> LLMClient:
        if config.model_configuration is None:
            api_key = config.resolve_api_key()
            base_url = config.base_url
        else:
            connection = config.model_configuration.connection(
                replay_scope.connection_id
            )
            api_key = connection.api_key
            if not api_key and connection.api_key_env:
                api_key = os.environ.get(connection.api_key_env, "")
            if not api_key:
                raise NovalError(
                    "credential_unavailable",
                    "The selected Connection has no available credential.",
                )
            provider = connection.adapter
            base_url = connection.base_url
        transport_key: Tuple[object, ...] = (
            purpose,
            provider,
            replay_scope.connection_id,
            replay_scope.transport_revision,
            replay_scope.credential_epoch,
            base_url,
            config.anthropic_base_url,
            config.request_timeout_seconds,
            config.request_max_retries,
            config.anthropic_max_tokens,
        )
        with self._transport_lock:
            transport = self._transport_pool.get(transport_key)
            client = create_provider_client(
                provider,
                api_key=api_key,
                model=model,
                base_url=base_url,
                anthropic_base_url=config.anthropic_base_url,
                timeout=config.request_timeout_seconds,
                max_retries=config.request_max_retries,
                anthropic_max_tokens=config.anthropic_max_tokens,
                replay_scope=replay_scope,
                transport=transport,
            )
            if transport is None:
                created = getattr(client, "_client", None)
                if created is not None:
                    self._transport_pool[transport_key] = created
            return client

    def get_session(self, session_id: str) -> AgentSession:
        with self._lock:
            try:
                return self._sessions[session_id]
            except KeyError as error:
                raise NovalError(
                    "session_not_open",
                    "Session is not open in this runtime.",
                    session_id=session_id,
                ) from error

    def inspect_request(
        self, session_id: str, request_id: str
    ) -> RequestInspection:
        return self.get_session(session_id).inspect_request(request_id)

    def list_active_sessions(self) -> Tuple[SessionInfo, ...]:
        with self._lock:
            return tuple(session.info for session in self._sessions.values())

    def list_provider_profiles(self) -> Tuple[ProviderProfileInfo, ...]:
        return tuple(
            ProviderProfileInfo.from_dict(profile)
            for profile in public_provider_profiles()
        )

    def get_model_configuration(self) -> ModelConfigurationInfo:
        return self.configuration().models

    def _require_model_store(self) -> ModelConfigurationStore:
        if self._model_store is None:
            raise NovalError(
                "model_configuration_read_only",
                "This Runtime was not created from a settings schema-v2 file.",
            )
        return self._model_store

    def _install_model_configuration(
        self, configuration: ModelConfiguration
    ) -> ModelConfigurationInfo:
        default = configuration.configured_model(
            configuration.default_model_id
        )
        connection = configuration.connection(default.connection_id)
        judge_model = default.model
        profile = BUILTIN_PROFILE_BY_ID.get(connection.profile_id)
        if profile is not None:
            candidates = [
                configured
                for configured in configuration.configured
                if configured.connection_id == connection.id
                and configured.model == profile.judge_model
            ]
            if len(candidates) == 1:
                judge_model = candidates[0].model
        with self._lock:
            self._config = replace(
                self._config,
                model_configuration=configuration,
                provider=connection.adapter,
                model=default.model,
                judge_model=judge_model,
                base_url=connection.base_url,
                api_key_env=connection.api_key_env,
                api_key=connection.api_key,
            )
        return self.configuration().models

    @staticmethod
    def _raise_model_configuration_error(
        error: ModelConfigurationError,
    ) -> NoReturn:
        raise NovalError(
            error.code,
            error.safe_message,
            retryable=error.code
            in {"settings_busy", "configuration_conflict"},
            details={"path": error.path},
        ) from error

    def upsert_connection(
        self, request: ConnectionUpsert
    ) -> ModelConfigurationInfo:
        if not isinstance(request, ConnectionUpsert):
            raise TypeError("request must be ConnectionUpsert")
        store = self._require_model_store()
        current = store.snapshot()
        existing = None
        if request.connection_id is not None:
            existing = next(
                (
                    connection
                    for connection in current.connections
                    if connection.id == request.connection_id
                ),
                None,
            )
            if existing is None:
                raise NovalError(
                    "connection_not_found",
                    "The requested Connection does not exist.",
                )
            if request.expected_connection_revision != existing.revision:
                raise NovalError(
                    "connection_conflict",
                    "The Connection changed after it was read.",
                    retryable=True,
                )
        connection_id = request.connection_id or str(uuid4())
        profile = BUILTIN_PROFILE_BY_ID.get(request.profile_id)
        if profile is not None:
            adapter = profile.adapter
            base_url = profile.base_url
            api_key_env = profile.api_key_env
        elif request.profile_id == CUSTOM_PROFILE_ID:
            adapter = OPENAI_COMPATIBLE_ADAPTER
            base_url = request.base_url or (
                existing.base_url if existing is not None else ""
            )
            api_key_env = (
                request.api_key_env
                if request.api_key_env is not None
                else (existing.api_key_env if existing is not None else "")
            )
        else:
            raise NovalError(
                "unknown_profile",
                "The requested Provider Profile does not exist.",
            )
        api_key = existing.api_key if existing is not None else ""
        if request.clear_api_key:
            api_key = ""
        elif request.api_key is not None:
            api_key = request.api_key
        candidate = Connection(
            id=connection_id,
            revision=existing.revision if existing is not None else 1,
            label=request.label,
            profile_id=request.profile_id,
            adapter=adapter,
            base_url=base_url,
            api_key_env=api_key_env,
            api_key=api_key,
        )
        try:
            updated = store.upsert_connection(
                candidate,
                expected_revision=request.expected_configuration_revision,
            )
        except ModelConfigurationError as error:
            self._raise_model_configuration_error(error)
        return self._install_model_configuration(updated)

    def delete_connection(
        self,
        connection_id: str,
        *,
        expected_configuration_revision: int,
    ) -> ModelConfigurationInfo:
        store = self._require_model_store()
        try:
            updated = store.delete_connection(
                connection_id,
                expected_revision=expected_configuration_revision,
            )
        except ModelConfigurationError as error:
            self._raise_model_configuration_error(error)
        return self._install_model_configuration(updated)

    def upsert_configured_model(
        self, request: ConfiguredModelUpsert
    ) -> ModelConfigurationInfo:
        if not isinstance(request, ConfiguredModelUpsert):
            raise TypeError("request must be ConfiguredModelUpsert")
        store = self._require_model_store()
        candidate = ConfiguredModel(
            id=request.configured_model_id or str(uuid4()),
            label=request.label,
            connection_id=request.connection_id,
            model=request.model,
        )
        try:
            updated = store.upsert_configured_model(
                candidate,
                expected_revision=request.expected_configuration_revision,
            )
        except ModelConfigurationError as error:
            self._raise_model_configuration_error(error)
        return self._install_model_configuration(updated)

    def delete_configured_model(
        self,
        configured_model_id: str,
        *,
        expected_configuration_revision: int,
    ) -> ModelConfigurationInfo:
        store = self._require_model_store()
        try:
            updated = store.delete_configured_model(
                configured_model_id,
                expected_revision=expected_configuration_revision,
            )
        except ModelConfigurationError as error:
            self._raise_model_configuration_error(error)
        return self._install_model_configuration(updated)

    def set_default_model(
        self,
        configured_model_id: str,
        *,
        expected_configuration_revision: int,
    ) -> ModelConfigurationInfo:
        store = self._require_model_store()
        try:
            updated = store.set_default_model(
                configured_model_id,
                expected_revision=expected_configuration_revision,
            )
        except ModelConfigurationError as error:
            self._raise_model_configuration_error(error)
        return self._install_model_configuration(updated)

    def configuration(self) -> RuntimeConfiguration:
        """Return the effective Runtime configuration without credential values."""
        configuration = self._config.model_configuration
        if configuration is None:
            models = ModelConfigurationInfo(
                revision=1,
                connections=(
                    ConnectionInfo(
                        id=f"runtime-{self._config.provider}",
                        revision=1,
                        label="Runtime default",
                        profile_id="custom",
                        adapter=self._config.provider,
                        base_url=self._config.base_url,
                        api_key_env=self._config.api_key_env,
                        api_key_configured=bool(self._config.api_key),
                        credential_available=self._config.api_key_configured(),
                    ),
                ),
                configured=tuple(
                    ConfiguredModelInfo(
                        id=model_name,
                        label=model_name,
                        connection_id=f"runtime-{self._config.provider}",
                        model=model_name,
                    )
                    for model_name in dict.fromkeys(
                        (self._config.model, self._config.judge_model)
                    )
                ),
                default_model_id=self._config.model,
            )
        else:
            models = ModelConfigurationInfo(
                revision=configuration.revision,
                connections=tuple(
                    ConnectionInfo.from_dict(connection.public_dict())
                    for connection in configuration.connections
                ),
                configured=tuple(
                    ConfiguredModelInfo.from_dict(configured.public_dict())
                    for configured in configuration.configured
                ),
                default_model_id=configuration.default_model_id,
            )
        return RuntimeConfiguration(
            models=models,
            max_steps=self._config.max_steps,
            max_tool_output_chars=self._config.max_tool_output_chars,
            persist_sessions=self._config.persist_sessions,
        )

    def list_persisted_projects(self) -> Tuple[PersistedProjectInfo, ...]:
        """Project inventory projected from canonical Session storage."""
        return tuple(
            PersistedProjectInfo(
                workdir=project.workdir,
                created_at=project.created_at,
                session_count=project.session_count,
                available=project.available,
            )
            for project in list_persisted_projects(self._config.sessions_dir())
        )

    def list_persisted_sessions(self, workdir: str) -> Tuple[SessionInfo, ...]:
        root = Path(workdir).expanduser().resolve()
        with self._lock:
            open_sessions = {
                session_id: session.info
                for session_id, session in self._sessions.items()
            }
        return tuple(
            SessionInfo(
                session_id=meta.session_id,
                workdir=str(root),
                persistence=SessionPersistence.PERSISTENT,
                selected_model_id=meta.selected_model_id,
                selected_judge_model_id=meta.selected_judge_model_id,
                active_model_id=(
                    open_sessions[meta.session_id].active_model_id
                    if meta.session_id in open_sessions
                    else None
                ),
                active_judge_model_id=(
                    open_sessions[meta.session_id].active_judge_model_id
                    if meta.session_id in open_sessions
                    else None
                ),
                is_open=meta.session_id in open_sessions,
                title=_observed_session_title(meta.title),
                message_count=meta.message_count,
                last_active=meta.last_active,
            )
            for meta in list_sessions(self._config.sessions_dir(), root)
        )

    def _session_closed(self, session: AgentSession) -> None:
        with self._lock:
            current = self._sessions.get(session.info.session_id)
            if current is session:
                del self._sessions[session.info.session_id]

    def _select_session_models(
        self,
        session: AgentSession,
        selected_model_id: str,
        selected_judge_model_id: str,
    ) -> SessionModelSelection:
        with self._lock:
            configuration = self._config.model_configuration
            if configuration is None:
                raise NovalError(
                    "model_configuration_unavailable",
                    "This Runtime was not created from settings schema v2.",
                    session_id=session.info.session_id,
                )
            try:
                agent = configuration.configured_model(selected_model_id)
                judge = configuration.configured_model(selected_judge_model_id)
                agent_connection = configuration.connection(agent.connection_id)
                judge_connection = configuration.connection(judge.connection_id)
            except ModelConfigurationError as error:
                raise NovalError(
                    "configured_model_unavailable",
                    "The selected Configured Model does not exist.",
                    session_id=session.info.session_id,
                ) from error
            if agent_connection.adapter != judge_connection.adapter:
                raise NovalError(
                    "configured_model_adapter_mismatch",
                    "Agent and judge Configured Models must use the same Adapter.",
                    session_id=session.info.session_id,
                )
            selection = SessionModelSelection(
                selected_model_id=agent.id,
                selected_judge_model_id=judge.id,
                configuration_revision=configuration.revision,
            )
            with session._state_lock:
                if session._closed:
                    raise NovalError(
                        "session_closed",
                        "Session is closed.",
                        session_id=session.info.session_id,
                    )
                if session._store is not None:
                    session._store.set_model_selection(selection)
                session._selection = selection
                return selection

    def _turn_binding(
        self,
        *,
        configuration,
        configured_model_id: str,
        legacy_provider: str,
    ) -> Tuple[TurnModelBinding, str]:
        if configuration is None:
            replay_adapter = (
                ANTHROPIC_ADAPTER
                if legacy_provider == "anthropic"
                else OPENAI_ADAPTER
            )
            scope = ReplayScope(
                adapter=replay_adapter,
                connection_id=f"runtime-{legacy_provider}",
                configured_model_id=configured_model_id,
                provider_model=configured_model_id,
                transport_revision=1,
                adapter_schema_version=1,
                credential_epoch=self._credential_epoch,
            )
            return (
                TurnModelBinding(
                    configured_model_id=configured_model_id,
                    connection_id=scope.connection_id,
                    adapter=legacy_provider,
                    provider_model=configured_model_id,
                    transport_revision=1,
                    configuration_revision=1,
                    replay_scope=scope,
                ),
                legacy_provider,
            )
        configured = configuration.configured_model(configured_model_id)
        connection = configuration.connection(configured.connection_id)
        replay_adapter = (
            ANTHROPIC_ADAPTER
            if connection.adapter == "anthropic"
            else OPENAI_ADAPTER
        )
        credential_epoch = (
            self._credential_epoch
            if connection.api_key_env and not connection.api_key
            else f"stored-r{connection.revision}"
        )
        scope = ReplayScope(
            adapter=replay_adapter,
            connection_id=connection.id,
            configured_model_id=configured.id,
            provider_model=configured.model,
            transport_revision=connection.revision,
            adapter_schema_version=1,
            credential_epoch=credential_epoch,
        )
        return (
            TurnModelBinding(
                configured_model_id=configured.id,
                connection_id=connection.id,
                adapter=connection.adapter,
                provider_model=configured.model,
                transport_revision=connection.revision,
                configuration_revision=configuration.revision,
                replay_scope=scope,
            ),
            connection.adapter,
        )

    def _request_context_for(self, session: AgentSession) -> RequestContext:
        metadata: Dict[str, object] = {}
        manager = session._agent.context_manager
        checkpoint = manager.checkpoint if manager is not None else None
        if checkpoint is not None:
            metadata = {
                "checkpoint_id": checkpoint.checkpoint_id,
                "source_through_seq": checkpoint.source_through_seq,
            }
        return RequestContext(
            session_id=session.info.session_id,
            turn_id=session._active_turn(),
            metadata=metadata,
        )

    def _record_turn_client(
        self,
        session: AgentSession,
        client: LLMClient,
        binding: TurnModelBinding,
        provider: str,
        purpose: str,
    ) -> LLMClient:
        decorated = client
        if session._session_config.persist_usage:
            decorated = MeteredLLMClient(
                decorated,
                JsonlUsageStore(
                    session._session_config.usage_dir(),
                    session.info.session_id,
                ),
                binding.provider_model,
                purpose=purpose,
            )
        return RequestRecordingClient(
            decorated,
            session._request_journal,
            lambda: self._request_context_for(session),
            purpose=purpose,
            identity=_client_identity(
                decorated, provider, binding.provider_model
            ),
            sequence=session._request_sequence,
        )

    def _build_turn_execution(
        self,
        session: AgentSession,
        selection: SessionModelSelection,
        configuration,
    ) -> TurnExecution:
        agent_binding, agent_provider = self._turn_binding(
            configuration=configuration,
            configured_model_id=selection.selected_model_id,
            legacy_provider=session._session_config.provider,
        )
        judge_binding, judge_provider = self._turn_binding(
            configuration=configuration,
            configured_model_id=selection.selected_judge_model_id,
            legacy_provider=session._session_config.provider,
        )
        if agent_binding.replay_scope.adapter != judge_binding.replay_scope.adapter:
            raise NovalError(
                "configured_model_adapter_mismatch",
                "Agent and judge Configured Models must use the same Adapter.",
                session_id=session.info.session_id,
            )
        turn_config = replace(
            copy.deepcopy(session._session_config),
            provider=agent_provider,
            model=agent_binding.provider_model,
            judge_model=judge_binding.provider_model,
            model_configuration=configuration,
        )
        agent_client = self._make_client(
            "agent",
            agent_provider,
            agent_binding.provider_model,
            session.info.session_id,
            turn_config,
            agent_binding.replay_scope,
        )
        judge_client = self._make_client(
            "completion_judge",
            judge_provider,
            judge_binding.provider_model,
            session.info.session_id,
            turn_config,
            judge_binding.replay_scope,
        )
        return TurnExecution(
            agent=agent_binding,
            judge=judge_binding,
            agent_client=self._record_turn_client(
                session,
                agent_client,
                agent_binding,
                agent_provider,
                "agent",
            ),
            judge_client=self._record_turn_client(
                session,
                judge_client,
                judge_binding,
                judge_provider,
                "completion_judge",
            ),
        )

    def _admit_turn(
        self, session: AgentSession, turn_id: str
    ) -> TurnExecution:
        # Runtime shutdown and Session turn admission share this lock order.
        with self._lock:
            with session._state_lock:
                session._require_idle_locked()
                if self._closed:
                    raise NovalError(
                        "runtime_closed",
                        "Runtime is closed.",
                        session_id=session.info.session_id,
                    )
                configuration = self._config.model_configuration
                selection = session._selection
                session._active_turn_id = turn_id
        try:
            execution = self._build_turn_execution(
                session, selection, configuration
            )
        except Exception:
            with session._state_lock:
                if session._active_turn_id == turn_id:
                    session._active_turn_id = None
            raise
        with session._state_lock:
            if session._active_turn_id != turn_id:
                raise RuntimeError("Turn admission state changed unexpectedly")
            session._active_execution = execution
            session._agent.bind_turn_clients(
                execution.agent_client,
                execution.agent.provider_model,
                execution.judge_client,
                execution.judge.provider_model,
            )
        return execution

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            sessions = tuple(self._sessions.values())
            busy = [
                session.info.session_id
                for session in sessions
                if session._active_turn() is not None
            ]
            if busy:
                raise NovalError(
                    "runtime_busy",
                    "Runtime has active turns and cannot be closed.",
                    retryable=True,
                    details={"session_ids": busy},
                )
            self._closed = True
        for session in sessions:
            session.close()
        with self._transport_lock:
            transports = tuple(
                {id(item): item for item in self._transport_pool.values()}.values()
            )
            self._transport_pool.clear()
        for transport in transports:
            close = getattr(transport, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    log.warning("Provider transport close failed", exc_info=True)

    def __enter__(self) -> "NovalRuntime":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
