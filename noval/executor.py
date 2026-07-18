"""执行管道（接缝3）—— 模型与真实世界之间的「感官接口」。

每次工具调用都流经同一条管道，所有横切关注点在此统一处理，
具体工具只管「做自己那件事」：

  查工具 → 解析参数(JSON容错) → schema校验 → [确认门]
    → 执行(统一 try/except) → 输出规整(head+tail 截断) → 包装 ToolResult

设计目标：加第 N 个工具时，错误/截断/确认/日志全自动继承，无需再操心。
"""
from __future__ import annotations

import inspect
import json
import logging
import time
from typing import Any, Callable, Dict, Optional, Sequence

from .config import Config
from .permissions import PermissionController
from .redaction import redact_sensitive_text
from .tools import Risk, Tool, ToolError, ToolResult, all_tools

log = logging.getLogger("noval.executor")

# 确认门回调：框架问「要不要执行这个工具」，由调用方（CLI/测试）决定怎么答。
# 返回 True/"yes" 允许一次；"always" 本会话总是允许该工具；其余拒绝。
Approver = Callable[[Tool, Dict[str, Any]], object]
BeforeToolExecute = Callable[[Tool, Dict[str, Any], Risk], Optional[str]]


def _normalize_decision(raw: object) -> str:
    if raw == "always":
        return "always"
    if raw is True or raw == "yes":
        return "yes"
    return "no"


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    """超长输出做 head+tail 截断，中间省略并标注可机读的提示。

    保留头尾两端：开头给上下文，结尾常含错误信息/结论，都不该被丢。
    """
    if len(text) <= limit:
        return text, False
    head = limit * 2 // 3
    tail = limit - head
    omitted = len(text) - head - tail
    note = (
        f"\n\n...[输出过长，已省略中间 {omitted} 字符。"
        f"如需完整内容，请用更聚焦的方式（搜索关键词 / 指定行范围等）缩小范围]...\n\n"
    )
    return text[:head] + note + text[-tail:], True


def _validate_required(tool: Tool, args: Dict[str, Any]) -> Optional[str]:
    """轻量 schema 校验：检查 required 参数是否齐全（把 Python TypeError 提前成可纠错信息）。"""
    missing = [r for r in tool.parameters.get("required", []) if r not in args]
    if missing:
        return f"缺少必填参数: {', '.join(missing)}"
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
    """执行单次工具调用，永远返回 ToolResult（绝不抛异常给上层）。"""
    started = time.perf_counter()
    t = {
        "approval_end": None,
        "exec_start": None,
        "executed": False,
    }  # 真正执行的起点；在确认门之后才设，避免把「等用户点 y」算进耗时

    def finish(content: str, *, is_error: bool = False, truncated: bool = False, **meta: Any) -> ToolResult:
        safe_content = redact_sensitive_text(content)
        if safe_content != content:
            content = safe_content
            meta["redacted"] = True
        now = time.perf_counter()
        ref = t["exec_start"] or t["approval_end"] or started
        meta.update(tool=name, duration_ms=round((now - ref) * 1000, 1))
        meta.setdefault("executed", t["executed"])
        if t["approval_end"] is not None:  # 确认等待时间单独记，不污染执行耗时
            wait = round((t["approval_end"] - started) * 1000, 1)
            if wait >= 1.0:
                meta["approval_wait_ms"] = wait
        log.info("tool=%s is_error=%s truncated=%s dur=%sms",
                 name, is_error, truncated, meta["duration_ms"])
        return ToolResult(content=content, is_error=is_error, truncated=truncated, meta=meta)

    # 1. 查工具：未知工具要把可用清单告诉模型，让它能改用正确的工具
    catalog = list(tools) if tools is not None else all_tools()
    tool = next((candidate for candidate in catalog if candidate.name == name), None)
    if tool is None:
        available = ", ".join(t.name for t in catalog) or "(无)"
        return finish(f"Error: 未知工具 '{name}'。可用工具: {available}", is_error=True)

    # 2. 解析参数（JSON 容错）
    try:
        args = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError as e:
        return finish(f"Error: 工具参数不是合法 JSON: {e}", is_error=True)
    if not isinstance(args, dict):
        return finish("Error: 工具参数必须是 JSON 对象", is_error=True)

    # 3. schema 校验
    err = _validate_required(tool, args)
    if err:
        return finish(f"Error: {err}", is_error=True)

    # 4. 确认门（横切关注点，不在工具内部）
    #    风险可由工具按本次参数动态评估（如 run_bash 把只读命令降级为 READ → 免确认）。
    effective_risk = tool.risk_assessor(args) if tool.risk_assessor else tool.risk
    permissions = context.permissions if context is not None else PermissionController()
    if permissions.requires_approval(tool.name, effective_risk.value):
        decision = _normalize_decision(approver(tool, args) if approver else "no")
        if decision == "always":
            permissions.allow_tool(tool.name)
        if decision == "no":
            return finish(
                f"Error: 用户拒绝执行工具 '{name}'。",
                is_error=True,
                effective_risk=effective_risk.value,
            )
    t["approval_end"] = time.perf_counter()

    # 上下文注入：声明了 ctx: Context 的工具，由框架把 context 作为首个位置参传入
    if tool.wants_context and context is None:
        return finish(
            f"Error: 工具 '{name}' 需要执行上下文，但调用方未提供 context",
            is_error=True,
            effective_risk=effective_risk.value,
        )
    extra = (context,) if tool.wants_context else ()

    # 5a. 先做签名绑定校验：把「参数对不上签名」与「工具内部抛 TypeError」区分开，
    #     否则工具体内的 TypeError 会被误报成「参数错误」，把模型带去改本来正确的参数。
    try:
        inspect.signature(tool.func).bind(*extra, **args)
    except TypeError as e:
        return finish(
            f"Error: 参数与工具签名不匹配: {e}",
            is_error=True,
            effective_risk=effective_risk.value,
        )

    # PreToolUse 接缝位于目标工具确认门之后、真正执行之前。回调只能允许继续
    # 或返回模型可纠正的阻断说明，不能改写工具参数。
    if before_execute is not None:
        try:
            blocked = before_execute(tool, args, effective_risk)
        except Exception as e:
            log.exception("工具 %s 的 PreToolUse callback 异常", name)
            return finish(
                f"Error: PreToolUse callback 异常 ({type(e).__name__}): {e}",
                is_error=True,
                effective_risk=effective_risk.value,
                pre_tool_hook_error=True,
            )
        if blocked:
            return finish(
                f"Error: PreToolUse Hook 阻止了工具执行。\n\n{blocked}",
                is_error=True,
                effective_risk=effective_risk.value,
                pre_tool_hook_blocked=True,
            )

    # 5b. 执行：统一 try/except —— 任何失败都转成模型可纠错的结果，而不是让程序崩
    t["exec_start"] = time.perf_counter()
    t["executed"] = True
    try:
        raw = tool.func(*extra, **args)
    except ToolError as e:                       # 工具主动抛出的领域错误（信息最丰富）
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
    except Exception as e:                         # 兜底：任何未预期异常（含工具内部 TypeError）
        log.exception("工具 %s 执行异常", name)
        return finish(
            f"Error: 工具执行异常 ({type(e).__name__}): {e}",
            is_error=True,
            effective_risk=effective_risk.value,
        )

    # 6. 输出规整：统一成字符串 + 截断
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
