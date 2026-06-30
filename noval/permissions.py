"""Session-scoped permission policy, independent from tools and storage."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, Optional, Set

log = logging.getLogger("noval.permissions")


class PermissionMode(str, Enum):
    ASK = "ask"
    FULL_ACCESS = "full_access"

    @property
    def label(self) -> str:
        return "请求批准" if self is PermissionMode.ASK else "完全访问"


@dataclass
class PermissionState:
    """Mutable session permission state persisted in the session sidecar."""

    mode: PermissionMode = PermissionMode.ASK
    approved_tools: Set[str] = field(default_factory=set)

    @classmethod
    def from_dict(cls, data: object) -> "PermissionState":
        if not isinstance(data, dict):
            return cls()
        try:
            mode = PermissionMode(data.get("mode", PermissionMode.ASK.value))
        except (TypeError, ValueError):
            mode = PermissionMode.ASK
        raw_tools = data.get("approved_tools", [])
        tools = {
            item for item in raw_tools
            if isinstance(item, str) and item.strip()
        } if isinstance(raw_tools, list) else set()
        return cls(mode=mode, approved_tools=tools)

    def to_dict(self) -> Dict[str, object]:
        return {
            "mode": self.mode.value,
            "approved_tools": sorted(self.approved_tools),
        }


PersistPermissions = Callable[[Dict[str, object]], None]


class PermissionController:
    """The single policy boundary used by the executor and CLI."""

    def __init__(
        self,
        state: Optional[PermissionState] = None,
        on_change: Optional[PersistPermissions] = None,
    ):
        source = state or PermissionState()
        self._state = PermissionState(source.mode, set(source.approved_tools))
        self._on_change = on_change

    @property
    def mode(self) -> PermissionMode:
        return self._state.mode

    @property
    def approved_tools(self) -> frozenset[str]:
        return frozenset(self._state.approved_tools)

    def requires_approval(self, tool_name: str, risk: str) -> bool:
        if self.mode is PermissionMode.FULL_ACCESS:
            return False
        if risk != "dangerous":
            return False
        return tool_name not in self._state.approved_tools

    def set_mode(self, mode: PermissionMode | str) -> None:
        parsed = mode if isinstance(mode, PermissionMode) else PermissionMode(mode)
        if parsed is self._state.mode:
            return
        self._state.mode = parsed
        self._notify()

    def allow_tool(self, tool_name: str) -> None:
        name = tool_name.strip()
        if not name or name in self._state.approved_tools:
            return
        self._state.approved_tools.add(name)
        self._notify()

    def revoke_tool(self, tool_name: str) -> None:
        if tool_name not in self._state.approved_tools:
            return
        self._state.approved_tools.remove(tool_name)
        self._notify()

    def reset(self) -> None:
        changed = self._state.mode is not PermissionMode.ASK or bool(self._state.approved_tools)
        self._state.mode = PermissionMode.ASK
        self._state.approved_tools.clear()
        if changed:
            self._notify()

    def snapshot(self) -> PermissionState:
        return PermissionState(self._state.mode, set(self._state.approved_tools))

    def _notify(self) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change(self._state.to_dict())
        except Exception:
            log.warning("会话权限状态持久化失败，当前进程内仍然生效", exc_info=True)
