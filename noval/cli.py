"""Terminal host adapter for Noval's public Application API."""
from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from .api import (
    ConnectionUpsert,
    EventType,
    NovalError,
    PermissionDecision,
    PermissionRequest,
    RuntimeEvent,
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


class _CliStreamRenderer:
    """Render visible deltas while retaining terminal-result fallback."""

    def __init__(self) -> None:
        self._active_request_id: Optional[str] = None
        self._active_turn_id: Optional[str] = None
        self._active_text = ""
        self._last_turn_id: Optional[str] = None
        self._last_text = ""

    def handle(self, event: RuntimeEvent) -> None:
        if event.type == EventType.MODEL_OUTPUT_DELTA.value:
            text = event.payload.get("text")
            request_id = event.payload.get("request_id")
            if not isinstance(text, str) or not text:
                return
            if request_id != self._active_request_id:
                self._finish_active()
                self._active_request_id = (
                    request_id if isinstance(request_id, str) else None
                )
                self._active_turn_id = event.turn_id
                self._active_text = ""
                print(
                    _turn_prefix("Noval", use_color=_supports_color()),
                    end="",
                    flush=True,
                )
            print(text, end="", flush=True)
            self._active_text += text
            return
        if event.type == EventType.MODEL_COMPLETED.value:
            self._finish_active(completed=True)
            return
        if event.type == EventType.MODEL_OUTPUT_ABORTED.value:
            self._finish_active()

    def displayed(self, turn_id: str, text: str) -> bool:
        return self._last_turn_id == turn_id and self._last_text == text

    def _finish_active(self, *, completed: bool = False) -> None:
        if self._active_request_id is None:
            return
        print()
        if completed:
            self._last_turn_id = self._active_turn_id
            self._last_text = self._active_text
        self._active_request_id = None
        self._active_turn_id = None
        self._active_text = ""


def _choose_resume_session(sessions: Sequence[SessionInfo]) -> Optional[str]:
    if not sessions:
        return None
    shown = list(sessions[:20])
    print("\nResumable sessions:")
    for index, session in enumerate(shown, 1):
        print(
            f"  {index}. {session.title or '(untitled)'}  [{session.session_id}]  "
            f"{session.last_active or ''}  {session.message_count} messages"
        )
    answer = input("Select a number or session ID (Enter=latest, n=new): ").strip()
    if not answer:
        return shown[0].session_id
    if answer.lower() in {"n", "new"}:
        return None
    if answer.isdigit() and 1 <= int(answer) <= len(shown):
        return shown[int(answer) - 1].session_id
    matches = [item for item in sessions if item.session_id.startswith(answer)]
    if len(matches) == 1:
        return matches[0].session_id
    raise SystemExit(f"Invalid session selection: {answer}")


def _permission_status(session: AgentSession) -> str:
    state = session.permission_state()
    approved = ", ".join(state.approved_tools) or "none"
    if state.mode is PermissionMode.FULL_ACCESS:
        lines = ["Permission mode: full access (full_access)", "Tool approval: all allowed"]
        if state.approved_tools:
            lines.append(f"Approvals retained for ask mode: {approved}")
        return "\n".join(lines)
    return f"Permission mode: ask for approval (ask)\nAlways allowed in this session: {approved}"


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
                f"Unknown tool '{tool_name}'. Available tools: "
                + ", ".join(sorted(session.available_tools))
            )
        if action == "allow":
            session.allow_tool(tool_name)
        else:
            session.revoke_tool(tool_name)
        return _permission_status(session)
    return (
        "Usage: /permissions [ask|full-access|reset|"
        "allow <tool>|revoke <tool>]"
    )


def _cli_permission_handler(request: PermissionRequest) -> PermissionDecision:
    print(f"\nTool '{request.tool_name}' requests execution (risk: {request.risk})")
    print(f"    Arguments: {request.arguments}")
    always = f"[a] always allow {request.tool_name} for this session"
    if request.tool_name == "run_bash":
        always += " (including any later command)"
    answer = input(f"    Allow execution? [y] yes / {always} / [N] no ").strip().lower()
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
        return "enabled (DeepSeek default)"
    return "provider-controlled"


def _format_reasoning_summary(metrics: TurnMetrics) -> Optional[str]:
    if not metrics.model_calls or not metrics.reasoning_tokens:
        return None
    return (
        f"Reasoning: {metrics.reasoning_tokens:,} tokens · "
        f"model time {metrics.model_duration_ms / 1000:.1f}s · "
        f"{metrics.tool_calls} tool calls"
    )


