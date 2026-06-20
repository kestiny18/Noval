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
from typing import Any, Callable, Dict, Optional

from .config import Config
from .tools import Tool, ToolError, ToolResult, all_tools, get_tool

log = logging.getLogger("noval.executor")

# 确认门回调：框架问「要不要执行这个工具」，由调用方（CLI/测试）决定怎么答
Approver = Callable[[Tool, Dict[str, Any]], bool]


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
) -> ToolResult:
    """执行单次工具调用，永远返回 ToolResult（绝不抛异常给上层）。"""
    started = time.perf_counter()

    def finish(content: str, *, is_error: bool = False, truncated: bool = False, **meta: Any) -> ToolResult:
        meta.update(tool=name, duration_ms=round((time.perf_counter() - started) * 1000, 1))
        log.info("tool=%s is_error=%s truncated=%s dur=%sms",
                 name, is_error, truncated, meta["duration_ms"])
        return ToolResult(content=content, is_error=is_error, truncated=truncated, meta=meta)

    # 1. 查工具：未知工具要把可用清单告诉模型，让它能改用正确的工具
    tool = get_tool(name)
    if tool is None:
        available = ", ".join(t.name for t in all_tools()) or "(无)"
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
    if config.needs_confirmation(tool.risk):
        approved = approver(tool, args) if approver else False  # 无 approver 时默认拒绝(安全)
        if not approved:
            return finish(f"Error: 用户拒绝执行工具 '{name}'。", is_error=True)

    # 5a. 先做签名绑定校验：把「参数对不上签名」与「工具内部抛 TypeError」区分开，
    #     否则工具体内的 TypeError 会被误报成「参数错误」，把模型带去改本来正确的参数。
    try:
        inspect.signature(tool.func).bind(**args)
    except TypeError as e:
        return finish(f"Error: 参数与工具签名不匹配: {e}", is_error=True)

    # 5b. 执行：统一 try/except —— 任何失败都转成模型可纠错的结果，而不是让程序崩
    try:
        raw = tool.func(**args)
    except ToolError as e:                       # 工具主动抛出的领域错误（信息最丰富）
        return finish(f"Error: {e}", is_error=True)
    except Exception as e:                         # 兜底：任何未预期异常（含工具内部 TypeError）
        log.exception("工具 %s 执行异常", name)
        return finish(f"Error: 工具执行异常 ({type(e).__name__}): {e}", is_error=True)

    # 6. 输出规整：统一成字符串 + 截断
    content = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, default=str)
    original_chars = len(content)
    content, truncated = _truncate(content, config.max_tool_output_chars)
    return finish(content, truncated=truncated, original_chars=original_chars)
