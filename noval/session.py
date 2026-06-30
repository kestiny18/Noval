"""会话持久化（接缝4）—— 与具体存储后端解耦的「会话日志」抽象。

设计见 DESIGN.md 决策 18。一句话契约：
  - **持久态**：对话轮次(user/assistant含tool_calls/tool)，一条不少，append-only。
  - **派生态**：system(人设+env+项目记忆) 不存，恢复时按当前环境重建。
  - **形状**：每会话一个 `.jsonl`，每行一个信封 `{seq, ts, msg}`，首行 `{_meta}`。
        时间(ts)在信封层、不进 msg —— 否则会跟着 replay 污染 wire 格式。

`agent.py` 只依赖 `SessionStore` 协议(像依赖 LLMClient/approver 一样注入)，
永不直接 json.dump。换 DB = 写一个新适配器，循环与 Agent 一行不动。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

log = logging.getLogger("noval.session")

SCHEMA_VERSION = 1
_TITLE_MAXLEN = 60
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


# ---------------------------------------------------------------------------
# Agent 看到的接缝：只有「往当前会话追加」和「读回当前会话」两件事
# ---------------------------------------------------------------------------
class SessionStore(Protocol):
    def append(self, msg: Dict[str, Any]) -> None: ...   # 追加一条消息(store 自盖 ts/seq)
    def load(self) -> List[Dict[str, Any]]: ...          # 读回 msg 序列(已剥信封)


class SessionMetadataStore(Protocol):
    def load_metadata(self) -> Dict[str, Any]: ...       # sidecar 可变会话属性
    def update_metadata(self, updates: Dict[str, Any]) -> None: ...


class PersistentSessionStore(SessionStore, SessionMetadataStore, Protocol):
    """CLI 持久化会话所需的完整能力；Agent 循环仍只依赖 SessionStore。"""


@dataclass
class SessionMeta:
    """给 --resume 选择器看的会话摘要（不含正文）。"""
    session_id: str
    created_at: str
    last_active: str       # 取 .jsonl 文件 mtime，不另存
    title: str
    message_count: int
    model: str


# ---------------------------------------------------------------------------
# 寻址：全局 ~/.noval/sessions/<workdir-hash>/，真实路径写进 project.json 反查
# ---------------------------------------------------------------------------
def _project_hash(workdir: Path) -> str:
    """workdir 绝对路径 → 文件系统安全的目录名（裸路径太长/含非法字符/跨机冲突）。"""
    return hashlib.sha256(str(workdir.resolve()).encode("utf-8")).hexdigest()[:16]


def _project_dir(base_dir: Path, workdir: Path) -> Path:
    return base_dir / _project_hash(workdir)


def _now_iso() -> str:
    """带时区的 ISO8601（跨时区/DST 不歧义）。"""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _atomic_write(path: Path, text: str) -> None:
    """小文件原子写：临时文件 + os.replace（Windows 上 replace 也能覆盖目标）。
    append-only 日志不走这里；只有 project.json / sidecar 这种「整体覆盖」的小文件用。"""
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _derive_title(user_content: str) -> str:
    """无自定义标题时，从首条 user 消息派生：剥掉 <context> 时间前缀 + 截断。"""
    stripped = re.sub(r"^<context>.*?</context>\s*", "", user_content, flags=re.S).strip()
    first_line = stripped.splitlines()[0] if stripped else ""
    if len(first_line) > _TITLE_MAXLEN:
        first_line = first_line[:_TITLE_MAXLEN] + "…"
    return first_line or "(无标题)"


def _iter_records(path: Path):
    """逐行解析 .jsonl，坏行 skip+warn（含崩溃留下的半截尾行），永不因一行废掉整会话。"""
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
                log.warning("跳过损坏的会话行 %s:%d", path.name, lineno)


# ---------------------------------------------------------------------------
# JSONL 适配器：每会话一个文件，append-only
# ---------------------------------------------------------------------------
class JsonlSessionStore:
    """绑定到「单个会话」的存储句柄。
    - create(): 新会话，懒创建（第一条消息落盘时才建文件/目录/header）。
    - open():   恢复已有会话，读出末尾 seq 续号，继续追加到同一文件。
    """

    def __init__(self, base_dir: Path, workdir: Path, session_id: str, model: str):
        if not _SESSION_ID_RE.fullmatch(session_id):
            raise ValueError(f"非法会话 ID: {session_id!r}")
        self.base_dir = Path(base_dir)
        self.workdir = Path(workdir)
        self.session_id = session_id
        self.model = model
        self._dir = _project_dir(self.base_dir, self.workdir)
        self._path = self._dir / f"{session_id}.jsonl"
        self._meta_path = self._dir / f"{session_id}.meta.json"
        self._next_seq = 0
        self._header_written = False
        self._fh = None  # type: ignore[assignment]  # 懒打开的 append 句柄
        self._pending_metadata: Optional[Dict[str, Any]] = None

    # --- 构造入口 ----------------------------------------------------------
    @classmethod
    def create(cls, base_dir: Path, workdir: Path, model: str) -> "JsonlSessionStore":
        """新会话。此刻不碰磁盘——空会话(进来啥也没说就退)永不落盘。"""
        sid = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
        return cls(base_dir, workdir, sid, model)

    @classmethod
    def open(cls, base_dir: Path, workdir: Path, session_id: str, model: str) -> "JsonlSessionStore":
        """恢复已有会话：扫一遍现有行，把 _next_seq 续到末尾，后续追加不与历史撞号。"""
        store = cls(base_dir, workdir, session_id, model)
        if not store._path.exists():
            raise FileNotFoundError(f"会话不存在: {session_id}")
        last = -1
        for rec in _iter_records(store._path):
            if isinstance(rec.get("seq"), int):
                last = max(last, rec["seq"])
        store._next_seq = last + 1
        store._header_written = True
        return store

    # --- 写 ----------------------------------------------------------------
    def append(self, msg: Dict[str, Any]) -> None:
        """追加一条消息。首次调用时才真正建目录/文件/header（懒创建）。
        每行写完即 flush，硬崩最多丢尾行（_iter_records 容忍半截行）。"""
        if self._fh is None:
            self._open_for_append()
        line = json.dumps(
            {"seq": self._next_seq, "ts": _now_iso(), "msg": msg},
            ensure_ascii=False,
        )
        self._fh.write(line + "\n")
        self._fh.flush()
        self._next_seq += 1

    def _open_for_append(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._ensure_project_json()
        new_file = not self._path.exists()
        self._fh = self._path.open("a", encoding="utf-8")
        try:                       # 0600：对话可能含粘贴的密钥/文件内容，收紧权限（Win 上是 no-op）
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
        """project.json 写一次、纯显示元数据（反查 hash 目录对应的真实路径）。"""
        pj = self._dir / "project.json"
        if pj.exists():
            return
        _atomic_write(pj, json.dumps(
            {"real_workdir": str(self.workdir.resolve()), "created_at": _now_iso()},
            ensure_ascii=False,
        ))

    def set_title(self, title: str) -> None:
        """改名 = 原子覆盖 sidecar（可变会话属性，不进 append-only 日志）。"""
        self.update_metadata({"title": title})

    def load_metadata(self) -> Dict[str, Any]:
        """读取 sidecar；损坏或不存在时按空元数据处理。"""
        if self._pending_metadata is not None:
            return dict(self._pending_metadata)
        return _read_json_object(self._meta_path)

    def update_metadata(self, updates: Dict[str, Any]) -> None:
        """合并更新 sidecar；新会话仍保持懒创建，首条消息落盘时再写。"""
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

    # --- 读 ----------------------------------------------------------------
    def load(self) -> List[Dict[str, Any]]:
        """读回该会话的 msg 序列（剥掉信封 + 跳过 _meta/坏行）。新会话返回 []。"""
        msgs: List[Dict[str, Any]] = []
        for rec in _iter_records(self._path):
            if "_meta" in rec:
                continue
            msg = rec.get("msg")
            if isinstance(msg, dict):
                msgs.append(msg)
        return msgs


# ---------------------------------------------------------------------------
# 项目级：列举会话（无 index —— 扫目录，session 文件是唯一真相源）
# ---------------------------------------------------------------------------
def list_sessions(base_dir: Path, workdir: Path) -> List[SessionMeta]:
    """列出当前 workdir 的所有会话，按最近活跃排序。供 --resume 选择器。"""
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


def _read_session_meta(path: Path) -> Optional[SessionMeta]:
    """从一个 .jsonl 读出摘要：首行 _meta + 首条 user 消息(派生标题) + 文件 mtime。"""
    session_id = path.stem
    created_at = ""
    model = ""
    first_user = ""
    msg_count = 0
    for rec in _iter_records(path):
        if "_meta" in rec:
            m = rec["_meta"]
            created_at = m.get("created_at", "")
            model = m.get("model", "")
            session_id = m.get("session_id", session_id)
            continue
        msg = rec.get("msg")
        if isinstance(msg, dict):
            msg_count += 1
            if not first_user and msg.get("role") == "user":
                first_user = msg.get("content", "")
    try:
        last_active = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")
    except OSError:
        last_active = created_at

    # 标题：sidecar 优先（用户自定义），否则从首条 user 消息派生
    title = _read_sidecar_title(path.parent / (path.stem + ".meta.json"))
    if title is None:
        title = _derive_title(first_user) if first_user else "(空会话)"
    return SessionMeta(session_id, created_at, last_active, title, msg_count, model)


def _read_sidecar_title(meta_path: Path) -> Optional[str]:
    t = _read_json_object(meta_path).get("title")
    return t if isinstance(t, str) and t.strip() else None


def _read_json_object(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}
