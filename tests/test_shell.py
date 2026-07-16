import subprocess

import pytest

from noval import shell
from noval.shell import ShellBackend


def test_bash_command_enables_pipefail():
    assert ShellBackend("bash", "bash").command("build | tail -20") == (
        "bash", "-o", "pipefail", "-c", "build | tail -20",
    )


def test_selected_bash_reports_failed_pipeline():
    backend = shell.resolve_shell_backend()
    if not backend.executable:
        pytest.skip("no Bash-compatible backend available")

    result = subprocess.run(
        backend.command("exit 7 | cat"),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        shell=False,
    )

    assert result.returncode == 7


def test_probe_bash_does_not_inherit_stdin(monkeypatch):
    seen = {}

    def fake_run(*args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0, stdout="MINGW64_NT", stderr="")

    monkeypatch.setattr("noval.process.subprocess.run", fake_run)

    backend = shell._probe_bash("bash")
    assert backend.flavor == "Git Bash"
    assert seen["stdin"] is subprocess.DEVNULL


def test_windows_prefers_git_bash_over_path_wsl(monkeypatch):
    monkeypatch.setattr(shell.platform, "system", lambda: "Windows")
    monkeypatch.setattr(shell.shutil, "which", lambda name: "path-bash" if name == "bash" else None)
    monkeypatch.setattr(shell, "_git_for_windows_bash", lambda: "git-bash")

    def fake_probe(executable, runtime=None):
        flavor = "WSL" if executable == "path-bash" else "Git Bash"
        return ShellBackend(executable, flavor)

    monkeypatch.setattr(shell, "_probe_bash", fake_probe)

    backend = shell.resolve_shell_backend()
    assert backend.flavor == "Git Bash"
    assert backend.executable == "git-bash"


def test_windows_keeps_wsl_as_fallback(monkeypatch):
    monkeypatch.setattr(shell.platform, "system", lambda: "Windows")
    monkeypatch.setattr(shell.shutil, "which", lambda name: "path-bash" if name == "bash" else None)
    monkeypatch.setattr(shell, "_git_for_windows_bash", lambda: None)
    monkeypatch.setattr(shell, "_probe_bash", lambda executable, runtime=None: ShellBackend(executable, "WSL"))

    assert shell.resolve_shell_backend().flavor == "WSL"
