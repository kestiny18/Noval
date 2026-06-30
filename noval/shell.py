"""Shell backend selection shared by environment reporting and run_bash."""
from __future__ import annotations

import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ShellBackend:
    """A shell selected once for the lifetime of one Noval process."""

    executable: Optional[str]
    flavor: str
    uname: str = ""
    path_hint: str = ""

    def command(self, source: str):
        if self.executable:
            return [self.executable, "-c", source], False
        return source, True


def to_bash_path(winpath: str, flavor: str) -> Optional[str]:
    """Convert C:\\X to the path syntax used by the selected bash backend."""
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", str(winpath))
    if not match:
        return None
    drive, rest = match.group(1).lower(), match.group(2).replace("\\", "/")
    if flavor == "WSL":
        return f"/mnt/{drive}/{rest}"
    if flavor == "Git Bash":
        return f"/{drive}/{rest}"
    return None


def _probe_bash(executable: str) -> ShellBackend:
    try:
        result = subprocess.run(
            [executable, "-c", "uname -s -r"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        uname = (result.stdout or "").strip()
    except Exception:
        return ShellBackend(executable, "bash")

    low = uname.lower()
    if "microsoft" in low or "wsl" in low:
        return ShellBackend(
            executable,
            "WSL",
            uname,
            "Windows 路径 C:\\X 在 run_bash 里要写成 /mnt/c/X（盘符小写）",
        )
    if "mingw" in low or "msys" in low:
        return ShellBackend(
            executable,
            "Git Bash",
            uname,
            "Windows 路径 C:\\X 在 run_bash 里要写成 /c/X",
        )
    return ShellBackend(executable, "Linux/Unix", uname)


def _git_for_windows_bash() -> Optional[str]:
    """Locate Git Bash from git.exe without depending on PATH's bash ordering."""
    git = shutil.which("git")
    if not git:
        return None
    git_path = Path(git).resolve()
    roots = [git_path.parent.parent, git_path.parent]
    for root in roots:
        candidate = root / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def resolve_shell_backend() -> ShellBackend:
    """Select one backend, preferring native Git Bash over WSL on Windows."""
    path_bash = shutil.which("bash")
    path_backend = _probe_bash(path_bash) if path_bash else None

    if platform.system() == "Windows":
        if path_backend and path_backend.flavor == "Git Bash":
            return path_backend

        git_bash = _git_for_windows_bash()
        if git_bash:
            git_backend = _probe_bash(git_bash)
            if git_backend.flavor == "Git Bash":
                return git_backend

        if path_backend:
            return path_backend
        return ShellBackend(None, "Windows command shell")

    if path_backend:
        return path_backend
    return ShellBackend(None, "system command shell")
