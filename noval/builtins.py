"""内置工具集。

文件工具共享一套状态机，横切关注点由框架统一处理，因此每个工具只保留领域逻辑。
最值钱的部分是三个文件工具共享的状态机（read-tracker）：
  - read_file 把 {mtime, content, is_partial} 写进 ctx.read_state
  - write_file / edit_file 改前校验：必须先 read 过（full read）+ 自上次 read 后没被外部改动
  - 写盘后回写 read_state，让紧接着的 edit 不被自己误判 stale
所有文件工具走同一个 _resolve（路径归一化一致，read_state 的 key 才对得上）。
"""
from __future__ import annotations

import difflib
import fnmatch
import glob as _glob
import os
import re
import subprocess
from pathlib import Path
from typing import List, Optional

from .tools import Context, Risk, ToolError, tool
from .shell import resolve_shell_backend

# 读取整文件的上限：超过就引导用 grep 定位，避免一口气塞爆上下文/内存
MAX_READ_BYTES = 256 * 1024
# 搜索时排除的版本控制目录（噪音）
_VCS_DIRS = {".git", ".svn", ".hg", ".bzr", ".jj", ".sl"}


# ---------------------------------------------------------------------------
# 共享基建
# ---------------------------------------------------------------------------
_WSL_MOUNT = re.compile(r"^/mnt/([a-zA-Z])(/.*)?$")


def _wsl_to_windows(s: str) -> str:
    """把 WSL 挂载路径翻译成 Windows 路径：/mnt/e/x → E:/x。非此形态原样返回。"""
    m = _WSL_MOUNT.match(s)
    return f"{m.group(1).upper()}:{m.group(2) or '/'}" if m else s


def _resolve(ctx: Context, path: str) -> Path:
    """统一路径归一化：相对路径基于 workdir，展开 ~，返回绝对规范路径。
    Read/Write/Edit/grep/glob 全走它，保证路径 key 一致。
    run_bash 用 WSL 路径(/mnt/e/..)，但本类工具是原生 Python——在 Windows 上把模型
    顺手给的 /mnt 路径翻译回盘符路径，让工具对两种约定都「forgiving」。"""
    s = str(path)
    if os.name == "nt":
        s = _wsl_to_windows(s)
    p = Path(s).expanduser()
    if not p.is_absolute():
        p = ctx.workdir / p
    return p.resolve()


def _read_text(p: Path) -> str:
    """读文本并归一化换行（\\r\\n → \\n），与 read_state 里存的形式一致。"""
    return p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")


def _rel(ctx: Context, p: Path) -> str:
    """结果路径相对 workdir 显示以省 token；不在 workdir 内则用绝对路径。"""
    try:
        return str(p.resolve().relative_to(ctx.workdir))
    except ValueError:
        return str(p.resolve())


def _suggest(p: Path) -> Optional[str]:
    """not-found 时在同目录里找一个最接近的文件名，做 "did you mean" 提示。"""
    parent = p.parent
    if not parent.is_dir():
        return None
    names = [e.name for e in parent.iterdir()]
    close = difflib.get_close_matches(p.name, names, n=1, cutoff=0.6)
    return close[0] if close else None


def _not_found(ctx: Context, path: str, p: Path) -> ToolError:
    sugg = _suggest(p)
    hint = f"，是不是想找 '{sugg}'?" if sugg else ""
    return ToolError(f"file '{path}' not found (workdir: {ctx.workdir}){hint}")


def _is_binary(p: Path) -> bool:
    try:
        return b"\x00" in p.read_bytes()[:1024]
    except OSError:
        return False