def _handle_reasoning_command(
    user_input: str,
    config: Config,
    metrics: TurnMetrics,
) -> Optional[str]:
    if user_input.strip().lower() != "/reasoning":
        return None
    summary = _format_reasoning_summary(metrics) or "Last request: none"
    if summary.startswith("Reasoning: "):
        summary = "Last request: " + summary[len("Reasoning: "):]
    return (
        f"Reasoning mode: {_reasoning_mode_status(config)}\n"
        "Reasoning effort: provider-controlled\n"
        f"{summary}\n"
        "Raw reasoning trace: hidden"
    )


def _format_usage_breakdown(usage: UsageBreakdown, *, indent: str = "") -> List[str]:
    lines = [
        f"{indent}Requests: {usage.requests:,}",
        f"{indent}Input: {usage.prompt_tokens:,}",
    ]
    if usage.cache_reported:
        cache_total = usage.cache_hit_tokens + usage.cache_miss_tokens
        hit_rate = usage.cache_hit_tokens / cache_total * 100 if cache_total else 0.0
        lines.extend([
            f"{indent}  Cache hits: {usage.cache_hit_tokens:,} ({hit_rate:.1f}%)",
            f"{indent}  Cache misses: {usage.cache_miss_tokens:,}",
        ])
    lines.append(f"{indent}Output: {usage.completion_tokens:,}")
    if usage.reasoning_reported:
        lines.append(f"{indent}  Reasoning: {usage.reasoning_tokens:,}")
    lines.append(f"{indent}Total: {usage.total_tokens:,}")
    return lines


def _format_usage_summary(summary: UsageSummary) -> str:
    lines = [f"Token usage today ({summary.day.isoformat()})"]
    lines.extend(_format_usage_breakdown(summary.total))
    return "\n".join(lines)


def _handle_usage_command(
    user_input: str,
    store: Optional[JsonlUsageStore],
) -> Optional[str]:
    if user_input.strip().lower() != "/usage":
        return None
    if store is None:
        return "Token usage tracking is disabled."
    try:
        return _format_usage_summary(store.summarize())
    except Exception:
        log.warning("failed to read token usage statistics", exc_info=True)
        return "Token usage statistics are temporarily unavailable; check the runtime log."


def _parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="noval")
    parser.add_argument("--workdir", help="working directory; defaults to the launch directory")
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
        help="resume a session for the current workdir; omit the ID to open the selector",
    )
    parser.add_argument(
        "--model-id",
        help="configured model id for this Session; defaults to the global selection",
    )
    parser.add_argument(
        "--judge-model-id",
        help="configured judge model id; defaults to --model-id or the global selection",
    )
    commands = parser.add_subparsers(dest="command")
    models = commands.add_parser(
        "models", help="inspect or update model configuration"
    )
    model_commands = models.add_subparsers(
        dest="models_action", required=True
    )
    model_commands.add_parser(
        "list", help="list safe Provider and Configured Model metadata"
    )
    model_commands.add_parser(
        "validate", help="validate settings and model references"
    )
    credential = model_commands.add_parser(
        "credential", help="replace a Connection credential without echoing it"
    )
    credential.add_argument("connection_id")
    credential.add_argument(
        "--clear",
        action="store_true",
        help="remove the file credential and fall back to its environment variable",
    )
    default = model_commands.add_parser(
        "default", help="select the global default Configured Model"
    )
    default.add_argument("configured_model_id")
    return parser.parse_args(argv)


def _print_model_configuration(runtime: NovalRuntime) -> None:
    profiles = runtime.list_provider_profiles()
    configuration = runtime.get_model_configuration()
    connections = {
        connection.id: connection for connection in configuration.connections
    }
    print("Provider Profiles")
    for profile in profiles:
        suffix = "custom endpoint" if profile.kind == "custom" else "built-in"
        print(f"  {profile.id:<12} {profile.label} ({suffix})")
    print("\nConfigured Models")
    for model in configuration.configured:
        connection = connections[model.connection_id]
        default = " [default]" if model.id == configuration.default_model_id else ""
        credential = "ready" if connection.credential_available else "missing credential"
        print(
            f"  {model.id:<36} {model.label} -> {model.model} "
            f"via {connection.label} ({credential}){default}"
        )


