"""Embeddable multi-session Application API for Noval."""
from __future__ import annotations

import copy
import json
import logging
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional, Protocol, Tuple
from uuid import uuid4

from .agent import Agent, AgentTurnOutcome, detect_environment, load_project_memory
from .api import (
    ErrorInfo,
    EventType,
    NovalError,
    PermissionDecision,
    PermissionRequest,
    PermissionStateView,
    RuntimeEvent,
    RuntimeOptions,
    SessionInfo,
    SessionOptions,
    SessionPersistence,
    StopReason,
    TurnMetrics,
    TurnRequest,
    TurnResult,
    TurnStatus,
)
from .client import LLMClient, ProviderError, create_provider_client
from .config import Config
from .context import ContextManager
from .permissions import PermissionController, PermissionMode, PermissionState
from .process import ProcessRuntime, SandboxMode, SandboxPolicy, sandbox_status_text
from .redaction import redact_sensitive_text
from .session import (
    JsonlSessionStore,
    PersistentSessionStore,
    SessionMetadataStore,
    list_sessions,
)
from .shell import resolve_shell_backend
from .task import CompletionVerifier, SemanticJudge, TaskController, TaskEventStore
from .tools import Tool, all_tools
from .usage import JsonlUsageStore, MeteredLLMClient


log = logging.getLogger("noval.application")


@dataclass(frozen=True)
class ClientSpec:
    """Native dependency-factory input; credentials never enter public DTOs."""

    purpose: str
    provider: str
    model: str
    session_id: str


class ClientFactory(Protocol):
    def __call__(self, spec: ClientSpec) -> LLMClient: ...


EventSink = Callable[[RuntimeEvent], None]


class PermissionHandler(Protocol):
    def __call__(self, request: PermissionRequest) -> PermissionDecision: ...


def _clone_tools(tools: Iterable[Tool]) -> Tuple[Tool, ...]:
    return tuple(
        replace(tool, parameters=copy.deepcopy(tool.parameters))
        for tool in tools
    )


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


def _public_status(reason: StopReason) -> TurnStatus:
    if reason is StopReason.COMPLETED:
        return TurnStatus.COMPLETED
    if reason is StopReason.ERROR:
        return TurnStatus.FAILED
    return TurnStatus.STOPPED