def _require_fresh_read(ctx: Context, p: Path) -> None:
    """write/edit 改一个已存在文件前的守卫：必须先 full read 过，且之后没被外部改动。"""
    rec = ctx.read_state.get(str(p))
    if rec is None or rec.is_partial:
        raise ToolError(
            f"file '{p.name}' 还没被（完整）读过。先用 read_file 读它，再修改。"
        )
    # staleness：磁盘 mtime 比上次 read 新 → 可能被用户/linter 改过
    if p.stat().st_mtime > rec.mtime:
        # Windows 上云同步/安全软件可能只改 mtime；回退比对内容以避免误报
        if _read_text(p) != rec.content:
            raise ToolError(
                f"file '{p.name}' 自上次 read 后被改动过（用户或 linter）。请重新 read 再写。"
            )


def _with_line_numbers(lines: List[str], start: int) -> str:
    return "\n".join(f"{start + i:6d}\t{line}" for i, line in enumerate(lines))


def _walk_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _VCS_DIRS]  # 原地剪枝，跳过 .git 等
        for fn in filenames:
            yield Path(dirpath) / fn


# ===========================================================================
# 文件读写
# ===========================================================================
@tool(risk=Risk.READ, param_descriptions={
    "path": "文件路径（相对 workdir 或绝对）",
    "offset": "起始行号(1-based)，翻大文件用",
    "limit": "读取行数，翻大文件用",
})
def read_file(ctx: Context, path: str, offset: int = 1, limit: Optional[int] = None) -> str:
    """读取文件内容，返回带行号的文本（cat -n 式）。用于查看文件，不要用于目录。
    大文件用 offset(起始行,1-based)+limit(行数) 流式读片段——不会把整文件载入内存。"""
    p = _resolve(ctx, path)
    if not p.exists():
        raise _not_found(ctx, path, p)
    if p.is_dir():
        raise ToolError(f"'{path}' 是目录，不是文件；用 list_directory 代替")
    if _is_binary(p):
        raise ToolError(f"'{path}' 像是二进制文件；此工具只读文本")

    full = offset == 1 and limit is None

    # 整文件读：受大小上限保护（否则引导用 offset/limit 流式读或 grep 定位）
    if full:
        if p.stat().st_size > MAX_READ_BYTES:
            raise ToolError(
                f"file '{path}' 整文件太大（{p.stat().st_size // 1024} KB > {MAX_READ_BYTES // 1024} KB）；"
                f"用 offset+limit 流式读取片段，或用 grep 定位关键内容"
            )
        text = _read_text(p)
        all_lines = text.split("\n")
        if all_lines and all_lines[-1] == "":   # 文件以换行结尾时去掉末尾空元素
            all_lines.pop()
        ctx.read_state[str(p)] = _make_record(p, text, is_partial=False)
        if not all_lines:
            return "<system-reminder>文件存在但内容为空。</system-reminder>"
        return _with_line_numbers(all_lines, 1)

    # 局部读：只流式读 [start, start+limit) 这个窗口，大文件也不爆内存。
    # 局部读 is_partial=True，不满足 write/edit 的「先完整 read」要求。
    start = max(offset, 1)
    lim = limit if limit is not None else 2000
    window: List[str] = []
    with p.open(encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh, 1):
            if i < start:
                continue
            if i >= start + lim:
                break
            window.append(line.rstrip("\n"))
    ctx.read_state[str(p)] = _make_record(p, "\n".join(window), is_partial=True)
    if not window:
        return f"<system-reminder>从第 {start} 行起没有内容（offset 可能越界）。</system-reminder>"
    return _with_line_numbers(window, start)


