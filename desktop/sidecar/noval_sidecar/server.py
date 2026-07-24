from __future__ import annotations

import importlib.metadata
import json
import platform
import sys
import threading
from pathlib import Path
from typing import Any, BinaryIO, Callable
from uuid import uuid4

from noval import (
    API_SCHEMA_VERSION,
    ConfiguredModelUpsert,
    ConnectionUpsert,
    NovalError,
    NovalRuntime,
    PermissionDecision,
    PermissionMode,
    PermissionRequest,
    RuntimeOptions,
    SessionOptions,
    TurnRequest,
)

from . import PROTOCOL_VERSION
from .protocol import ProtocolError, Request, error_response, event, parse_request, response


class SidecarServer:
    def __init__(self, stdin: BinaryIO, stdout: BinaryIO):
        self._stdin = stdin
        self._stdout = stdout
        self._write_lock = threading.Lock()
        self._runtime: NovalRuntime | None = None
        self._workspace: Path | None = None
        self._permission_waiters: dict[str, tuple[threading.Event, list[PermissionDecision]]] = {}
        self._waiters_lock = threading.Lock()
        self._turn_threads: dict[str, threading.Thread] = {}

    def serve(self) -> int:
        for line in self._stdin:
            try:
                request = parse_request(line)
                result = self.dispatch(request)
                if result is not None:
                    self._send(response(request.request_id, result))
            except ProtocolError as error:
                self._send(error_response(error.request_id, error.code, error.safe_message))
            except NovalError as error:
                self._send(error_response(getattr(locals().get("request", None), "request_id", None), error.code, error.safe_message, retryable=error.retryable))
            except SystemExit as error:
                self._send(error_response(getattr(locals().get("request", None), "request_id", None), "configuration_error", str(error)))
            except (TypeError, ValueError, KeyError) as error:
                self._send(error_response(getattr(locals().get("request", None), "request_id", None), "invalid_params", str(error)))
            except Exception:
                self._send(error_response(getattr(locals().get("request", None), "request_id", None), "internal_error", "The sidecar encountered an internal error."))
        self.close()
        return 0

    def dispatch(self, request: Request) -> Any:
        handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "system.hello": self._hello,
            "runtime.start": self._runtime_start,
            "runtime.configuration": self._runtime_configuration,
            "model.profiles": self._model_profiles,
            "model.configuration": self._model_configuration,
            "model.connection.upsert": self._model_connection_upsert,
            "model.connection.delete": self._model_connection_delete,
            "model.configured.upsert": self._model_configured_upsert,
            "model.configured.delete": self._model_configured_delete,
            "model.default.set": self._model_default_set,
            "workspace.list": self._workspace_list,
            "workspace.select": self._workspace_select,
            "workspace.sessions": self._workspace_sessions,
            "session.list": self._session_list,
            "session.create": self._session_create,
            "session.resume": self._session_resume,
            "session.rename": self._session_rename,
            "session.transcript": self._session_transcript,
            "session.transcript_history": self._session_transcript_history,
            "session.events": self._session_events,
            "session.permissions": self._session_permissions,
            "session.permission_mode": self._session_permission_mode,
            "session.allow_tool": self._session_allow_tool,
            "session.revoke_tool": self._session_revoke_tool,
            "session.reset_permissions": self._session_reset_permissions,
            "session.models.select": self._session_models_select,
            "permission.resolve": self._permission_resolve,
            "turn.start": lambda params: self._turn_start(request.request_id, params),
            "turn.cancel": self._turn_cancel,
        }
        handler = handlers.get(request.method)
        if handler is None:
            raise ProtocolError("method_not_found", "The requested sidecar method is not supported.", request_id=request.request_id)
        return handler(request.params)

    def _hello(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "core_version": importlib.metadata.version("noval"),
            "python_version": platform.python_version(),
            "platform": sys.platform,
            "capabilities": [
                "sessions",
                "transcript",
                "transcript_history",
                "events",
                "permissions",
                "visible_streaming",
                "cancellation",
                "model_configuration",
            ],
        }

    def _runtime_start(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._runtime is not None:
            raise ValueError("Runtime is already started.")
        settings_path = params.get("settings_path")
        if settings_path is not None and not isinstance(settings_path, str):
            raise ValueError("settings_path must be a string or null")
        self._runtime = NovalRuntime.from_settings(
            RuntimeOptions(settings_path=settings_path), event_sink=self._runtime_event, configure_logging=True
        )
        return {"started": True}

    def _workspace_select(self, params: dict[str, Any]) -> dict[str, Any]:
        raw = self._required_string(params, "workdir")
        path = Path(raw).expanduser().resolve()
        if not path.is_dir():
            raise ValueError("workdir must be an existing directory")
        self._workspace = path
        return {"workdir": str(path)}

    def _runtime_configuration(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._runtime_required().configuration().to_dict()

    def _model_profiles(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": API_SCHEMA_VERSION,
            "profiles": [
                profile.to_dict()
                for profile in self._runtime_required().list_provider_profiles()
            ],
        }

    def _model_configuration(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._runtime_required().get_model_configuration().to_dict()

    def _model_connection_upsert(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        request = ConnectionUpsert.from_dict(params)
        return self._runtime_required().upsert_connection(request).to_dict()

    def _model_connection_delete(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        return self._runtime_required().delete_connection(
            self._required_string(params, "connection_id"),
            expected_configuration_revision=self._required_int(
                params, "expected_configuration_revision"
            ),
        ).to_dict()

    def _model_configured_upsert(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        request = ConfiguredModelUpsert.from_dict(params)
        return self._runtime_required().upsert_configured_model(
            request
        ).to_dict()

    def _model_configured_delete(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        return self._runtime_required().delete_configured_model(
            self._required_string(params, "configured_model_id"),
            expected_configuration_revision=self._required_int(
                params, "expected_configuration_revision"
            ),
        ).to_dict()

    def _model_default_set(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._runtime_required().set_default_model(
            self._required_string(params, "configured_model_id"),
            expected_configuration_revision=self._required_int(
                params, "expected_configuration_revision"
            ),
        ).to_dict()

    def _workspace_list(self, params: dict[str, Any]) -> dict[str, Any]:
        projects = self._runtime_required().list_persisted_projects()
        return {"projects": [project.to_dict() for project in projects]}

    def _workspace_sessions(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._runtime is None:
            raise ValueError("Runtime is not started.")
        path = Path(self._required_string(params, "workdir")).expanduser().resolve()
        if not path.is_dir():
            raise ValueError("workdir must be an existing directory")
        return {"sessions": [item.to_dict() for item in self._runtime.list_persisted_sessions(str(path))]}

    def _session_list(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime, workspace = self._ready()
        return {"sessions": [item.to_dict() for item in runtime.list_persisted_sessions(str(workspace))]}

    def _options(self, params: dict[str, Any]) -> SessionOptions:
        _, workspace = self._ready()
        data = dict(params.get("options", {}))
        data.setdefault("schema_version", API_SCHEMA_VERSION)
        data["workdir"] = str(workspace)
        return SessionOptions.from_dict(data)

    def _session_create(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime, _ = self._ready()
        session = runtime.create_session(self._options(params), permission_handler=self._permission_handler)
        return {"session": session.info.to_dict(), "permissions": session.permission_state().to_dict()}

    def _session_resume(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime, _ = self._ready()
        session_id = self._required_string(params, "session_id")
        try:
            session = runtime.get_session(session_id)
        except NovalError as error:
            if error.code != "session_not_open":
                raise
            session = runtime.resume_session(session_id, self._options(params), permission_handler=self._permission_handler)
        return {"session": session.info.to_dict(), "permissions": session.permission_state().to_dict()}

    def _session(self, params: dict[str, Any]):
        runtime, _ = self._ready()
        return runtime.get_session(self._required_string(params, "session_id"))

    def _session_rename(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._session(params).rename(self._required_string(params, "title")).to_dict()

    def _session_transcript(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._session(params).transcript(after_sequence=int(params.get("after_sequence", 0)), limit=int(params.get("limit", 100))).to_dict()

    def _session_transcript_history(self, params: dict[str, Any]) -> dict[str, Any]:
        before_sequence = params.get("before_sequence")
        return self._session(params).transcript_history(
            before_sequence=(
                int(before_sequence) if before_sequence is not None else None
            ),
            limit=int(params.get("limit", 24)),
        ).to_dict()

    def _session_events(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._session(params).replay_events(after_sequence=int(params.get("after_sequence", 0)), limit=int(params.get("limit", 100))).to_dict()

    def _session_permissions(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._session(params).permission_state().to_dict()

    def _session_permission_mode(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._session(params).set_permission_mode(PermissionMode(self._required_string(params, "mode"))).to_dict()

    def _session_allow_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._session(params).allow_tool(self._required_string(params, "tool_name")).to_dict()

    def _session_revoke_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._session(params).revoke_tool(self._required_string(params, "tool_name")).to_dict()

    def _session_reset_permissions(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._session(params).reset_permissions().to_dict()

    def _session_models_select(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._session(params)
        selection = session.select_models(
            self._required_string(params, "selected_model_id"),
            self._required_string(params, "selected_judge_model_id"),
        )
        return {
            "selection": selection.to_dict(),
            "session": session.info.to_dict(),
        }

    def _turn_start(self, request_id: str, params: dict[str, Any]) -> None:
        session = self._session(params)
        turn = TurnRequest.from_dict({
            "schema_version": API_SCHEMA_VERSION,
            "text": self._required_string(params, "text"),
            "client_request_id": params.get("client_request_id"),
            "goal": params.get("goal"),
        })

        def run() -> None:
            try:
                result = session.run_turn(turn)
                self._send(response(request_id, result.to_dict()))
            except NovalError as error:
                self._send(error_response(request_id, error.code, error.safe_message, retryable=error.retryable))
            except Exception:
                self._send(error_response(request_id, "internal_error", "The turn failed inside the sidecar."))
            finally:
                self._turn_threads.pop(request_id, None)

        thread = threading.Thread(target=run, name=f"noval-turn-{request_id}", daemon=True)
        self._turn_threads[request_id] = thread
        thread.start()
        return None

    def _turn_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"cancelled": self._session(params).cancel_active_turn()}

    def _permission_handler(self, request: PermissionRequest) -> PermissionDecision:
        signal = threading.Event()
        decision: list[PermissionDecision] = []
        with self._waiters_lock:
            self._permission_waiters[request.request_id] = (signal, decision)
        self._send(event("permission.request", request.request_id, {"request": request.to_dict()}))
        if not signal.wait(timeout=300) or not decision:
            result = PermissionDecision.DENY
        else:
            result = decision[0]
        with self._waiters_lock:
            self._permission_waiters.pop(request.request_id, None)
        return result

    def _permission_resolve(self, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._required_string(params, "permission_request_id")
        decision = PermissionDecision(self._required_string(params, "decision"))
        with self._waiters_lock:
            waiter = self._permission_waiters.get(request_id)
        if waiter is None:
            raise ValueError("permission request is not pending")
        signal, target = waiter
        target.append(decision)
        signal.set()
        return {"resolved": True}

    def _runtime_event(self, value) -> None:
        self._send(event(value.type, value.event_id, value.to_dict()))

    def _ready(self) -> tuple[NovalRuntime, Path]:
        runtime = self._runtime_required()
        if self._workspace is None:
            raise ValueError("A workspace must be selected.")
        return runtime, self._workspace

    def _runtime_required(self) -> NovalRuntime:
        if self._runtime is None:
            raise ValueError("Runtime is not started.")
        return self._runtime

    @staticmethod
    def _required_string(params: dict[str, Any], key: str) -> str:
        value = params.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a non-empty string")
        return value

    @staticmethod
    def _required_int(params: dict[str, Any], key: str) -> int:
        value = params.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{key} must be a positive integer")
        return value

    def _send(self, value: dict[str, Any]) -> None:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        with self._write_lock:
            self._stdout.write(encoded)
            self._stdout.flush()

    def close(self) -> None:
        if self._runtime is not None:
            self._runtime.close()
            self._runtime = None
