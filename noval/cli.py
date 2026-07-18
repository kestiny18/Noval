"""Terminal host adapter for Noval's public Application API."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from .api import (
    NovalError,
    PermissionDecision,
    PermissionRequest,
    SessionInfo,
    SessionOptions,
    SessionPersistence,
    TurnMetrics,
    TurnRequest,
    TurnStatus,
)
from .application import AgentSession, NovalRuntime
from .config import Config
from .permissions import PermissionMode
from .process import NetworkAccess, SandboxMode
from .runtime_log import setup_runtime_logging
from .usage import JsonlUsageStore, UsageBreakdown, UsageSummary


log = logging.getLogger("noval.cli")
_TURN_LABEL_WIDTH = 6
_TURN_COLORS = {"You": "\033[1;36m", "Noval": "\033[1;32m"}
_ANSI_RESET = "\033[0m"


def _supports_color(stream=None) -> bool:
    target = stream or sys.stdout
    return bool(getattr(target, "isatty", lambda: False)()) and (
        os.name != "nt" or bool(os.environ.get("WT_SESSION"))
    )


def _turn_prefix(label: str, use_color: bool = False) -> str:
    visible = f"{label:<{_TURN_LABEL_WIDTH}}> "
    color = _TURN_COLORS.get(label)
    if use_color and color:
        return f"{color}{visible[:-1]}{_ANSI_RESET} "
    return visible


def _format_turn(label: str, text: str, use_color: bool = False) -> str:
    plain_prefix = _turn_prefix(label)
    lines = str(text).splitlines() or [""]
    continuation = " " * len(plain_prefix)
    rendered = [_turn_prefix(label, use_color) + lines[0]]
    rendered.extend(continuation + line for line in lines[1:])
    return "\n".join(rendered)


def _print_turn(label: str, text: str) -> None:
    print(_format_turn(label, text, use_color=_supports_color()))


def _read_turn(label: str) -> str:
    print(_turn_prefix(label, use_color=_supports_color()), end="", flush=True)
    return input()


def _choose_resume_session(sessions: Sequence[SessionInfo]) -> Optional[str]:
    if not sessions:
        return None
    shown = list(sessions[:20])
    print("\n可恢复的会话：")
    for index, session in enumerate(shown, 1):
        compatibility = "" if session.compatible else "  [不可恢复]"
        print(
            f"  {index}. {session.title or '(无标题)'}  [{session.session_id}]  "
            f"{session.last_active or ''}  {session.message_count} 条{compatibility}"
        )
    answer = input("选择编号/session id（回车=最近，n=新会话）: ").strip()
    if not answer:
        selected = next((item for item in shown if item.compatible), None)
        return selected.session_id if selected is not None else None
    if answer.lower() in {"n", "new"}:
        return None
    if answer.isdigit() and 1 <= int(answer) <= len(shown):
        selected = shown[int(answer) - 1]
        if not selected.compatible:
            raise SystemExit(
                f"会话 {selected.session_id} 使用不兼容的 schema，不能恢复"
            )
        return selected.session_id
    matches = [item for item in sessions if item.session_id.startswith(answer)]
    if len(matches) == 1:
        if not matches[0].compatible:
            raise SystemExit(
                f"会话 {matches[0].session_id} 使用不兼容的 schema，不能恢复"
            )
        return matches[0].session_id
    raise SystemExit(f"无效的会话选择: {answer}")


def _permission_status(session: AgentSession) -> str:
    state = session.permission_state()
    approved = ", ".join(state.approved_tools) or "无"
    if state.mode is PermissionMode.FULL_ACCESS:
        lines = ["权限模式: 完全访问 (full_access)", "工具审批: 全部允许"]
        if state.approved_tools:
            lines.append(f"请求批准模式保留授权: {approved}")
        return "\n".join(lines)
    return f"权限模式: 请求批准 (ask)\n本会话始终允许: {approved}"


def _handle_permissions_command(
    user_input: str,
    session: AgentSession,
) -> Optional[str]:
    parts = user_input.strip().split()
    if not parts or parts[0].lower() != "/permissions":
        return None
    if len(parts) == 1:
        return _permission_status(session)
    action = parts[1].lower().replace("-", "_")
    if len(parts) == 2 and action in {"ask", "request", "request_approval"}:
        session.set_permission_mode(PermissionMode.ASK)
        return _permission_status(session)
    if len(parts) == 2 and action in {"full", "full_access"}:
        session.set_permission_mode(PermissionMode.FULL_ACCESS)
        return _permission_status(session)
    if len(parts) == 2 and action == "reset":
        session.reset_permissions()
        return _permission_status(session)
    if len(parts) == 3 and action in {"allow", "revoke"}:
        tool_name = parts[2]
        if action == "allow" and tool_name not in session.available_tools:
            return (
                f"未知工具 '{tool_name}'。可用工具: "
                + ", ".join(sorted(session.available_tools))
            )
        if action == "allow":
            session.allow_tool(tool_name)
        else:
            session.revoke_tool(tool_name)
        return _permission_status(session)
    return (
        "用法: /permissions [ask|full-access|reset|"
        "allow <tool>|revoke <tool>]"
    )


def _cli_permission_handler(request: PermissionRequest) -> PermissionDecision:
    print(f"\n工具 '{request.tool_name}' (风险: {request.risk}) 请求执行")
    print(f"    参数: {request.arguments}")
    always = f"[a]本会话总是允许 {request.tool_name}"
    if request.tool_name == "run_bash":
        always += "（包括后续任意命令）"
    answer = input(f"    允许执行? [y]是 / {always} / [N]否 ").strip().lower()
    if answer in {"a", "always"}:
        return PermissionDecision.ALLOW_SESSION
    if answer in {"y", "yes"}:
        return PermissionDecision.ALLOW_ONCE
    return PermissionDecision.DENY


def _reasoning_mode_status(config: Config) -> str:
    if config.provider == "openai-compatible" and (
        "deepseek" in config.base_url.lower()
        or config.model.lower().startswith("deepseek")
    ):
        return "已开启（DeepSeek 默认）"
    return "由 Provider 决定"


def _format_reasoning_summary(metrics: TurnMetrics) -> Optional[str]:
    if not metrics.model_calls or not metrics.reasoning_tokens:
        return None
    return (
        f"思考: {metrics.reasoning_tokens:,} reasoning tokens · "
        f"模型耗时 {metrics.model_duration_ms / 1000:.1f}s · "
        f"{metrics.tool_calls} 次工具调用"
    )


def _handle_reasoning_command(
    user_input: str,
    config: Config,
    metrics: TurnMetrics,
) -> Optional[str]:
    if user_input.strip().lower() != "/reasoning":
        return None
    summary = _format_reasoning_summary(metrics) or "上次请求: 无"
    if summary.startswith("思考: "):
        summary = "上次请求: " + summary[len("思考: "):]
    return (
        f"思考模式: {_reasoning_mode_status(config)}\n"
        "思考强度: Provider 自动\n"
        f"{summary}\n"
        "原始思考过程: 不展示"
    )


def _format_usage_breakdown(usage: UsageBreakdown, *, indent: str = "") -> List[str]:
    lines = [
        f"{indent}请求次数: {usage.requests:,}",
        f"{indent}输入: {usage.prompt_tokens:,}",
    ]
    if usage.cache_reported:
        cache_total = usage.cache_hit_tokens + usage.cache_miss_tokens
        hit_rate = usage.cache_hit_tokens / cache_total * 100 if cache_total else 0.0
        lines.extend([
            f"{indent}  缓存命中: {usage.cache_hit_tokens:,} ({hit_rate:.1f}%)",
            f"{indent}  缓存未命中: {usage.cache_miss_tokens:,}",
        ])
    lines.append(f"{indent}输出: {usage.completion_tokens:,}")
    if usage.reasoning_reported:
        lines.append(f"{indent}  其中 reasoning: {usage.reasoning_tokens:,}")
    lines.append(f"{indent}总计: {usage.total_tokens:,}")
    return lines


def _format_usage_summary(summary: UsageSummary) -> str:
    lines = [f"今日 Token 使用 ({summary.day.isoformat()})"]
    lines.extend(_format_usage_breakdown(summary.total))
    return "\n".join(lines)


def _handle_usage_command(
    user_input: str,
    store: Optional[JsonlUsageStore],
) -> Optional[str]:
    if user_input.strip().lower() != "/usage":
        return None
    if store is None:
        return "Token 统计已关闭。"
    try:
        return _format_usage_summary(store.summarize())
    except Exception:
        log.warning("读取 token 用量统计失败", exc_info=True)
        return "Token 统计暂时无法读取，请查看运行日志。"


def _parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="noval")
    parser.add_argument("--workdir", help="工作目录；不指定则用当前启动目录")
    parser.add_argument(
        "--sandbox",
        choices=[mode.value for mode in SandboxMode],
        default=SandboxMode.AUTO.value,
    )
    parser.add_argument(
        "--sandbox-network",
        choices=[access.value for access in NetworkAccess],
        default=NetworkAccess.INHERIT.value,
    )
    parser.add_argument(
        "--resume",
        nargs="?",
        const="",
        metavar="SESSION_ID",
        help="恢复当前 workdir 的历史会话；不填 ID 时进入选择器",
    )
    return parser.parse_args(argv)


def run_cli(argv: Optional[List[str]] = None) -> None:
    args = _parse_args(argv)
    workdir = Path(args.workdir).expanduser().resolve() if args.workdir else Path.cwd()
    if not workdir.is_dir():
        raise SystemExit(f"--workdir 不是有效目录: {workdir}")

    config = Config.load()
    if args.resume is not None and not config.persist_sessions:
        raise SystemExit("--resume 需要 settings.json 中 persist_sessions=true")
    options = SessionOptions(
        workdir=str(workdir),
        persistence=SessionPersistence.DEFAULT,
        sandbox_mode=SandboxMode(args.sandbox),
        network_access=NetworkAccess(args.sandbox_network),
    )

    runtime = NovalRuntime(config)
    session: Optional[AgentSession] = None
    resumed = False
    try:
        if args.resume is None:
            session = runtime.create_session(
                options,
                permission_handler=_cli_permission_handler,
            )
        else:
            session_id = args.resume.strip()
            if not session_id:
                session_id = _choose_resume_session(
                    runtime.list_persisted_sessions(str(workdir))
                ) or ""
            if session_id:
                session = runtime.resume_session(
                    session_id,
                    options,
                    permission_handler=_cli_permission_handler,
                )
                resumed = True
            else:
                print("没有选择可恢复会话，已开启新会话。")
                session = runtime.create_session(
                    options,
                    permission_handler=_cli_permission_handler,
                )
    except NovalError as error:
        runtime.close()
        raise SystemExit(error.safe_message) from error

    setup_runtime_logging(config, session.info.session_id)
    usage_store = (
        JsonlUsageStore(config.usage_dir(), session.info.session_id)
        if config.persist_usage else None
    )
    print(f"Noval 已就绪 (workdir: {workdir})。输入 'exit' 退出。")
    if resumed:
        print(
            f"已恢复会话 {session.info.session_id}"
            f"（{session.info.message_count} 条历史消息）"
        )
    elif session.info.persistence is SessionPersistence.PERSISTENT:
        print(f"会话持久化已开启（session: {session.info.session_id}）")
    state = session.permission_state()
    print(f"权限模式: {state.mode.label}")
    if state.approved_tools:
        print(f"本会话始终允许: {', '.join(state.approved_tools)}")
    print(f"思考模式: {_reasoning_mode_status(config)}")

    last_metrics = TurnMetrics()
    try:
        while True:
            try:
                print()
                user_input = _read_turn("You")
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not user_input.strip():
                continue
            if user_input.strip().lower() == "exit":
                break
            local_reply = _handle_permissions_command(user_input, session)
            if local_reply is None:
                local_reply = _handle_reasoning_command(
                    user_input, config, last_metrics
                )
            if local_reply is None:
                local_reply = _handle_usage_command(user_input, usage_store)
            if local_reply is not None:
                print()
                _print_turn("Noval", local_reply)
                continue

            try:
                result = session.run_turn(TurnRequest(user_input))
            except NovalError as error:
                print()
                _print_turn("Noval", f"[出错 {error.code}: {error.safe_message}]")
                continue
            last_metrics = result.metrics
            if result.message is not None:
                reply = result.message.text
            elif result.error is not None:
                reply = f"[出错 {result.error.code}: {result.error.safe_message}]"
            else:
                reply = "（本轮没有可显示的结果。）"
            print()
            _print_turn("Noval", reply)
            summary = _format_reasoning_summary(result.metrics)
            if summary:
                print(" " * len(_turn_prefix("Noval")) + summary)
            if result.status is TurnStatus.FAILED:
                log.info("turn failed code=%s", result.error.code if result.error else "unknown")
    finally:
        session.close()
        runtime.close()
    print("\n再见！")


if __name__ == "__main__":
    run_cli()
