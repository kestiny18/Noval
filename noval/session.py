"""Session persistence (seam 4), independent of the storage backend.

See DESIGN.md decision 18. The contract is:
  - **Persistent state**: every canonical user/assistant/tool block message,
    stored append-only.
  - **Derived state**: system identity, environment, and project instructions
    are rebuilt from the current environment instead of being stored.
  - **Shape**: schema v2 uses one `.jsonl` file per session, with
    `{seq, ts, message}` envelopes after an initial `{_meta}` record. Timestamps
    stay in the envelope and never enter canonical messages or provider input.

`agent.py` depends only on the injected `SessionStore` protocol, just as it
depends on the LLM client and permission handler. Replacing the database means
adding an adapter, without changing the agent loop.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Tuple

from .messages import ConversationMessage, MessageFormatError, MessageRole

log = logging.getLogger("noval.session")

SCHEMA_VERSION = 2
_TITLE_MAXLEN = 60
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


# ---------------------------------------------------------------------------
# The agent-facing seam only appends to and reads from the current session.
# ---------------------------------------------------------------------------
class SessionStore(Protocol):
    def append(self, message: ConversationMessage) -> None: ...
    def load(self) -> List[ConversationMessage]: ...


class SessionMetadataStore(Protocol):
    def load_metadata(self) -> Dict[str, Any]: ...       # Mutable session sidecar attributes.
    def update_metadata(self, updates: Dict[str, Any]) -> None: ...


class PersistentSessionStore(SessionStore, SessionMetadataStore, Protocol):
    """Full CLI persistence contract; the agent loop still uses SessionStore."""

    session_id: str

    def load_records(self) -> List["SessionRecord"]: ...
    def load_record_page(
        self, after_seq: int, limit: int
    ) -> Tuple[List["SessionRecord"], bool]: ...
    def context_path(self) -> Path: ...
    def task_path(self) -> Path: ...
    def request_path(self) -> Path: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class SessionRecord:
    """Canonical message with its append-only persistence envelope."""

    seq: int
    ts: str
    message: ConversationMessage


@dataclass
class SessionMeta:
    """Message-free session summary for the --resume selector."""
    session_id: str
    created_at: str
    last_active: str       # Derived from the .jsonl mtime instead of stored.
    title: str
    message_count: int
    model: str
    compatible: bool = True
    schema_version: Optional[int] = SCHEMA_VERSION
    provider: str = ""


@dataclass(frozen=True)
class PersistedProjectMeta:
    """Project inventory derived from message-bearing Session directories."""

    workdir: str
    created_at: str
    session_count: int
    available: bool


class UnsupportedSessionVersion(ValueError):
    def __init__(self, session_id: str, version: Any):
        self.session_id = session_id
        self.version = version
        super().__init__(
            f"Session {session_id} uses incompatible schema v{version}; "
            f"this Noval version reads only schema v{SCHEMA_VERSION}, and the original file was not modified"
        )


class SessionLockedError(RuntimeError):
    def __init__(self, session_id: str):
        self.session_id = session_id
        super().__init__(
            f"Session {session_id} is already open in another process and cannot be written concurrently"
        )


class _WriterLease:
    """Cross-platform non-blocking advisory lock held by an open file handle."""

    def __init__(self, path: Path, session_id: str):
        self.path = path
        self.session_id = session_id
        self._file = None

    def acquire(self) -> None:
        if self._file is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file = self.path.open("a+b")
        try:
            if sys.platform == "win32":
                import msvcrt

                if file.seek(0, os.SEEK_END) == 0:
                    file.write(b"\0")
                    file.flush()
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError) as error:
            file.close()
            raise SessionLockedError(self.session_id) from error
        self._file = file

    def release(self) -> None:
        file = self._file
        if file is None:
            return
        try:
            file.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(file.fileno(), fcntl.LOCK_UN)
        finally:
            file.close()
            self._file = None


# ---------------------------------------------------------------------------
# Addressing: ~/.noval/sessions/<workdir-hash>/, with the real path in project.json.
# ---------------------------------------------------------------------------
def _project_hash(workdir: Path) -> str:
    """Map an absolute workdir to a short, filesystem-safe directory name."""
    return hashlib.sha256(str(workdir.resolve()).encode("utf-8")).hexdigest()[:16]


def _project_dir(base_dir: Path, workdir: Path) -> Path:
    return base_dir / _project_hash(workdir)


def _now_iso() -> str:
    """Return timezone-aware ISO 8601 without cross-timezone or DST ambiguity."""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _atomic_write(path: Path, text: str) -> None:
    """Atomically replace a small file through a temporary file and os.replace.

    Append-only logs do not use this path. It is reserved for small files that
    are replaced as a whole, such as project.json and metadata sidecars.
    """
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _derive_title(user_content: str) -> str:
    """Derive a title from the first user message after removing context metadata."""
    stripped = re.sub(r"^<context>.*?</context>\s*", "", user_content, flags=re.S).strip()
    first_line = stripped.splitlines()[0] if stripped else ""
    if len(first_line) > _TITLE_MAXLEN:
        first_line = first_line[:_TITLE_MAXLEN - 1] + "…"
    return first_line or "(untitled)"


def _iter_records(path: Path):
    """Parse JSONL records, skipping corrupt lines without losing the session."""
    try:
        fh = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return
    with fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                log.warning("skipping corrupt session line %s:%d", path.name, lineno)


# ---------------------------------------------------------------------------
# JSONL adapter: one append-only file per session.
# ---------------------------------------------------------------------------
class JsonlSessionStore:
    """Storage handle bound to one session.

    - create(): lazily creates a new session on the first appended message.
    - open(): resumes an existing session and continues after its last sequence.
    """

    def __init__(self, base_dir: Path, workdir: Path, session_id: str, model: str):
        if not _SESSION_ID_RE.fullmatch(session_id):
            raise ValueError(f"invalid session ID: {session_id!r}")
        self.base_dir = Path(base_dir)
        self.workdir = Path(workdir)
        self.session_id = session_id
        self.model = model
        self._dir = _project_dir(self.base_dir, self.workdir)
        self._path = self._dir / f"{session_id}.jsonl"
        self._meta_path = self._dir / f"{session_id}.meta.json"
        self._next_seq = 0
        self._header_written = False
        self._fh = None  # type: ignore[assignment]  # Lazily opened append handle.
        self._pending_metadata: Optional[Dict[str, Any]] = None
        self._lease = _WriterLease(
            self._dir / "locks" / f"{session_id}.lock",
            session_id,
        )

    # --- Construction ------------------------------------------------------
    @classmethod
    def create(cls, base_dir: Path, workdir: Path, model: str) -> "JsonlSessionStore":
        """Create a session lazily so an unused session never touches disk."""
        sid = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
        return cls(base_dir, workdir, sid, model)

    @classmethod
    def open(cls, base_dir: Path, workdir: Path, session_id: str, model: str) -> "JsonlSessionStore":
        """Resume a session and advance _next_seq beyond its persisted records."""
        store = cls(base_dir, workdir, session_id, model)
        if not store._path.exists():
            raise FileNotFoundError(f"session not found: {session_id}")
        version = _session_schema_version(store._path)
        if version != SCHEMA_VERSION:
            raise UnsupportedSessionVersion(session_id, version if version is not None else "unknown")
        last = -1
        for rec in _iter_records(store._path):
            if isinstance(rec.get("seq"), int):
                last = max(last, rec["seq"])
        store._next_seq = last + 1
        store._header_written = True
        store._lease.acquire()
        return store

    # --- Write -------------------------------------------------------------
    def append(self, message: ConversationMessage) -> None:
        """Append one message, creating the directory, file, and header lazily.

        Each line is flushed immediately. A hard crash can lose at most the
        incomplete tail record, which _iter_records tolerates.
        """
        if self._fh is None:
            self._open_for_append()
        line = json.dumps(
            {"seq": self._next_seq, "ts": _now_iso(), "message": message.to_dict()},
            ensure_ascii=False,
        )
        self._fh.write(line + "\n")
        self._fh.flush()
        self._next_seq += 1

    def _open_for_append(self) -> None:
        self._lease.acquire()
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._ensure_project_json()
            new_file = not self._path.exists()
            self._fh = self._path.open("a", encoding="utf-8")
        except Exception:
            self._lease.release()
            raise
        try:                       # 0600 protects pasted secrets and file content; a no-op on Windows.
            os.chmod(self._path, 0o600)
        except OSError:
            pass
        if new_file and not self._header_written:
            header = {"_meta": {
                "schema_version": SCHEMA_VERSION,
                "session_id": self.session_id,
                "created_at": _now_iso(),
                "workdir": str(self.workdir.resolve()),
                "model": self.model,
            }}
            self._fh.write(json.dumps(header, ensure_ascii=False) + "\n")
            self._fh.flush()
            self._header_written = True
        self._flush_metadata()

    def _ensure_project_json(self) -> None:
        """Write display-only metadata that maps the hash directory to its path."""
        pj = self._dir / "project.json"
        if pj.exists():
            return
        _atomic_write(pj, json.dumps(
            {"real_workdir": str(self.workdir.resolve()), "created_at": _now_iso()},
            ensure_ascii=False,
        ))

    def set_title(self, title: str) -> None:
        """Rename through the mutable sidecar, outside the append-only log."""
        self.update_metadata({"title": title})

    def load_metadata(self) -> Dict[str, Any]:
        """Read the sidecar, treating missing or corrupt content as empty."""
        if self._pending_metadata is not None:
            return dict(self._pending_metadata)
        return _read_json_object(self._meta_path)

    def update_metadata(self, updates: Dict[str, Any]) -> None:
        """Merge sidecar updates while preserving lazy creation for new sessions."""
        data = self.load_metadata()
        data.update(updates)
        self._pending_metadata = data
        if self._path.exists():
            self._flush_metadata()

    def _flush_metadata(self) -> None:
        if self._pending_metadata is None:
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(
            self._meta_path,
            json.dumps(self._pending_metadata, ensure_ascii=False),
        )
        self._pending_metadata = None

    def context_path(self) -> Path:
        """Return the derived context checkpoint path, separate from raw messages."""
        return self._dir / "context" / f"{self.session_id}.jsonl"

    def task_path(self) -> Path:
        """Return the append-only path for derived task-completion events."""
        return self._dir / "task" / f"{self.session_id}.jsonl"

    def request_path(self) -> Path:
        """Return the model request journal path, separate from the canonical session."""
        return self._dir / "requests" / f"{self.session_id}.jsonl"

    # --- Read --------------------------------------------------------------
    def load_records(self) -> List[SessionRecord]:
        """Load valid message envelopes, retaining seq and ts for checkpoints."""
        records, _ = self.load_record_page(-1, sys.maxsize)
        return records

    def load_record_page(
        self,
        after_seq: int,
        limit: int,
    ) -> Tuple[List[SessionRecord], bool]:
        """Scan a bounded record page without materializing the complete Session."""
        records: List[SessionRecord] = []
        for rec in _iter_records(self._path):
            if "_meta" in rec:
                continue
            seq = rec.get("seq")
            ts = rec.get("ts")
            raw_message = rec.get("message")
            if not isinstance(seq, int) or not isinstance(ts, str):
                continue
            try:
                message = ConversationMessage.from_dict(raw_message)
            except MessageFormatError:
                log.warning("skipping corrupt canonical session message: %s seq=%s", self._path, seq)
                continue
            if seq <= after_seq:
                continue
            records.append(SessionRecord(seq=seq, ts=ts, message=message))
            if len(records) > limit:
                return records[:limit], True
        return records, False

    def load(self) -> List[ConversationMessage]:
        return [record.message for record in self.load_records()]

    def close(self) -> None:
        """Flush and release the append handle; safe to call more than once."""
        try:
            if self._fh is not None:
                self._fh.close()
                self._fh = None
        finally:
            self._lease.release()


# ---------------------------------------------------------------------------
# Project-level session listing scans the source-of-truth files without an index.
# ---------------------------------------------------------------------------
def list_sessions(base_dir: Path, workdir: Path) -> List[SessionMeta]:
    """List workdir sessions by most recent activity for the resume selector."""
    pdir = _project_dir(Path(base_dir), Path(workdir))
    if not pdir.is_dir():
        return []
    metas: List[SessionMeta] = []
    for path in pdir.glob("*.jsonl"):
        meta = _read_session_meta(path)
        if meta is not None:
            metas.append(meta)
    metas.sort(key=lambda m: m.last_active, reverse=True)
    return metas


def list_persisted_projects(base_dir: Path) -> List[PersistedProjectMeta]:
    """List projects recorded by canonical Session storage in stable order."""
    root = Path(base_dir)
    if not root.is_dir():
        return []
    projects: List[PersistedProjectMeta] = []
    for project_dir in root.iterdir():
        if not project_dir.is_dir():
            continue
        data = _read_json_object(project_dir / "project.json")
        workdir = data.get("real_workdir")
        created_at = data.get("created_at")
        if not isinstance(workdir, str) or not workdir.strip():
            continue
        if not isinstance(created_at, str) or not created_at.strip():
            continue
        session_count = sum(1 for _ in project_dir.glob("*.jsonl"))
        if session_count < 1:
            continue
        projects.append(PersistedProjectMeta(
            workdir=workdir,
            created_at=created_at,
            session_count=session_count,
            available=Path(workdir).expanduser().is_dir(),
        ))
    projects.sort(key=lambda item: (item.created_at, item.workdir.casefold()))
    return projects


def _read_session_meta(path: Path) -> Optional[SessionMeta]:
    """Read a summary from metadata, the first user message, and file mtime."""
    session_id = path.stem
    created_at = ""
    model = ""
    first_user = ""
    msg_count = 0
    schema_version: Optional[int] = None
    for rec in _iter_records(path):
        if "_meta" in rec:
            m = rec["_meta"]
            if not isinstance(m, dict):
                continue
            schema_version = m.get("schema_version")
            created_at = m.get("created_at", "")
            model = m.get("model", "")
            session_id = m.get("session_id", session_id)
            continue
        raw_message = rec.get("message")
        if schema_version == SCHEMA_VERSION and isinstance(raw_message, dict):
            try:
                message = ConversationMessage.from_dict(raw_message)
            except MessageFormatError:
                continue
            msg_count += 1
            if not first_user and message.role is MessageRole.USER:
                first_user = message.text
        elif "msg" in rec or "message" in rec:
            msg_count += 1
    try:
        last_active = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")
    except OSError:
        last_active = created_at

    # Prefer a custom sidecar title; otherwise derive it from the first user message.
    compatible = schema_version == SCHEMA_VERSION
    sidecar = _read_json_object(path.parent / (path.stem + ".meta.json"))
    title_value = sidecar.get("title")
    title = title_value if isinstance(title_value, str) and title_value.strip() else None
    application = sidecar.get("application")
    provider = (
        application.get("provider", "")
        if isinstance(application, dict) and isinstance(application.get("provider", ""), str)
        else ""
    )
    if not compatible:
        title = f"[incompatible v{schema_version if schema_version is not None else '?'}] {title or path.stem}"
    elif title is None:
        title = _derive_title(first_user) if first_user else "(empty session)"
    return SessionMeta(
        session_id, created_at, last_active, title, msg_count, model,
        compatible=compatible, schema_version=schema_version, provider=provider,
    )


def _session_schema_version(path: Path) -> Optional[int]:
    for record in _iter_records(path):
        meta = record.get("_meta")
        if isinstance(meta, dict):
            version = meta.get("schema_version")
            return version if isinstance(version, int) and not isinstance(version, bool) else None
        return None
    return None


def _read_json_object(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}
