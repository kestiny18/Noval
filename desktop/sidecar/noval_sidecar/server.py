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
            "workspace.select": self._workspace_select,
            "session.list": self._session_list,
            "session.create": self._session_create,
            "session.resume": self._session_resume,
            "session.rename": self._session_rename,
            "session.transcript": self._session_transcript,
            "session.events": self._session_events,
            "session.permissions": self._session_permissions,
            "session.permission_mode": self._session_permission_mode,
            "session.allow_tool": self._session_allow_tool,
            "session.revoke_tool": self._session_revoke_tool,
            "session.reset_permissions": self._session_reset_permissions,
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
            "capabilities": ["sessions", "transcript", "events", "permissions", "visible_streaming", "cancellation"],
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

    def _session_list(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime, workspace = self._ready()
        return {"sessions": [item.to_dict() for item in runtime.list_persisted_sessions(str(workspace))]}

    def _options(self, params: dict[str, Any]) -> SessionOptions:
        _, workspace = self._ready()
        data = dict(params.get("options", {}))
        data.setdefault("schema_version", 1)
        data["workdir"] = str(workspace)
        return SessionOptions.from_dict(data)

    def _session_create(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime, _ = self._ready()
        session = runtime.create_session(self._options(params), permission_handler=self._permission_handler)
        return {"session": session.info.to_dict(), "permissions": session.permission_state().to_dict()}

    def _session_resume(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime, _ = self._ready()
        session = runtime.resume_session(self._required_string(params, "session_id"), self._options(params), permission_handler=self._permission_handler)
        return {"session": session.info.to_dict(), "permissions": session.permission_state().to_dict()}

    def _session(self, params: dict[str, Any]):
        runtime, _ = self._ready()
        return runtime.get_session(self._required_string(params, "session_id"))

    def _session_rename(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._session(params).rename(self._required_string(params, "title")).to_dict()

    def _session_transcript(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._session(params).transcript(after_sequence=int(params.get("after_sequence", 0)), limit=int(params.get("limit", 100))).to_dict()

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

    def _turn_start(self, request_id: str, params: dict[str, Any]) -> None:
        session = self._session(params)
        turn = TurnRequest.from_dict({
            "schema_version": 1,
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
        if self._runtime is None:
            raise ValueError("Runtime is not started.")
        if self._workspace is None:
            raise ValueError("A workspace must be selected.")
        return self._runtime, self._workspace

    @staticmethod
    def _required_string(params: dict[str, Any], key: str) -> str:
        value = params.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a non-empty string")
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

