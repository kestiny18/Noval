"""Unified subprocess runtime and sandbox adapter boundary.

The runtime owns process preparation and one-shot execution. Tool approval,
output truncation, redaction, and model-facing errors remain executor concerns.
Long-lived transports such as MCP use ``prepare`` and keep their lifecycle in
the official protocol SDK.
"""
from __future__ import annotations

import logging
import platform
import subprocess
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Mapping, Optional, Protocol, Tuple


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


class ProcessRuntime:
    """Single process boundary shared by shell, Skills, probes, and MCP."""

    def __init__(
        self,
        *,
        policy: Optional[SandboxPolicy] = None,
        backend: Optional[SandboxBackend] = None,
    ):
        self.policy = policy or SandboxPolicy()
        detected = backend or detect_sandbox_backend()
        self.backend: SandboxBackend = (
            NoSandbox("sandbox disabled explicitly")
            if self.policy.mode is SandboxMode.OFF
            else detected
        )
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


def detect_sandbox_backend() -> SandboxBackend:
    """Detect implemented hard backends without mistaking WSL/containers for one.

    v0.7.0 establishes the adapter boundary but intentionally ships no hard
    backend. Platform adapters can replace this function as they land.
    """
    system = platform.system() or "unknown platform"
    return NoSandbox(f"no hard sandbox backend is implemented for {system} in v0.7.0")


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
        return f"硬沙箱: {status.backend} ({detail})"
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
