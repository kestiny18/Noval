"""Foundational tool types and registration.

Adding a tool should require only a typed function. This module is deliberately
Provider-neutral: it describes tool names, descriptions, and JSON schemas, while
Provider adapters translate them into wire-specific function-calling formats.

Tool contract:
  - success: return raw domain content;
  - actionable domain failure: raise ToolError with a corrective message;
  - generic failures, timeouts, truncation, approval, and logging belong to the
    executor pipeline.
"""
from __future__ import annotations

import inspect
import types
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import (
    TYPE_CHECKING, Any, Callable, Dict, List, Literal, Optional, Tuple, Union,
    get_args, get_origin, get_type_hints,
)

from .permissions import PermissionController

if TYPE_CHECKING:
    from .confinement import ConfinementPolicy
    from .discovery import DiscoveryPolicy
    from .mcp import McpRegistry
    from .process import ProcessRuntime
    from .shell import ShellBackend
    from .skills import SkillRegistry


# ---------------------------------------------------------------------------
# Risk is a fact declared by a tool; the permission controller decides approval.
# ---------------------------------------------------------------------------
class Risk(str, Enum):
    READ = "read"            # Read-only operation.
    WRITE = "write"          # Changes local state.
    DANGEROUS = "dangerous"  # Arbitrary or high-impact operation.


# ---------------------------------------------------------------------------
# Tool-raised domain errors are returned to the model as corrective feedback.
# ---------------------------------------------------------------------------
class ToolError(Exception):
    """Domain error whose message should tell the model how to recover."""


# ---------------------------------------------------------------------------
# Unified result: content is model-visible; meta remains framework-only.
# ---------------------------------------------------------------------------
@dataclass
class ToolResult:
    content: str                          # Text returned to the model.
    is_error: bool = False
    truncated: bool = False
    meta: Dict[str, Any] = field(default_factory=dict)  # Timing and raw-size metadata.


# ---------------------------------------------------------------------------
# Execution context is injected into tools that declare ctx: Context first.
# ---------------------------------------------------------------------------
@dataclass
class ReadRecord:
    """Read-tracker record used for read-before-write and staleness checks."""
    mtime: float
    content: str          # Full normalized content; meaningful only for a full read.
    is_partial: bool      # Partial reads do not satisfy the read-before-write contract.
    read_ranges: List[Tuple[int, int]] = field(default_factory=list)  # Inclusive visible ranges.
    total_lines: Optional[int] = None  # Known after EOF or budget-limited full reads.


@dataclass
class Context:
    """Per-invocation execution context.

    ``workdir`` anchors relative paths and child-process cwd. ``read_state`` is
    shared across file tools. ``process_runtime`` freezes the selected process
    backend and sandbox policy, ``discovery`` owns project ignore matching, and
    ``permissions`` owns session authority.
    """
    workdir: Path
    read_state: Dict[str, ReadRecord] = field(default_factory=dict)
    permissions: PermissionController = field(default_factory=PermissionController)
    shell_backend: Optional["ShellBackend"] = None
    process_runtime: Optional["ProcessRuntime"] = None
    confinement: Optional["ConfinementPolicy"] = None
    discovery: Optional["DiscoveryPolicy"] = None
    skills: Optional["SkillRegistry"] = None
    skills_auto_refresh: bool = False
    mcp: Optional["McpRegistry"] = None
    mcp_auto_refresh: bool = False


# ---------------------------------------------------------------------------
# Tool definition stored in the registry.
# ---------------------------------------------------------------------------
@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]   # JSON Schema generated from the signature.
    func: Callable
    risk: Risk = Risk.READ
    # Whether the first parameter is an injected ctx: Context.
    wants_context: bool = False
    # Optional per-argument risk assessment; the result overrides static risk.
    risk_assessor: Optional[Callable[[Dict[str, Any]], Risk]] = None


# ---------------------------------------------------------------------------
# Registry.
# ---------------------------------------------------------------------------
_REGISTRY: Dict[str, Tool] = {}


