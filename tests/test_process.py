import ast
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from noval.process import (
    BubblewrapBackend,
    NetworkAccess,
    NoSandbox,
    PreparedProcess,
    ProcessRuntime,
    ProcessCancelled,
    ProcessSpec,
    ProcessTimeout,
    SandboxCapabilities,
    SandboxMode,
    SandboxPolicy,
    SandboxStatus,
    SandboxStrength,
    SandboxUnavailable,
    detect_sandbox_backend,
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
    runtime = ProcessRuntime(
        policy=SandboxPolicy(mode=SandboxMode.REQUIRED),
        backend=NoSandbox("test fallback"),
    )

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
    assert "disabled explicitly" in sandbox_status_text(runtime)


def test_auto_mode_reports_honest_no_sandbox_status():
    runtime = ProcessRuntime(backend=NoSandbox("test fallback"))

    text = sandbox_status_text(runtime)

    assert "NoSandbox" in text
    assert "test fallback" in text


def test_runtime_executes_without_shell_and_captures_output(monkeypatch, tmp_path):
    seen = {}

    class FakePopen:
        returncode = 3

        def __init__(self, argv, **kwargs):
            seen["argv"] = argv
            seen.update(kwargs)

        def communicate(self, timeout=None):
            seen["timeout"] = timeout
            return "out", "err"

        def terminate(self):
            pass

        def kill(self):
            pass

    monkeypatch.setattr("noval.process.subprocess.Popen", FakePopen)

    result = ProcessRuntime(backend=NoSandbox()).run(_python_spec(tmp_path))

    assert result.stdout == "out"
    assert result.stderr == "err"
    assert result.returncode == 3
    assert seen["shell"] is False
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["cwd"] == str(tmp_path.resolve())


def test_runtime_cancellation_terminates_an_owned_process(tmp_path):
    runtime = ProcessRuntime(backend=NoSandbox())
    runtime.begin_turn()
    errors = []

    worker = threading.Thread(
        target=lambda: _capture_error(
            errors,
            lambda: runtime.run(_python_spec(
                tmp_path,
                "import time; time.sleep(30)",
                timeout=60,
            )),
        )
    )
    worker.start()
    deadline = time.monotonic() + 3
    while runtime.active_process_count == 0 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert runtime.active_process_count == 1
    runtime.cancel()
    worker.join(3)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], ProcessCancelled)


def _capture_error(errors, callback):
    try:
        callback()
    except Exception as error:
        errors.append(error)


def test_runtime_timeout_is_typed(tmp_path):
    runtime = ProcessRuntime(backend=NoSandbox())

    with pytest.raises(ProcessTimeout) as exc:
        runtime.run(_python_spec(tmp_path, "import time; time.sleep(5)", timeout=0.1))

    assert exc.value.timeout == 0.1


def test_runtime_passes_exact_environment(tmp_path):
    runtime = ProcessRuntime(backend=NoSandbox())
    spec = ProcessSpec(
        argv=(sys.executable, "-c", "import os; print(os.environ.get('NOVAL_TEST', 'missing'))"),
        cwd=tmp_path,
        env={"NOVAL_TEST": "visible"},
        timeout=5,
        purpose="environment-test",
    )

    result = runtime.run(spec)

    assert result.stdout.strip() == "visible"


def test_workspace_policy_normalizes_roots(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()

    policy = SandboxPolicy.workspace(tmp_path, extra_read_roots=(docs, docs))

    assert policy.read_roots == (tmp_path.resolve(), docs.resolve())
    assert policy.write_roots == (tmp_path.resolve(),)


def test_bubblewrap_compiles_filesystem_network_and_process_policy(tmp_path):
    workspace = tmp_path / "workspace"
    skill = tmp_path / "skill"
    workspace.mkdir()
    skill.mkdir()
    policy = SandboxPolicy.workspace(
        workspace,
        mode=SandboxMode.REQUIRED,
        network=NetworkAccess.DENY,
    )
    runtime = ProcessRuntime(
        policy=policy,
        backend=BubblewrapBackend(Path("/usr/bin/bwrap")),
    )

    prepared = runtime.prepare(_python_spec(skill))
    argv = prepared.argv

    assert "--unshare-all" in argv
    assert "--die-with-parent" in argv
    assert "--new-session" in argv
    assert "--share-net" not in argv
    assert ("--bind", str(workspace.resolve()), str(workspace.resolve())) in tuple(
        zip(argv, argv[1:], argv[2:])
    )
    assert ("--ro-bind", str(skill.resolve()), str(skill.resolve())) in tuple(
        zip(argv, argv[1:], argv[2:])
    )
    assert ("--tmpfs", "/tmp") in tuple(zip(argv, argv[1:]))
    assert argv.index("--tmpfs") < argv.index("--bind")
    assert argv[-len(_python_spec(skill).argv):] == _python_spec(skill).argv


def test_bubblewrap_inherit_network_is_explicit(tmp_path):
    policy = SandboxPolicy.workspace(tmp_path, network=NetworkAccess.INHERIT)
    runtime = ProcessRuntime(
        policy=policy,
        backend=BubblewrapBackend(Path("/usr/bin/bwrap")),
    )

    assert "--share-net" in runtime.prepare(_python_spec(tmp_path)).argv


def test_detector_requires_successful_bubblewrap_probe(monkeypatch):
    detect_sandbox_backend.cache_clear()
    monkeypatch.setattr("noval.process.platform.system", lambda: "Linux")
    monkeypatch.setattr("noval.process.shutil.which", lambda name: "/usr/bin/bwrap")
    monkeypatch.setattr(
        "noval.process.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", ""),
    )
    try:
        backend = detect_sandbox_backend()
    finally:
        detect_sandbox_backend.cache_clear()

    assert isinstance(backend, BubblewrapBackend)
    assert backend.status.is_hard


def test_detector_downgrades_when_bubblewrap_probe_fails(monkeypatch):
    detect_sandbox_backend.cache_clear()
    monkeypatch.setattr("noval.process.platform.system", lambda: "Linux")
    monkeypatch.setattr("noval.process.shutil.which", lambda name: "/usr/bin/bwrap")
    monkeypatch.setattr(
        "noval.process.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, "", "user namespaces disabled\n"
        ),
    )
    try:
        backend = detect_sandbox_backend()
    finally:
        detect_sandbox_backend.cache_clear()

    assert isinstance(backend, NoSandbox)
    assert "user namespaces disabled" in backend.status.reason


def test_detector_explains_ubuntu_apparmor_loopback_failure(monkeypatch):
    detect_sandbox_backend.cache_clear()
    monkeypatch.setattr("noval.process.platform.system", lambda: "Linux")
    monkeypatch.setattr("noval.process.shutil.which", lambda name: "/usr/bin/bwrap")
    monkeypatch.setattr(
        "noval.process.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            1,
            "",
            "bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted\n",
        ),
    )
    try:
        backend = detect_sandbox_backend()
    finally:
        detect_sandbox_backend.cache_clear()

    assert isinstance(backend, NoSandbox)
    assert "bwrap-userns-restrict profile" in backend.status.reason


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
