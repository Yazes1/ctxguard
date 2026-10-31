"""Discovery of transcript files. Strictly read-only: nothing here writes to a source."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

MB = 1024 * 1024


@dataclass(frozen=True)
class FileInfo:
    """One transcript file found on disk."""

    path: str
    session_id: str
    size: int
    mtime: float

    @property
    def size_mb(self) -> float:
        return round(self.size / MB, 2)

    @property
    def label(self) -> str:
        """Short, human-friendly name: the last two path segments."""
        parts = self.session_id.split("/")
        return "/".join(parts[-2:]) if len(parts) >= 2 else self.session_id


def discover(root: str | Path, pattern: str) -> list[FileInfo]:
    """Walk one source root and return the files matching `pattern`.

    The session id is the path relative to the root, so it stays stable and
    independent of any single application's naming scheme.
    """
    base = Path(root)
    if not base.is_dir():
        return []

    found: list[FileInfo] = []
    for path in base.glob(pattern):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        try:
            session_id = path.relative_to(base).as_posix()
        except ValueError:
            session_id = path.as_posix()
        found.append(
            FileInfo(
                path=str(path),
                session_id=session_id,
                size=stat.st_size,
                mtime=stat.st_mtime,
            )
        )
    found.sort(key=lambda info: info.size, reverse=True)
    return found


def summarize(files: Iterable[FileInfo]) -> dict[str, float]:
    items = list(files)
    total = sum(info.size for info in items)
    return {
        "count": float(len(items)),
        "total_mb": round(total / MB, 2),
        "largest_mb": round(max((info.size for info in items), default=0) / MB, 2),
    }