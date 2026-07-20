"""Central tool-execution pipeline: the model's interface to the real world.

Every call crosses the same boundary:

  resolve tool -> parse JSON -> validate schema -> approve -> execute
    -> normalize and truncate output -> return ToolResult

New tools inherit error handling, approval, truncation, redaction, and logging.
"""
from __future__ import annotations

import inspect
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Sequence

from .config import Config
from .permissions import PermissionController
from .redaction import redact_sensitive_text
from .tools import Risk, Tool, ToolError, ToolResult, all_tools

log = logging.getLogger("noval.executor")

# The host answers approval requests. True/"yes" allows once, "always" grants
# session-wide approval for the tool, and every other answer denies the call.
Approver = Callable[[Tool, Dict[str, Any]], object]
BeforeToolExecute = Callable[[Tool, Dict[str, Any], Risk], Optional[str]]


def _normalize_decision(raw: object) -> str:
    if raw == "always":
        return "always"
    if raw is True or raw == "yes":
        return "yes"
    return "no"


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    """Apply head-and-tail truncation while reporting the omitted character count."""
    if len(text) <= limit:
        return text, False
    head = limit * 2 // 3
    tail = limit - head
    omitted = len(text) - head - tail
    note = (
        f"\n\n...[output truncated: omitted {omitted} characters from the middle. "
        "Use a more focused query, keyword search, or line range to narrow the output]...\n\n"
    )
    return text[:head] + note + text[-tail:], True


def _validate_required(tool: Tool, args: Dict[str, Any]) -> Optional[str]:
    """Validate required schema fields before invocation."""
    missing = [r for r in tool.parameters.get("required", []) if r not in args]
    if missing:
        return f"missing required arguments: {', '.join(missing)}"
    return None


def execute_tool_call(
    name: str,
    raw_arguments: str,
    config: Config,
    approver: Optional[Approver] = None,
    context: Optional["Context"] = None,
    before_execute: Optional[BeforeToolExecute] = None,
    tools: Optional[Sequence[Tool]] = None,
) -> ToolResult:
    """Execute one tool call and always return a ToolResult."""
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc)
    t = {
        "approval_end": None,
        "exec_start": None,
        "executed": False,
        "effective_risk": "unknown",
    }  # Execution timing begins after approval so user wait time remains separate.

    def finish(content: str, *, is_error: bool = False, truncated: bool = False, **meta: Any) -> ToolResult:
        safe_content = redact_sensitive_text(content)
        if safe_content != content:
            content = safe_content
            meta["redacted"] = True
        now = time.perf_counter()
        ref = t["exec_start"] or t["approval_end"] or started
        meta.update(
            tool=name,
            duration_ms=round((now - ref) * 1000, 1),
            started_at=started_at.isoformat(timespec="milliseconds"),
            completed_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        )
        meta.setdefault("executed", t["executed"])
        meta.setdefault("effective_risk", t["effective_risk"])
        if t["approval_end"] is not None:  # Record approval wait separately.
            wait = round((t["approval_end"] - started) * 1000, 1)
            if wait >= 1.0:
                meta["approval_wait_ms"] = wait
        log.info("tool=%s is_error=%s truncated=%s dur=%sms",
                 name, is_error, truncated, meta["duration_ms"])
        return ToolResult(content=content, is_error=is_error, truncated=truncated, meta=meta)

    # 1. Resolve the tool and expose alternatives for corrective retries.
    catalog = list(tools) if tools is not None else all_tools()
    tool = next((candidate for candidate in catalog if candidate.name == name), None)
    if tool is None:
        available = ", ".join(t.name for t in catalog) or "(none)"
        return finish(f"Error: unknown tool '{name}'. Available tools: {available}", is_error=True)
    t["effective_risk"] = tool.risk.value

    # 2. Parse JSON arguments.
    try:
        args = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError as e:
        return finish(f"Error: tool arguments are not valid JSON: {e}", is_error=True)
    if not isinstance(args, dict):
        return finish("Error: tool arguments must be a JSON object", is_error=True)

    # 3. Validate the schema.
    err = _validate_required(tool, args)
    if err:
        return finish(f"Error: {err}", is_error=True)

    # 4. Apply centralized approval using the invocation's effective risk.
    effective_risk = tool.risk_assessor(args) if tool.risk_assessor else tool.risk
    t["effective_risk"] = effective_risk.value
    permissions = context.permissions if context is not None else PermissionController()
    if permissions.requires_approval(tool.name, effective_risk.value):
        decision = _normalize_decision(approver(tool, args) if approver else "no")
        if decision == "always":
            permissions.allow_tool(tool.name)
        if decision == "no":
            return finish(
                f"Error: the user denied tool '{name}'.",
                is_error=True,
                effective_risk=effective_risk.value,
            )
    t["approval_end"] = time.perf_counter()

    # Inject Context as the first positional argument when declared by the tool.
    if tool.wants_context and context is None:
        return finish(
            f"Error: tool '{name}' requires an execution context, but none was provided",
            is_error=True,
            effective_risk=effective_risk.value,
        )
    extra = (context,) if tool.wants_context else ()

    # 5a. Bind first so signature mismatches remain distinct from tool-internal TypeError.
    try:
        inspect.signature(tool.func).bind(*extra, **args)
    except TypeError as e:
        return finish(
            f"Error: arguments do not match the tool signature: {e}",
            is_error=True,
            effective_risk=effective_risk.value,
        )

    # PreToolUse runs after approval and before execution. It may allow or block,
    # but cannot rewrite tool arguments.
    if before_execute is not None:
        try:
            blocked = before_execute(tool, args, effective_risk)
        except Exception as e:
            log.exception("PreToolUse callback failed for tool %s", name)
            return finish(
                f"Error: PreToolUse callback failed ({type(e).__name__}): {e}",
                is_error=True,
                effective_risk=effective_risk.value,
                pre_tool_hook_error=True,
            )
        if blocked:
            return finish(
                f"Error: a PreToolUse Hook blocked tool execution.\n\n{blocked}",
                is_error=True,
                effective_risk=effective_risk.value,
                pre_tool_hook_blocked=True,
            )

    # 5b. Execute and normalize every failure into corrective model feedback.
    t["exec_start"] = time.perf_counter()
    t["executed"] = True
    try:
        raw = tool.func(*extra, **args)
    except ToolError as e:                       # Rich domain failure from the tool.
        content = f"Error: {e}"
        safe_content = redact_sensitive_text(content)
        redacted = safe_content != content
        content = safe_content
        original_chars = len(content)
        content, truncated = _truncate(content, config.max_tool_output_chars)
        return finish(
            content,
            is_error=True,
            truncated=truncated,
            original_chars=original_chars,
            effective_risk=effective_risk.value,
            **({"redacted": True} if redacted else {}),
        )
    except Exception as e:                         # Catch unexpected tool failures.
        log.exception("tool %s execution failed", name)
        return finish(
            f"Error: tool execution failed ({type(e).__name__}): {e}",
            is_error=True,
            effective_risk=effective_risk.value,
        )

    # 6. Normalize output to text, redact it, and truncate it.
    content = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, default=str)
    safe_content = redact_sensitive_text(content)
    redacted = safe_content != content
    content = safe_content
    original_chars = len(content)
    content, truncated = _truncate(content, config.max_tool_output_chars)
    return finish(
        content,
        truncated=truncated,
        original_chars=original_chars,
        effective_risk=effective_risk.value,
        **({"redacted": True} if redacted else {}),
    )