def _redact_arguments(arguments: Dict[str, object]) -> Dict[str, object]:
    """Reuse the executor redactor while preserving a JSON object shape."""
    encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    try:
        decoded = json.loads(redact_sensitive_text(encoded))
    except json.JSONDecodeError:
        return {"argument_keys": sorted(str(key) for key in arguments)}
    return decoded if isinstance(decoded, dict) else {}


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
    ):
        self._runtime = runtime
        self._base_info = info
        self._agent = agent
        self._store = store
        self._permissions = permissions
        self._process_runtime = process_runtime
        self._event_sink = event_sink
        self._permission_handler = permission_handler
        self._turn_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._closed = False
        self._active_turn_id: Optional[str] = None
        self._event_sequence = 0

    @property
    def info(self) -> SessionInfo:
        with self._state_lock:
            return replace(self._base_info, is_open=not self._closed)

    def permission_state(self) -> PermissionStateView:
        return PermissionStateView(
            mode=self._permissions.mode,
            approved_tools=tuple(sorted(self._permissions.approved_tools)),
        )

    def set_permission_handler(
        self, handler: Optional[PermissionHandler]
    ) -> None:
        self._require_idle()
        self._permission_handler = handler

    def set_permission_mode(self, mode: PermissionMode) -> PermissionStateView:
        self._require_idle()
        self._permissions.set_mode(mode)
        return self.permission_state()

    def allow_tool(self, tool_name: str) -> PermissionStateView:
        self._require_idle()
        self._permissions.allow_tool(tool_name)
        return self.permission_state()

    def revoke_tool(self, tool_name: str) -> PermissionStateView:
        self._require_idle()
        self._permissions.revoke_tool(tool_name)
        return self.permission_state()

    def reset_permissions(self) -> PermissionStateView:
        self._require_idle()
        self._permissions.reset()
        return self.permission_state()

    def _require_idle(self) -> None:
        with self._state_lock:
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
        with self._state_lock:
            if self._closed:
                raise NovalError(
                    "session_closed",
                    "Session is closed.",
                    session_id=self._base_info.session_id,
                )
        if not self._turn_lock.acquire(blocking=False):
            with self._state_lock:
                active_turn_id = self._active_turn_id
            raise NovalError(
                "session_busy",
                "Session already has an active turn.",
                retryable=True,
                session_id=self._base_info.session_id,
                details={"active_turn_id": active_turn_id},
            )

        turn_id = "turn-" + uuid4().hex
        self._process_runtime.begin_turn()
        with self._state_lock:
            self._active_turn_id = turn_id
        started = time.perf_counter()
        self._emit(
            EventType.TURN_STARTED.value,
            turn_id=turn_id,
            payload={"client_request_id": request.client_request_id},
        )
        try:
            outcome = self._agent.run_turn(request.text)
            result = self._result_from_outcome(
                request, turn_id, outcome, started
            )
            self._emit(
                EventType.TURN_COMPLETED.value,
                turn_id=turn_id,
                payload={
                    "status": result.status.value,
                    "stop_reason": result.stop_reason.value,
                },
            )
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
            )
            self._emit_terminal_failure(result)
            return result
        finally:
            with self._state_lock:
                self._active_turn_id = None
            self._turn_lock.release()

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
            status=_public_status(reason),
            message=outcome.message,
            stop_reason=reason,
            usage=outcome.usage,
            metrics=metrics,
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
        )

    def _emit_terminal_failure(self, result: TurnResult) -> None:
        self._emit(
            EventType.TURN_FAILED.value,
            turn_id=result.turn_id,
            payload={
                "status": result.status.value,
                "stop_reason": result.stop_reason.value,
                "error": result.error.to_dict() if result.error else None,
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
        sink = self._event_sink
        if sink is None:
            return
        with self._state_lock:
            self._event_sequence += 1
            sequence = self._event_sequence
        event = RuntimeEvent(
            event_id="event-" + uuid4().hex,
            session_id=self._base_info.session_id,
            turn_id=turn_id,
            sequence=sequence,
            timestamp=_utc_now(),
            type=event_type,
            payload=dict(payload or {}),
        )
        try:
            sink(event)
        except Exception:
            log.warning("runtime event sink failed type=%s", event_type, exc_info=True)

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
    ):
        self._config = copy.deepcopy(config)
        self._tool_catalog = _clone_tools(tools if tools is not None else all_tools())
        self._uses_default_client_factory = client_factory is None
        self._client_factory = client_factory
        self._event_sink = event_sink
        self._sessions: Dict[str, AgentSession] = {}
        self._lock = threading.RLock()
        self._closed = False

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
        provider = options.provider or self._config.provider
        model = options.model or self._config.model
        judge_model = options.judge_model or self._config.judge_model

        store: Optional[PersistentSessionStore]
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
                except (FileNotFoundError, ValueError) as error:
                    raise NovalError(
                        "session_not_found",
                        str(error),
                        session_id=session_id,
                    ) from error
            resolved_session_id = store.session_id
            application_metadata = store.load_metadata().get("application")
            if session_id is not None and isinstance(application_metadata, dict):
                if options.provider is None:
                    stored_provider = application_metadata.get("provider")
                    if isinstance(stored_provider, str) and stored_provider:
                        provider = stored_provider
                if options.model is None:
                    stored_model = application_metadata.get("model")
                    if isinstance(stored_model, str) and stored_model:
                        model = stored_model
                if options.judge_model is None:
                    stored_judge = application_metadata.get("judge_model")
                    if isinstance(stored_judge, str) and stored_judge:
                        judge_model = stored_judge
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
            store.update_metadata({
                "application": {
                    "schema_version": 1,
                    "provider": provider,
                    "model": model,
                    "judge_model": judge_model,
                }
            })

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
        agent_client = self._make_client(
            "agent", provider, model, resolved_session_id, session_config
        )
        judge_client = self._make_client(
            "completion_judge",
            provider,
            judge_model,
            resolved_session_id,
            session_config,
        )
        if session_config.persist_usage:
            usage_store = JsonlUsageStore(
                session_config.usage_dir(), resolved_session_id
            )
            agent_client = MeteredLLMClient(
                agent_client, usage_store, model, purpose="agent"
            )
            judge_client = MeteredLLMClient(
                judge_client,
                usage_store,
                judge_model,
                purpose="completion_judge",
            )

        resume_messages = None
        context_manager = None
        if store is not None:
            context_manager = ContextManager(
                agent_client,
                store,
                model,
                session_config.context_budget_tokens,
            )
            if session_id is not None:
                resume_messages = context_manager.restore()
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
            provider=provider,
            model=model,
            is_open=True,
        )
        selected_sink = event_sink if event_sink is not None else self._event_sink
        session_holder: Dict[str, AgentSession] = {}

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
    ) -> LLMClient:
        if self._uses_default_client_factory:
            return self._default_client(config, provider, model)
        assert self._client_factory is not None
        return self._client_factory(ClientSpec(
            purpose=purpose,
            provider=provider,
            model=model,
            session_id=session_id,
        ))

    @staticmethod
    def _default_client(config: Config, provider: str, model: str) -> LLMClient:
        return create_provider_client(
            provider,
            api_key=config.resolve_api_key(),
            model=model,
            base_url=config.base_url,
            anthropic_base_url=config.anthropic_base_url,
            timeout=config.request_timeout_seconds,
            max_retries=config.request_max_retries,
            anthropic_max_tokens=config.anthropic_max_tokens,
        )

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

    def list_active_sessions(self) -> Tuple[SessionInfo, ...]:
        with self._lock:
            return tuple(session.info for session in self._sessions.values())

    def list_persisted_sessions(self, workdir: str) -> Tuple[SessionInfo, ...]:
        root = Path(workdir).expanduser().resolve()
        with self._lock:
            open_ids = set(self._sessions)
        return tuple(
            SessionInfo(
                session_id=meta.session_id,
                workdir=str(root),
                persistence=SessionPersistence.PERSISTENT,
                provider=meta.provider or self._config.provider,
                model=meta.model or self._config.model,
                is_open=meta.session_id in open_ids,
            )
            for meta in list_sessions(self._config.sessions_dir(), root)
        )

    def _session_closed(self, session: AgentSession) -> None:
        with self._lock:
            current = self._sessions.get(session.info.session_id)
            if current is session:
                del self._sessions[session.info.session_id]

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

    def __enter__(self) -> "NovalRuntime":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
