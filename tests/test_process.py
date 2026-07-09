import ast
import subprocess
import sys
from pathlib import Path

import pytest

from noval.process import (
    NoSandbox,
    PreparedProcess,
    ProcessRuntime,
    ProcessSpec,
    ProcessTimeout,
    SandboxCapabilities,
    SandboxMode,
    SandboxPolicy,
    SandboxStatus,
    SandboxStrength,
    SandboxUnavailable,
    sandbox_status_text,
)


class FakeHardSandbox:
    def __init__(self):
        self.seen = []
        self._status = SandboxStatus(
            backend="fake-hard",
            strength=SandboxStrength.HARD,
            capabilities=SandboxCapabilities(
                filesystem=True,
                network=True,
                process_tree=True,
            ),
        )

    @property
    def status(self):
        return self._status

    def prepare(self, spec, policy):
        self.seen.append((spec, policy))
        return PreparedProcess(
            argv=spec.argv,
            cwd=spec.cwd,
            env=spec.env,
            timeout=spec.timeout,
            purpose=spec.purpose,
            sandbox=self.status,
        )


def _python_spec(tmp_path, source="print('ok')", *, timeout=5):
    return ProcessSpec(
        argv=(sys.executable, "-c", source),
        cwd=tmp_path,
        timeout=timeout,
        purpose="test",
    )


def test_no_sandbox_is_honest_pass_through(tmp_path):
    runtime = ProcessRuntime(backend=NoSandbox("test fallback"))
    spec = _python_spec(tmp_path)

    prepared = runtime.prepare(spec)

    assert prepared.argv == spec.argv
    assert prepared.cwd == tmp_path.resolve()
    assert prepared.sandbox.strength is SandboxStrength.NONE
    assert prepared.sandbox.reason == "test fallback"


def test_required_mode_fails_closed_without_hard_backend(tmp_path):
    runtime = ProcessRuntime(policy=SandboxPolicy(mode=SandboxMode.REQUIRED))

    with pytest.raises(SandboxUnavailable, match="hard sandbox required"):
        runtime.prepare(_python_spec(tmp_path))


def test_required_mode_accepts_hard_backend(tmp_path):
    backend = FakeHardSandbox()
    policy = SandboxPolicy(mode=SandboxMode.REQUIRED)
    runtime = ProcessRuntime(policy=policy, backend=backend)

    prepared = runtime.prepare(_python_spec(tmp_path))

    assert prepared.sandbox.backend == "fake-hard"
    assert backend.seen[0][1] is policy


def test_off_mode_uses_explicit_no_sandbox(tmp_path):
    backend = FakeHardSandbox()
    runtime = ProcessRuntime(
        policy=SandboxPolicy(mode=SandboxMode.OFF),
        backend=backend,
    )

    prepared = runtime.prepare(_python_spec(tmp_path))

    assert prepared.sandbox.strength is SandboxStrength.NONE
    assert prepared.sandbox.reason == "sandbox disabled explicitly"
    assert backend.seen == []
    assert "显式关闭" in sandbox_status_text(runtime)


def test_auto_mode_reports_honest_no_sandbox_status():
    runtime = ProcessRuntime()

    text = sandbox_status_text(runtime)

    assert "NoSandbox" in text
    assert "v0.7.0" in text


def test_runtime_executes_without_shell_and_captures_output(monkeypatch, tmp_path):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 3, stdout="out", stderr="err")

    monkeypatch.setattr("noval.process.subprocess.run", fake_run)

    result = ProcessRuntime().run(_python_spec(tmp_path))

    assert result.stdout == "out"
    assert result.stderr == "err"
    assert result.returncode == 3
    assert seen["shell"] is False
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["cwd"] == str(tmp_path.resolve())


def test_runtime_timeout_is_typed(tmp_path):
    runtime = ProcessRuntime()

    with pytest.raises(ProcessTimeout) as exc:
        runtime.run(_python_spec(tmp_path, "import time; time.sleep(5)", timeout=0.1))

    assert exc.value.timeout == 0.1


def test_runtime_passes_exact_environment(tmp_path):
    runtime = ProcessRuntime()
    spec = ProcessSpec(
        argv=(sys.executable, "-c", "import os; print(os.environ.get('NOVAL_TEST', 'missing'))"),
        cwd=tmp_path,
        env={"NOVAL_TEST": "visible"},
        timeout=5,
        purpose="environment-test",
    )

    result = runtime.run(spec)

    assert result.stdout.strip() == "visible"


def test_only_process_module_imports_subprocess():
    package_root = Path(__file__).parents[1] / "noval"
    offenders = []
    for path in package_root.glob("*.py"):
        if path.name == "process.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imports_subprocess = (
                isinstance(node, ast.Import)
                and any(alias.name == "subprocess" for alias in node.names)
            )
            if imports_subprocess:
                offenders.append(path.name)
            if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                offenders.append(path.name)

    assert offenders == []
