"""Unified subprocess runtime and sandbox adapter boundary.

The runtime owns process preparation and one-shot execution. Tool approval,
output truncation, redaction, and model-facing errors remain executor concerns.
Long-lived transports such as MCP use ``prepare`` and keep their lifecycle in
the official protocol SDK.
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping, Optional, Protocol, Tuple


log = logging.getLogger("noval.process")


class SandboxMode(str, Enum):
    AUTO = "auto"
    REQUIRED = "required"
    OFF = "off"


class SandboxStrength(str, Enum):
    NONE = "none"
    HARD = "hard"


class NetworkAccess(str, Enum):
    INHERIT = "inherit"
    DENY = "deny"


@dataclass(frozen=True)
class SandboxPolicy:
    """Per-invocation subprocess isolation policy."""

    mode: SandboxMode = SandboxMode.AUTO
    network: NetworkAccess = NetworkAccess.INHERIT
    read_roots: Tuple[Path, ...] = field(default_factory=tuple)
    write_roots: Tuple[Path, ...] = field(default_factory=tuple)

    @classmethod
    def workspace(
        cls,
        workdir: Path,
        *,
        mode: SandboxMode = SandboxMode.AUTO,
        network: NetworkAccess = NetworkAccess.INHERIT,
        extra_read_roots: Iterable[Path] = (),
    ) -> "SandboxPolicy":
        root = _normalize_path(workdir)
        extras = tuple(_normalize_path(path) for path in extra_read_roots)
        return cls(
            mode=mode,
            network=network,
            read_roots=_deduplicate_paths((root, *extras)),
            write_roots=(root,),
        )


@dataclass(frozen=True)
class SandboxCapabilities:
    filesystem: bool = False
    network: bool = False
    process_tree: bool = False


@dataclass(frozen=True)
class SandboxStatus:
    backend: str
    strength: SandboxStrength
    capabilities: SandboxCapabilities = field(default_factory=SandboxCapabilities)
    reason: str = ""

    @property
    def is_hard(self) -> bool:
        return self.strength is SandboxStrength.HARD


@dataclass(frozen=True)
class ProcessSpec:
    """A shell-free process launch request.

    Shell tools still work by making the selected shell executable the first
    argv item. Keeping ``shell=False`` here makes sandbox wrapping reliable.
    """

    argv: Tuple[str, ...]
    cwd: Path
    env: Optional[Mapping[str, str]] = None
    timeout: float = 120.0
    purpose: str = "subprocess"


@dataclass(frozen=True)
class PreparedProcess:
    argv: Tuple[str, ...]
    cwd: Path
    env: Optional[Mapping[str, str]]
    timeout: float
    purpose: str
    sandbox: SandboxStatus


@dataclass(frozen=True)
class ProcessResult:
    stdout: str
    stderr: str
    returncode: int
    duration_ms: float
    sandbox: SandboxStatus


class ProcessRuntimeError(RuntimeError):
    """Base class for correctable process-runtime failures."""


class ProcessLaunchError(ProcessRuntimeError):
    pass


class ProcessTimeout(ProcessRuntimeError):
    def __init__(self, timeout: float):
        self.timeout = timeout
        super().__init__(f"process timed out after {timeout:g}s")


class SandboxUnavailable(ProcessRuntimeError):
    pass


class SandboxBackend(Protocol):
    @property
    def status(self) -> SandboxStatus:
        ...

    def prepare(self, spec: ProcessSpec, policy: SandboxPolicy) -> PreparedProcess:
        ...


class NoSandbox:
    """Honest pass-through backend used when hard isolation is unavailable."""

    def __init__(self, reason: str = "no supported sandbox backend detected"):
        self._status = SandboxStatus(
            backend="none",
            strength=SandboxStrength.NONE,
            reason=reason,
        )

    @property
    def status(self) -> SandboxStatus:
        return self._status

    def prepare(self, spec: ProcessSpec, policy: SandboxPolicy) -> PreparedProcess:
        return PreparedProcess(
            argv=spec.argv,
            cwd=spec.cwd,
            env=spec.env,
            timeout=spec.timeout,
            purpose=spec.purpose,
            sandbox=self.status,
        )


class BubblewrapBackend:
    """Linux namespace sandbox implemented by the Bubblewrap policy builder."""

    _SYSTEM_READ_ROOTS = (
        Path("/usr"),
        Path("/bin"),
        Path("/sbin"),
        Path("/lib"),
        Path("/lib64"),
        Path("/etc"),
        Path("/nix/store"),
    )

    def __init__(self, executable: Path):
        self.executable = _normalize_path(executable)
        self._status = SandboxStatus(
            backend="bubblewrap",
            strength=SandboxStrength.HARD,
            capabilities=SandboxCapabilities(
                filesystem=True,
                network=True,
                process_tree=True,
            ),
            reason=f"usable Bubblewrap at {self.executable}",
        )

    @property
    def status(self) -> SandboxStatus:
        return self._status

    def prepare(self, spec: ProcessSpec, policy: SandboxPolicy) -> PreparedProcess:
        read_roots, write_roots = _effective_sandbox_roots(spec, policy)
        executable_root = _executable_mount_root(spec)
        if executable_root is not None:
            read_roots = _deduplicate_paths((*read_roots, executable_root))

        system_roots = tuple(path for path in self._SYSTEM_READ_ROOTS if path.exists())
        visible_roots = _deduplicate_paths((*system_roots, *read_roots, *write_roots))
        argv = [
            str(self.executable),
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
        ]
        if policy.network is NetworkAccess.INHERIT:
            argv.append("--share-net")

        argv.extend((
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
        ))
        for directory in _mount_parent_directories(visible_roots):
            argv.extend(("--dir", str(directory)))
        for root in system_roots:
            argv.extend(("--ro-bind", str(root), str(root)))
        for root in read_roots:
            if not any(_contains(write_root, root) for write_root in write_roots):
                argv.extend(("--ro-bind", str(root), str(root)))
        for root in write_roots:
            argv.extend(("--bind", str(root), str(root)))

        argv.extend((
            "--chdir", str(spec.cwd),
            "--",
            *spec.argv,
        ))
        return PreparedProcess(
            argv=tuple(argv),
            cwd=spec.cwd,
            env=spec.env,
            timeout=spec.timeout,
            purpose=spec.purpose,
            sandbox=self.status,
        )


class ProcessRuntime:
    """Single process boundary shared by shell, Skills, probes, and MCP."""

    def __init__(
        self,
        *,
        policy: Optional[SandboxPolicy] = None,
        backend: Optional[SandboxBackend] = None,
    ):
        self.policy = policy or SandboxPolicy()
        if self.policy.mode is SandboxMode.OFF:
            self.backend: SandboxBackend = NoSandbox("sandbox disabled explicitly")
        else:
            self.backend = backend or detect_sandbox_backend()
        log.info(
            "sandbox_mode=%s backend=%s strength=%s reason=%s",
            self.policy.mode.value,
            self.backend.status.backend,
            self.backend.status.strength.value,
            self.backend.status.reason or "<none>",
        )

    @property
    def status(self) -> SandboxStatus:
        return self.backend.status

    def prepare(self, spec: ProcessSpec) -> PreparedProcess:
        normalized = _normalize_spec(spec)
        if self.policy.mode is SandboxMode.REQUIRED and not self.backend.status.is_hard:
            reason = self.backend.status.reason or "hard sandbox backend unavailable"
            raise SandboxUnavailable(f"hard sandbox required but unavailable: {reason}")
        return self.backend.prepare(normalized, self.policy)

    def run(self, spec: ProcessSpec) -> ProcessResult:
        prepared = self.prepare(spec)
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                list(prepared.argv),
                cwd=str(prepared.cwd),
                env=dict(prepared.env) if prepared.env is not None else None,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=prepared.timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired as error:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            log.info(
                "purpose=%s backend=%s strength=%s timed_out=true dur=%sms",
                prepared.purpose,
                prepared.sandbox.backend,
                prepared.sandbox.strength.value,
                duration_ms,
            )
            raise ProcessTimeout(prepared.timeout) from error
        except OSError as error:
            raise ProcessLaunchError(
                f"failed to launch process for {prepared.purpose}: {error}"
            ) from error

        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        log.info(
            "purpose=%s backend=%s strength=%s returncode=%s dur=%sms",
            prepared.purpose,
            prepared.sandbox.backend,
            prepared.sandbox.strength.value,
            proc.returncode,
            duration_ms,
        )
        return ProcessResult(
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            returncode=proc.returncode,
            duration_ms=duration_ms,
            sandbox=prepared.sandbox,
        )


@lru_cache(maxsize=1)
def detect_sandbox_backend() -> SandboxBackend:
    """Return Bubblewrap only after its required namespace features work."""
    system = platform.system() or "unknown platform"
    if system != "Linux":
        return NoSandbox(f"Bubblewrap is only supported on Linux (host: {system})")

    executable = shutil.which("bwrap")
    if executable is None:
        return NoSandbox("Bubblewrap executable 'bwrap' was not found on PATH")

    probe = (
        executable,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--ro-bind", "/", "/",
        "--proc", "/proc",
        "--dev", "/dev",
        "--",
        "/bin/true",
    )
    try:
        result = subprocess.run(
            list(probe),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return NoSandbox(f"Bubblewrap usability probe failed: {error}")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip().splitlines()
        reason = detail[-1] if detail else "unknown error"
        if "RTM_NEWADDR" in reason:
            reason += (
                "; Ubuntu/AppArmor may require the distro "
                "bwrap-userns-restrict profile"
            )
        return NoSandbox(f"Bubblewrap usability probe failed: {reason}")
    return BubblewrapBackend(Path(executable))


def sandbox_status_text(runtime: ProcessRuntime) -> str:
    status = runtime.status
    if status.is_hard:
        capabilities = []
        if status.capabilities.filesystem:
            capabilities.append("filesystem")
        if status.capabilities.network:
            capabilities.append("network")
        if status.capabilities.process_tree:
            capabilities.append("process-tree")
        detail = ", ".join(capabilities) or "backend-defined"
        return (
            f"硬沙箱: {status.backend} ({detail}; "
            f"network={runtime.policy.network.value})"
        )
    if runtime.policy.mode is SandboxMode.OFF:
        return "未启用 OS 硬沙箱（已显式关闭）"
    reason = status.reason or "hard sandbox backend unavailable"
    return f"未启用 OS 硬沙箱（NoSandbox: {reason}）"


def _normalize_spec(spec: ProcessSpec) -> ProcessSpec:
    argv = tuple(str(item) for item in spec.argv)
    if not argv or not argv[0].strip():
        raise ProcessLaunchError("process argv must contain an executable")
    try:
        timeout = float(spec.timeout)
    except (TypeError, ValueError) as error:
        raise ProcessLaunchError("process timeout must be a positive number") from error
    if timeout <= 0:
        raise ProcessLaunchError("process timeout must be a positive number")
    env = None if spec.env is None else {str(k): str(v) for k, v in spec.env.items()}
    return replace(
        spec,
        argv=argv,
        cwd=Path(spec.cwd).expanduser().resolve(strict=False),
        env=env,
        timeout=timeout,
        purpose=str(spec.purpose or "subprocess"),
    )


def _effective_sandbox_roots(
    spec: ProcessSpec,
    policy: SandboxPolicy,
) -> Tuple[Tuple[Path, ...], Tuple[Path, ...]]:
    write_roots = _deduplicate_paths(policy.write_roots or (spec.cwd,))
    read_roots = _deduplicate_paths(policy.read_roots or write_roots)
    for root in (*read_roots, *write_roots):
        if not root.is_dir():
            raise ProcessLaunchError(f"sandbox root is not a directory: {root}")
    if not any(_contains(root, spec.cwd) for root in (*read_roots, *write_roots)):
        read_roots = _deduplicate_paths((*read_roots, spec.cwd))
    return read_roots, write_roots


def _executable_mount_root(spec: ProcessSpec) -> Optional[Path]:
    executable = spec.argv[0]
    if os.path.isabs(executable):
        candidate: Optional[str] = executable
    else:
        path = spec.env.get("PATH") if spec.env is not None else None
        candidate = shutil.which(executable, path=path)
    if not candidate:
        return None

    executable_path = _normalize_path(Path(candidate))
    if any(_contains(root, executable_path) for root in BubblewrapBackend._SYSTEM_READ_ROOTS):
        return None
    parent = executable_path.parent
    return parent.parent if parent.name in {"bin", "sbin"} else parent


def _mount_parent_directories(roots: Iterable[Path]) -> Tuple[Path, ...]:
    parents = set()
    precreated = {Path("/tmp"), Path("/proc"), Path("/dev")}
    for root in roots:
        current = root.parent
        while current != current.parent:
            if current.exists() and current not in precreated:
                parents.add(current)
            current = current.parent
    return tuple(sorted(parents, key=lambda path: (len(path.parts), str(path))))


def _deduplicate_paths(paths: Iterable[Path]) -> Tuple[Path, ...]:
    unique = []
    seen = set()
    for path in paths:
        normalized = _normalize_path(path)
        key = os.path.normcase(os.path.normpath(str(normalized)))
        if key not in seen:
            seen.add(key)
            unique.append(normalized)
    return tuple(unique)


def _contains(root: Path, candidate: Path) -> bool:
    try:
        return os.path.commonpath((str(root), str(candidate))) == str(root)
    except ValueError:
        return False


def _normalize_path(path: Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)
