"""Real Bubblewrap escape tests, enabled explicitly by Linux CI."""
import os
import platform
import socket
import sys
from pathlib import Path

import pytest

from noval.process import (
    BubblewrapBackend,
    NetworkAccess,
    ProcessRuntime,
    ProcessSpec,
    SandboxMode,
    SandboxPolicy,
    detect_sandbox_backend,
)


pytestmark = pytest.mark.skipif(
    platform.system() != "Linux" or os.environ.get("NOVAL_REQUIRE_BUBBLEWRAP") != "1",
    reason="real Bubblewrap tests run in the dedicated Linux CI job",
)


@pytest.fixture(scope="module")
def backend():
    detect_sandbox_backend.cache_clear()
    detected = detect_sandbox_backend()
    if not isinstance(detected, BubblewrapBackend):
        pytest.fail(f"Bubblewrap is required but unavailable: {detected.status.reason}")
    return detected


def _runtime(workspace: Path, backend, *, network=NetworkAccess.INHERIT):
    return ProcessRuntime(
        policy=SandboxPolicy.workspace(
            workspace,
            mode=SandboxMode.REQUIRED,
            network=network,
        ),
        backend=backend,
    )


def _python_spec(workspace: Path, source: str):
    return ProcessSpec(
        argv=(sys.executable, "-c", source),
        cwd=workspace,
        timeout=10,
        purpose="bubblewrap-escape-test",
    )


def test_workspace_write_is_allowed(tmp_path, backend):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = _runtime(workspace, backend).run(
        _python_spec(workspace, "from pathlib import Path; Path('ok.txt').write_text('ok')")
    )

    assert result.returncode == 0, result.stderr
    assert (workspace / "ok.txt").read_text(encoding="utf-8") == "ok"


def test_read_outside_workspace_is_blocked(tmp_path, backend):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = tmp_path / "host-secret.txt"
    secret.write_text("must-not-leak", encoding="utf-8")
    source = (
        "from pathlib import Path\n"
        f"p = Path({str(secret)!r})\n"
        "try:\n"
        "    print(p.read_text())\n"
        "except OSError:\n"
        "    print('blocked')\n"
    )

    result = _runtime(workspace, backend).run(_python_spec(workspace, source))

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "blocked"
    assert "must-not-leak" not in result.stdout


def test_explicit_read_root_is_visible_but_not_host_writable(tmp_path, backend):
    workspace = tmp_path / "workspace"
    docs = tmp_path / "docs"
    workspace.mkdir()
    docs.mkdir()
    reference = docs / "reference.txt"
    reference.write_text("original", encoding="utf-8")
    policy = SandboxPolicy.workspace(
        workspace,
        mode=SandboxMode.REQUIRED,
        extra_read_roots=(docs,),
    )
    runtime = ProcessRuntime(policy=policy, backend=backend)
    source = (
        "from pathlib import Path\n"
        f"p = Path({str(reference)!r})\n"
        "print(p.read_text())\n"
        "try:\n"
        "    p.write_text('changed')\n"
        "except OSError:\n"
        "    pass\n"
    )

    result = runtime.run(_python_spec(workspace, source))

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "original"
    assert reference.read_text(encoding="utf-8") == "original"


def test_write_outside_workspace_cannot_modify_host(tmp_path, backend):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    escaped = tmp_path / "escaped.txt"
    escaped.write_text("host-original", encoding="utf-8")
    source = (
        "from pathlib import Path\n"
        f"p = Path({str(escaped)!r})\n"
        "try:\n"
        "    p.write_text('escaped')\n"
        "    print('wrote')\n"
        "except OSError:\n"
        "    print('blocked')\n"
    )

    result = _runtime(workspace, backend).run(_python_spec(workspace, source))

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() in {"blocked", "wrote"}
    assert escaped.read_text(encoding="utf-8") == "host-original"


def test_network_deny_cannot_reach_host_loopback(tmp_path, backend):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    source = (
        "import socket\n"
        "s = socket.socket()\n"
        "s.settimeout(1)\n"
        "try:\n"
        f"    s.connect(('127.0.0.1', {port}))\n"
        "    print('connected')\n"
        "except OSError:\n"
        "    print('blocked')\n"
    )
    try:
        result = _runtime(
            workspace, backend, network=NetworkAccess.DENY
        ).run(_python_spec(workspace, source))
    finally:
        server.close()

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "blocked"


def test_process_runs_in_fresh_pid_namespace(tmp_path, backend):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = _runtime(workspace, backend).run(
        _python_spec(workspace, "import os; print(os.getpid(), os.getppid())")
    )

    assert result.returncode == 0, result.stderr
    pid, parent_pid = (int(value) for value in result.stdout.split())
    assert pid <= 3
    assert parent_pid <= 2
