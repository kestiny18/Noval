"""Path confinement for in-process file tools.

This module is intentionally policy-only: it does not know about tools,
permissions, or shell execution. Built-in file tools call it from their shared
path resolver so read/write boundaries stay centralized.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional, Tuple


class PathAccess(str, Enum):
    READ = "read"
    WRITE = "write"


class ConfinementMode(str, Enum):
    WORKSPACE = "workspace"
    EXPANDED_READ = "expanded_read"
    OFF = "off"


class PathConfinementError(ValueError):
    """Raised when a resolved path is outside the allowed roots."""

    def __init__(self, *, access: PathAccess, path: Path, roots: Tuple[Path, ...]):
        self.access = access
        self.path = path
        self.roots = roots
        super().__init__(
            f"{access.value} path '{path}' is outside allowed roots: "
            + ", ".join(str(root) for root in roots)
        )


@dataclass(frozen=True)
class ConfinementPolicy:
    """Allowed roots for in-process file-tool reads and writes.

    ``FULL_ACCESS`` permissions only bypass approval prompts. They do not turn
    this policy off. Embedders that genuinely want no path confinement must pass
    ``ConfinementPolicy.disabled()`` explicitly.
    """

    mode: ConfinementMode = ConfinementMode.WORKSPACE
    read_roots: Tuple[Path, ...] = field(default_factory=tuple)
    write_roots: Tuple[Path, ...] = field(default_factory=tuple)

    @classmethod
    def workspace(cls, workdir: Path) -> "ConfinementPolicy":
        root = _normalize_path(workdir)
        return cls(
            mode=ConfinementMode.WORKSPACE,
            read_roots=(root,),
            write_roots=(root,),
        )

    @classmethod
    def expanded_read(
        cls,
        workdir: Path,
        extra_read_roots: Iterable[Path],
    ) -> "ConfinementPolicy":
        work_root = _normalize_path(workdir)
        extras = tuple(_normalize_path(root) for root in extra_read_roots)
        return cls(
            mode=ConfinementMode.EXPANDED_READ,
            read_roots=(work_root, *extras),
            write_roots=(work_root,),
        )

    @classmethod
    def disabled(cls) -> "ConfinementPolicy":
        return cls(mode=ConfinementMode.OFF)

    def is_disabled(self) -> bool:
        return self.mode == ConfinementMode.OFF

    def roots_for(self, workdir: Path, access: PathAccess) -> Tuple[Path, ...]:
        if self.is_disabled():
            return ()
        default_root = (_normalize_path(workdir),)
        if access == PathAccess.READ:
            roots = self.read_roots or default_root
        else:
            roots = self.write_roots or default_root
        return tuple(_normalize_path(root) for root in roots)


def effective_policy(
    policy: Optional[ConfinementPolicy],
    workdir: Path,
) -> ConfinementPolicy:
    return policy if policy is not None else ConfinementPolicy.workspace(workdir)


def allowed_roots(
    policy: Optional[ConfinementPolicy],
    workdir: Path,
    access: PathAccess,
) -> Tuple[Path, ...]:
    return effective_policy(policy, workdir).roots_for(workdir, access)


def assert_path_allowed(
    policy: Optional[ConfinementPolicy],
    workdir: Path,
    path: Path,
    access: PathAccess,
) -> None:
    current = effective_policy(policy, workdir)
    if current.is_disabled():
        return

    resolved = _normalize_path(path)
    roots = current.roots_for(workdir, access)
    if any(_contains(root, resolved) for root in roots):
        return
    raise PathConfinementError(access=access, path=resolved, roots=roots)


def is_path_allowed(
    policy: Optional[ConfinementPolicy],
    workdir: Path,
    path: Path,
    access: PathAccess,
) -> bool:
    try:
        assert_path_allowed(policy, workdir, path, access)
    except PathConfinementError:
        return False
    return True


def _normalize_path(path: Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _contains(root: Path, candidate: Path) -> bool:
    root_s = _compare_path(root)
    candidate_s = _compare_path(candidate)
    try:
        return os.path.commonpath([root_s, candidate_s]) == root_s
    except ValueError:
        return False


def _compare_path(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))
