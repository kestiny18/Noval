"""Redacted persistent runtime logging for Noval."""
from __future__ import annotations

import logging
import contextvars
import os
import re
import shutil
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

from .config import Config


_DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b(api[_-]?key|authorization|token|secret|password|passwd)"
               r"\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)
_SESSION_ID = contextvars.ContextVar("noval_log_session_id", default="-")
_TURN_ID = contextvars.ContextVar("noval_log_turn_id", default="-")
_REQUEST_ID = contextvars.ContextVar("noval_log_request_id", default="-")


@contextmanager
def runtime_log_context(
    *,
    session_id: Optional[str] = None,
    turn_id: Optional[str] = None,
    request_id: Optional[str] = None,
):
    values = (
        (_SESSION_ID, session_id),
        (_TURN_ID, turn_id),
        (_REQUEST_ID, request_id),
    )
    tokens = [
        (variable, variable.set(value))
        for variable, value in values if value is not None
    ]
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.noval_session_id = _SESSION_ID.get()
        record.noval_turn_id = _TURN_ID.get()
        record.noval_request_id = _REQUEST_ID.get()
        return True


def redact_text(text: str) -> str:
    """Best-effort redaction for common credential shapes."""
    redacted = str(text)
    redacted = _SECRET_PATTERNS[0].sub(r"\1 <redacted>", redacted)
    redacted = _SECRET_PATTERNS[1].sub(r"\1=<redacted>", redacted)
    redacted = _SECRET_PATTERNS[2].sub("sk-<redacted>", redacted)
    return redacted


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # A previous handler may have cached an unredacted exception string on the record.
        record.exc_text = None
        return redact_text(super().format(record))

    def formatException(self, exc_info) -> str:
        """Keep call sites for diagnosis but omit exception values from disk."""
        frames = traceback.extract_tb(exc_info[2])
        rendered = "\n".join(
            f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}'
            for frame in frames
        )
        exception_type = getattr(exc_info[0], "__name__", "Exception")
        return f"{rendered}\n{exception_type}: <details redacted>"


def cleanup_old_logs(base_dir: Path, retention_days: int, today: date) -> None:
    """Remove only expired YYYY-MM-DD directories owned by runtime logging."""
    if retention_days < 1 or not base_dir.is_dir():
        return
    cutoff = today - timedelta(days=retention_days - 1)
    for child in base_dir.iterdir():
        if not child.is_dir() or not _DATE_DIR.fullmatch(child.name):
            continue
        try:
            child_date = date.fromisoformat(child.name)
        except ValueError:
            continue
        if child_date < cutoff:
            shutil.rmtree(child)


def _log_path(config: Config, session_id: Optional[str], now: datetime) -> Path:
    name = session_id or now.strftime("%H%M%S")
    safe_name = _SAFE_NAME.sub("-", name).strip("-.") or "session"
    return config.logs_dir() / now.strftime("%Y-%m-%d") / f"noval-{safe_name}-{os.getpid()}.log"


def setup_runtime_logging(
    config: Config,
    session_id: Optional[str] = None,
    *,
    level: int = logging.INFO,
    now: Optional[datetime] = None,
) -> Optional[Path]:
    """Configure console logging and an optional redacted per-process log file."""
    current = now or datetime.now().astimezone()
    console = logging.StreamHandler()
    correlation = CorrelationFilter()
    console.addFilter(correlation)
    console.setFormatter(logging.Formatter(
        "%(levelname)s %(name)s "
        "session=%(noval_session_id)s turn=%(noval_turn_id)s "
        "request=%(noval_request_id)s: %(message)s"
    ))
    handlers: list[logging.Handler] = [console]
    path: Optional[Path] = None
    file_error: Optional[OSError] = None

    if config.persist_logs:
        try:
            base_dir = config.logs_dir()
            base_dir.mkdir(parents=True, exist_ok=True)
            cleanup_old_logs(base_dir, config.log_retention_days, current.date())
            path = _log_path(config, session_id, current)
            path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(path, encoding="utf-8", delay=True)
            file_handler.addFilter(correlation)
            file_handler.setFormatter(RedactingFormatter(
                "%(asctime)s %(levelname)s %(name)s pid=%(process)d "
                "session=%(noval_session_id)s turn=%(noval_turn_id)s "
                "request=%(noval_request_id)s: %(message)s"
            ))
            handlers.append(file_handler)
        except OSError as exc:
            file_error = exc
            path = None

    logging.basicConfig(level=level, handlers=handlers, force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    if file_error is not None:
        logging.getLogger("noval.runtime_log").warning(
            "runtime log file is unavailable: %s", file_error
        )
    return path