def get_tool(name: str) -> Optional[Tool]:
    return _REGISTRY.get(name)


def all_tools() -> List[Tool]:
    return list(_REGISTRY.values())


# ---------------------------------------------------------------------------
# Automatic Python type annotation to JSON Schema conversion.
# ---------------------------------------------------------------------------
_PY_TO_JSON = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _type_schema(py_type: Any) -> Dict[str, Any]:
    """Convert a Python annotation into a conservative JSON Schema fragment.

    Supports bare types, Optional, list, dict, and Literal. Unknown annotations
    fall back to string instead of inventing a more specific type.
    """
    origin = get_origin(py_type)

    # Optional uses its non-None member; multi-type unions remain unconstrained.
    if origin is Union or origin is getattr(types, "UnionType", None):
        non_none = [a for a in get_args(py_type) if a is not type(None)]
        return _type_schema(non_none[0]) if len(non_none) == 1 else {}

    if origin is list:
        args = get_args(py_type)
        return {"type": "array", "items": _type_schema(args[0]) if args else {"type": "string"}}

    if origin is dict:
        return {"type": "object"}

    if origin is Literal:
        return {"enum": list(get_args(py_type))}

    return {"type": _PY_TO_JSON.get(py_type, "string")}


def _build_schema(func: Callable, param_descriptions: Dict[str, str]) -> Dict[str, Any]:
    """Derive JSON Schema from function annotations, defaults, and descriptions."""
    hints = get_type_hints(func)
    sig = inspect.signature(func)

    props: Dict[str, Any] = {}
    required: List[str] = []

    for pname, param in sig.parameters.items():
        if pname == "self":
            continue
        py_type = hints.get(pname, str)
        if py_type is Context:          # Framework-injected and hidden from the model.
            continue
        prop: Dict[str, Any] = _type_schema(py_type)
        if pname in param_descriptions:
            prop["description"] = param_descriptions[pname]
        props[pname] = prop
        if param.default is inspect.Parameter.empty:
            required.append(pname)

    return {"type": "object", "properties": props, "required": required}


def _wants_context(func: Callable) -> bool:
    """Return whether the tool declares ctx: Context as its first parameter."""
    params = list(inspect.signature(func).parameters.values())
    if not params:
        return False
    hints = get_type_hints(func)
    return hints.get(params[0].name) is Context


# ---------------------------------------------------------------------------
# @tool is the only registration entry point.
# ---------------------------------------------------------------------------
def tool(
    risk: Risk = Risk.READ,
    *,
    name: Optional[str] = None,
    param_descriptions: Optional[Dict[str, str]] = None,
    override: bool = False,
    risk_assessor: Optional[Callable[[Dict[str, Any]], Risk]] = None,
) -> Callable:
    """Register a regular typed function as a tool.

    Example:
        @tool(risk=Risk.READ, param_descriptions={"path": "File path to read"})
        def read_file(path: str) -> str:
            '''Read a file from the requested path.'''
            ...

    The description comes from the docstring and the schema from the signature.
    The original function is returned unchanged, so direct calls and tests work.

    Duplicate names fail fast because silent replacement would make the model's
    visible capabilities depend on import order. Set ``override=True`` only for
    an intentional replacement.
    """
    def decorator(func: Callable) -> Callable:
        t = Tool(
            name=name or func.__name__,
            description=(func.__doc__ or "").strip(),
            parameters=_build_schema(func, param_descriptions or {}),
            func=func,
            risk=risk,
            wants_context=_wants_context(func),
            risk_assessor=risk_assessor,
        )
        if t.name in _REGISTRY and not override:
            raise ValueError(
                f"tool name '{t.name}' is already registered; use "
                "@tool(..., override=True) for an intentional replacement"
            )
        _REGISTRY[t.name] = t
        return func
    return decorator


# Built-in implementations live in noval/builtins.py.
