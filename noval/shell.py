"""Shell backend selection shared by environment reporting and run_bash."""
from __future__ import annotations

import os
import platform
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .process import ProcessRuntime, ProcessRuntimeError, ProcessSpec


@dataclass(frozen=True)
class ShellBackend:
    """A shell selected once for the lifetime of one Noval process."""

    executable: Optional[str]
    flavor: str
    uname: str = ""
    path_hint: str = ""

    def command(self, source: str) -> tuple[str, ...]:
        if self.executable:
            # All executable backends discovered here are Bash-compatible. Keep
            # the first failing stage visible when users trim output with a pipe.
            return (self.executable, "-o", "pipefail", "-c", source)
        if platform.system() == "Windows":
            return (os.environ.get("COMSPEC") or "cmd.exe", "/d", "/s", "/c", source)
        return (os.environ.get("SHELL") or "/bin/sh", "-c", source)


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


def _probe_bash(
    executable: str,
    runtime: Optional[ProcessRuntime] = None,
) -> ShellBackend:
    runner = runtime or ProcessRuntime()
    try:
        result = runner.run(ProcessSpec(
            argv=(executable, "-c", "uname -s -r"),
            cwd=Path.cwd(),
            timeout=5,
            purpose="shell-probe",
        ))
        uname = result.stdout.strip()
    except ProcessRuntimeError:
        return ShellBackend(executable, "bash")
    except Exception:
        # Environment probing must not prevent Noval from starting.
        return ShellBackend(executable, "bash")

    low = uname.lower()
    if "microsoft" in low or "wsl" in low:
        return ShellBackend(
            executable,
            "WSL",
            uname,
            "Use /mnt/c/X (with a lowercase drive letter) for Windows path C:\\X in run_bash",
        )
    if "mingw" in low or "msys" in low:
        return ShellBackend(
            executable,
            "Git Bash",
            uname,
            "Use /c/X for Windows path C:\\X in run_bash",
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


def resolve_shell_backend(runtime: Optional[ProcessRuntime] = None) -> ShellBackend:
    """Select one backend, preferring native Git Bash over WSL on Windows."""
    path_bash = shutil.which("bash")
    path_backend = _probe_bash(path_bash, runtime) if path_bash else None

    if platform.system() == "Windows":
        if path_backend and path_backend.flavor == "Git Bash":
            return path_backend

        git_bash = _git_for_windows_bash()
        if git_bash:
            git_backend = _probe_bash(git_bash, runtime)
            if git_backend.flavor == "Git Bash":
                return git_backend

        if path_backend:
            return path_backend
        return ShellBackend(None, "Windows command shell")

    if path_backend:
        return path_backend
    return ShellBackend(None, "system command shell")
