"""Project-scoped file discovery filtering.

The policy combines root ``.gitignore`` and ``.llmignore`` files for built-in
directory listing and search tools. It is deliberately not an access-control
boundary: explicit reads, writes, and external processes remain governed by
their existing contracts.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional, Tuple

from pathspec import GitIgnoreSpec

log = logging.getLogger("noval.discovery")

IGNORE_FILENAMES = (".gitignore", ".llmignore")
VCS_DIRECTORY_NAMES = frozenset({".git", ".svn", ".hg", ".bzr", ".jj", ".sl"})

_FileStamp = Optional[Tuple[int, int]]
_PolicyStamp = Tuple[_FileStamp, ...]


class DiscoveryPolicy:
    """Lazily refreshed ignore rules for one workdir.

    Only ignore files at the workdir root are loaded. Rules from
    ``.llmignore`` follow ``.gitignore``, so later Git-style negation rules can
    re-include a path for discovery.
    """

    def __init__(self, workdir: Path):
        self.workdir = Path(workdir).expanduser().resolve(strict=False)
        self._stamp: Optional[_PolicyStamp] = None
        self._spec = GitIgnoreSpec.from_lines([])

    def refresh(self) -> None:
        """Reload rules when either root ignore file changes."""
        paths = tuple(self.workdir / name for name in IGNORE_FILENAMES)
        stamp = tuple(_file_stamp(path) for path in paths)
        if stamp == self._stamp:
            return

        lines = []
        try:
            for path in paths:
                if path.is_file():
                    text = path.read_text(encoding="utf-8", errors="replace")
                    lines.extend(text.splitlines())
            spec = GitIgnoreSpec.from_lines(lines)
        except Exception as error:
            # Discovery filtering is an optimization, not a reason to make the
            # workspace unreadable. Fail open and leave a content-free trace.
            log.warning(
                "failed to load project discovery rules; filters disabled error=%s",
                type(error).__name__,
            )
            spec = GitIgnoreSpec.from_lines([])

        self._spec = spec
        self._stamp = stamp

    def is_ignored(self, path: Path, *, is_dir: Optional[bool] = None) -> bool:
        """Return whether ``path`` should be omitted from file discovery."""
        candidate = Path(os.path.abspath(Path(path).expanduser()))
        try:
            relative = candidate.relative_to(self.workdir)
        except ValueError:
            return False

        if any(part in VCS_DIRECTORY_NAMES for part in relative.parts):
            return True
        if not relative.parts:
            return False

        directory = candidate.is_dir() if is_dir is None else is_dir
        normalized = relative.as_posix()
        if directory:
            normalized += "/"
        return bool(self._spec.match_file(normalized))


def _file_stamp(path: Path) -> _FileStamp:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size