def _run_models_command(args: argparse.Namespace, config: Config) -> None:
    runtime = NovalRuntime(config, configure_logging=True)
    try:
        action = args.models_action
        if action == "list":
            _print_model_configuration(runtime)
            return
        configuration = runtime.get_model_configuration()
        if action == "validate":
            print(
                "Model configuration is valid "
                f"(revision {configuration.revision}, "
                f"{len(configuration.connections)} connections, "
                f"{len(configuration.configured)} configured models)."
            )
            return
        if action == "default":
            updated = runtime.set_default_model(
                args.configured_model_id,
                expected_configuration_revision=configuration.revision,
            )
            print(f"Default model selected: {updated.default_model_id}")
            return
        if action == "credential":
            connection = next(
                (
                    item
                    for item in configuration.connections
                    if item.id == args.connection_id
                ),
                None,
            )
            if connection is None:
                raise SystemExit(
                    f"Connection does not exist: {args.connection_id}"
                )
            api_key = None
            if not args.clear:
                api_key = getpass.getpass("API key (input hidden): ").strip()
                if not api_key:
                    raise SystemExit("API key cannot be empty; use --clear instead.")
            runtime.upsert_connection(ConnectionUpsert(
                expected_configuration_revision=configuration.revision,
                connection_id=connection.id,
                expected_connection_revision=connection.revision,
                label=connection.label,
                profile_id=connection.profile_id,
                base_url=connection.base_url,
                api_key_env=connection.api_key_env,
                api_key=api_key,
                clear_api_key=args.clear,
            ))
            state = "cleared" if args.clear else "updated"
            print(f"Credential {state} for Connection {connection.id}.")
            return
        raise SystemExit(f"Unsupported models command: {action}")
    finally:
        runtime.close()


def run_cli(argv: Optional[List[str]] = None) -> None:
    args = _parse_args(argv)
    config = Config.load()
    if args.command == "models":
        try:
            _run_models_command(args, config)
        except NovalError as error:
            raise SystemExit(error.safe_message) from error
        return

    workdir = Path(args.workdir).expanduser().resolve() if args.workdir else Path.cwd()
    if not workdir.is_dir():
        raise SystemExit(f"--workdir is not a valid directory: {workdir}")

    if args.resume is not None and not config.persist_sessions:
        raise SystemExit("--resume requires persist_sessions=true in settings.json")
    options = SessionOptions(
        workdir=str(workdir),
        persistence=SessionPersistence.DEFAULT,
        selected_model_id=args.model_id,
        selected_judge_model_id=args.judge_model_id or args.model_id,
        sandbox_mode=SandboxMode(args.sandbox),
        network_access=NetworkAccess(args.sandbox_network),
    )

    stream_renderer = _CliStreamRenderer()
    runtime = NovalRuntime(
        config,
        configure_logging=True,
        event_sink=stream_renderer.handle,
    )
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
                print("No resumable session was selected; started a new session.")
                session = runtime.create_session(
                    options,
                    permission_handler=_cli_permission_handler,
                )
    except NovalError as error:
        runtime.close()
        raise SystemExit(error.safe_message) from error

    usage_store = (
        JsonlUsageStore(config.usage_dir(), session.info.session_id)
        if config.persist_usage else None
    )
    print(f"Noval is ready (workdir: {workdir}). Type 'exit' to quit.")
    if resumed:
        print(
            f"Resumed session {session.info.session_id} "
            f"({session.info.message_count} historical messages)"
        )
    elif session.info.persistence is SessionPersistence.PERSISTENT:
        print(f"Session persistence is enabled (session: {session.info.session_id})")
    state = session.permission_state()
    print(f"Permission mode: {state.mode.label}")
    if state.approved_tools:
        print(f"Always allowed in this session: {', '.join(state.approved_tools)}")
    print(f"Reasoning mode: {_reasoning_mode_status(config)}")

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
                _print_turn("Noval", f"[Error {error.code}: {error.safe_message}]")
                continue
            last_metrics = result.metrics
            if result.message is not None:
                reply = result.message.text
            elif result.error is not None:
                reply = f"[Error {result.error.code}: {result.error.safe_message}]"
            else:
                reply = "(No displayable result for this turn.)"
            if not stream_renderer.displayed(result.turn_id, reply):
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
    print("\nGoodbye!")


if __name__ == "__main__":
    run_cli()
    RuntimeEvent,