@tool(risk=Risk.WRITE, param_descriptions={
    "path": "文件路径（相对 workdir 或绝对）",
    "content": "要写入的完整内容（覆盖原文件）",
})
def write_file(ctx: Context, path: str, content: str) -> str:
    """把内容写入文件（全量覆盖）。已存在的文件必须先用 read_file 读过；新文件免。
    自动创建父目录。内容按原样落盘（不改写换行）。"""
    p = _resolve(ctx, path)
    if p.is_dir():
        raise ToolError(f"'{path}' 是目录，无法作为文件写入")
    existed = p.exists()
    if existed:
        _require_fresh_read(ctx, p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    ctx.read_state[str(p)] = _make_record(p, content.replace("\r\n", "\n"), is_partial=False)
    verb = "updated" if existed else "created"
    return f"File {verb} at {_rel(ctx, p)} ({len(content)} chars)"


@tool(risk=Risk.WRITE, param_descriptions={
    "path": "文件路径（相对 workdir 或绝对）",
    "old_string": "要被替换的原文（须在文件中唯一出现，除非 replace_all）",
    "new_string": "替换成的新内容",
    "replace_all": "是否替换全部匹配（默认只换唯一一处）",
})
def edit_file(ctx: Context, path: str, old_string: str, new_string: str,
              replace_all: bool = False) -> str:
    """精确字符串替换。old_string 必须在文件中唯一出现（否则用 replace_all）。
    改前须先用 read_file 读过该文件。"""
    if old_string == new_string:
        raise ToolError("old_string 与 new_string 相同，无需修改")
    p = _resolve(ctx, path)
    if not p.exists():
        raise _not_found(ctx, path, p)
    if p.is_dir():
        raise ToolError(f"'{path}' 是目录，不是文件")
    _require_fresh_read(ctx, p)

    text = _read_text(p)
    count = text.count(old_string)
    if count == 0:
        raise ToolError(f"未找到要替换的字符串：\n{old_string}")
    if count > 1 and not replace_all:
        raise ToolError(
            f"找到 {count} 处匹配，但 replace_all=false。"
            f"设 replace_all=true 替换全部，或补充上下文使 old_string 唯一。"
        )
    new_text = text.replace(old_string, new_string) if replace_all \
        else text.replace(old_string, new_string, 1)
    p.write_text(new_text, encoding="utf-8")
    ctx.read_state[str(p)] = _make_record(p, new_text, is_partial=False)
    n = count if replace_all else 1
    return f"Edited {_rel(ctx, p)} ({n} replacement{'s' if n > 1 else ''})"


def _make_record(p: Path, content: str, is_partial: bool):
    from .tools import ReadRecord
    return ReadRecord(mtime=p.stat().st_mtime, content=content, is_partial=is_partial)


# ===========================================================================
# 目录 / 搜索
# ===========================================================================
@tool(risk=Risk.READ, param_descriptions={"path": "目录路径，默认 workdir"})
def list_directory(ctx: Context, path: str = ".") -> str:
    """列出目录内容（目录在前，带 / 标记）。默认列 workdir。"""
    p = _resolve(ctx, path)
    if not p.exists():
        raise _not_found(ctx, path, p)
    if not p.is_dir():
        raise ToolError(f"'{path}' 不是目录，用 read_file 读文件")
    entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
    lines = [f"{e.name}{'/' if e.is_dir() else ''}" for e in entries]
    return "\n".join(lines) if lines else "(空目录)"


@tool(risk=Risk.READ, param_descriptions={
    "pattern": "glob 模式，如 **/*.py（基于 path 递归）",
    "path": "搜索起点目录，默认 workdir",
})
def glob(ctx: Context, pattern: str, path: str = ".") -> str:
    """按文件名模式查找文件（如 **/*.py），结果按修改时间排序（最近的在前）。"""
    root = _resolve(ctx, path)
    if not root.is_dir():
        raise ToolError(f"'{path}' 不是有效目录")
    hits = [Path(m) for m in _glob.glob(str(root / pattern), recursive=True)]
    files = [m for m in hits if m.is_file()]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return "未找到匹配的文件。"
    limit = 100
    truncated = len(files) > limit
    out = "\n".join(_rel(ctx, f) for f in files[:limit])
    if truncated:
        out += f"\n\n[结果已截断到 {limit} 条，用更具体的 pattern/path 缩小范围]"
    return out


@tool(risk=Risk.READ, param_descriptions={
    "pattern": "正则表达式（匹配文件内容）",
    "path": "搜索起点（文件或目录），默认 workdir",
    "glob_filter": "只搜匹配此 glob 的文件，如 *.py",
    "output_mode": "files_with_matches(默认) | content | count",
    "case_insensitive": "是否忽略大小写",
    "head_limit": "结果上限，默认 250，0 表示不限",
})
def grep(ctx: Context, pattern: str, path: str = ".", glob_filter: str = "",
         output_mode: str = "files_with_matches", case_insensitive: bool = False,
         head_limit: int = 250) -> str:
    """在文件内容里做正则搜索。按修改时间排序，自动排除 .git 等版本控制目录。
    output_mode: files_with_matches(列文件) / content(列匹配行) / count(每文件计数)。"""
    try:
        rx = re.compile(pattern, re.IGNORECASE if case_insensitive else 0)
    except re.error as e:
        raise ToolError(f"正则非法: {e}")
    root = _resolve(ctx, path)
    if not root.exists():
        raise _not_found(ctx, path, root)

    files = [root] if root.is_file() else list(_walk_files(root))
    if glob_filter:
        files = [f for f in files if fnmatch.fnmatch(f.name, glob_filter)]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

    file_hits: List[str] = []
    content_lines: List[str] = []
    counts: List[str] = []
    for f in files:
        if _is_binary(f):
            continue  # 真二进制(含 NUL)跳过
        try:
            # 用 errors="replace"，非 UTF-8 的文本文件(gbk/latin-1 日志等)也能搜，
            # 不再被静默漏掉——否则"我明明知道有匹配，grep 怎么没找到"。
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue  # 读不了的(权限等)跳过
        n = 0
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                n += 1
                if output_mode == "content":
                    content_lines.append(f"{_rel(ctx, f)}:{i}:{line}")
        if n:
            file_hits.append(_rel(ctx, f))
            counts.append(f"{_rel(ctx, f)}:{n}")

    items = {"content": content_lines, "count": counts}.get(output_mode, file_hits)
    if not items:
        return "未找到匹配。"
    truncated = head_limit > 0 and len(items) > head_limit
    shown = items[:head_limit] if head_limit > 0 else items
    header = "" if output_mode == "content" else f"Found {len(file_hits)} file(s):\n"
    out = header + "\n".join(shown)
    if truncated:
        out += f"\n\n[结果已截断到 {head_limit} 条，用更精确的 pattern/path/glob_filter，或调 head_limit]"
    return out


# ===========================================================================
# Shell
# ===========================================================================
# 公认只读的命令：全管道都是这些时，run_bash 可降级为 READ → 免确认。
# 「风险在命令里，不在工具上」——日志排查的 grep/sed/cat 不该每次都打扰用户。
_READONLY_BASH = {
    # 导航（无害；链里真危险的命令仍会被另判）
    "cd",
    # 文本/文件查看与检索
    "grep", "egrep", "fgrep", "rg", "sed", "awk", "cat", "head", "tail", "wc",
    "ls", "find", "echo", "pwd", "stat", "file", "sort", "uniq", "cut", "tr",
    "diff", "cmp", "du", "df", "date", "which", "whoami", "env", "basename",
    "dirname", "realpath", "tac", "nl", "column", "true", "test", "comm",
    "paste", "rev", "fold", "printf", "seq", "expr",
    # 压缩包只读查看（解压到 stdout，不落盘）
    "zcat", "zgrep", "zegrep", "zfgrep", "bzcat", "xzcat",
    # 摘要 / 编码 / 二进制查看
    "md5sum", "sha1sum", "sha256sum", "sha512sum", "base64", "xxd", "od",
    "hexdump", "strings", "jq",
}
# 只读的 git 子命令。git 整体是双刃(commit/push/reset 会改状态)，不能整体放行，
# 只精确放行这些确定只读的子命令(code 探索最高频)。
_GIT_READONLY = {
    "log", "show", "diff", "status", "blame", "ls-files", "ls-tree", "rev-parse",
    "rev-list", "describe", "shortlog", "whatchanged", "cat-file", "grep",
    "for-each-ref", "show-ref", "symbolic-ref", "name-rev", "reflog",
}
# git 的「带参数全局 flag」：取子命令时要连同其参数一起跳过(如 -C /path)
_GIT_ARG_FLAGS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
# 这些子串只要出现就判定为危险（程序名检查覆盖不到的「写」语义）
_DANGER_SUBSTR = ("$(", "`", "sed -i", "awk -i", "perl -i", "--in-place",
                  "-delete", "-exec ")
# 「安全重定向」：fd 复制(2>&1)与丢进黑洞(2>/dev/null)都不是真的写文件，
# 剥离后剩下的 > 才是写到真实文件 → 危险。
_SAFE_REDIR = re.compile(r"\d*>&\d*|&?\d*>>?\s*/dev/null")


def _segment_tokens(seg: str) -> List[str]:
    """取一段命令的 token，跳过前置 VAR=val 赋值。"""
    toks = seg.split()
    while toks and "=" in toks[0] and not toks[0].startswith("-"):
        toks = toks[1:]
    return toks


def _git_subcommand(toks: List[str]) -> Optional[str]:
    """从 ['git', ...] 里找出子命令，跳过全局 flag 及其参数(如 -C /path)。"""
    i = 1
    while i < len(toks):
        tok = toks[i]
        if tok in _GIT_ARG_FLAGS:
            i += 2          # flag + 它的参数
        elif tok.startswith("-"):
            i += 1          # 无参 flag(--no-pager 等)
        else:
            return tok      # 第一个非 flag token = 子命令
    return None


def _is_readonly_segment(seg: str) -> bool:
    """单段命令是否只读：白名单程序，或 git 的只读子命令。"""
    toks = _segment_tokens(seg)
    if not toks:
        return False        # 无法判定 → 保守当作非只读
    if toks[0] == "git":
        return _git_subcommand(toks) in _GIT_READONLY
    return toks[0] in _READONLY_BASH


def _bash_risk(args: dict) -> Risk:
    """动态评估 run_bash 的风险：纯只读命令(可含管道) → READ，其余 → DANGEROUS。
    保守起见，任何无法确认只读的命令都判为 DANGEROUS（宁可多问一次）。"""
    cmd = (args.get("command") or "").strip()
    if not cmd:
        return Risk.DANGEROUS
    # 先剥掉安全重定向(2>&1 / 2>/dev/null)，免得 > 被误判为写文件、& 被误拆成假程序名
    clean = _SAFE_REDIR.sub(" ", cmd)
    if ">" in clean or any(tok in clean for tok in _DANGER_SUBSTR):  # 剩下的 > 才是真写文件
        return Risk.DANGEROUS
    segments = [s for s in re.split(r"[|&;]+", clean) if s.strip()]
    if segments and all(_is_readonly_segment(s) for s in segments):
        return Risk.READ
    return Risk.DANGEROUS


@tool(risk=Risk.DANGEROUS, risk_assessor=_bash_risk, param_descriptions={
    "command": "要执行的 shell 命令",
    "timeout": "超时秒数，默认 120",
})
def run_bash(ctx: Context, command: str, timeout: int = 120) -> str:
    """在 workdir 下执行 shell 命令，返回合并的 stdout+stderr。
    命令非交互执行，超时会真正终止子进程。属危险操作，受确认门管控。"""
    backend = ctx.shell_backend or resolve_shell_backend()
    argv, use_system_shell = backend.command(command)
    try:
        proc = subprocess.run(
            argv,
            cwd=str(ctx.workdir),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=use_system_shell,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"命令超时（>{timeout}s）已被终止: {command}")
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        out += f"\n[exit code: {proc.returncode}]"
    return out if out.strip() else "(命令执行完成，无输出)"
