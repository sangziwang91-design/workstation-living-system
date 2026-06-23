from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import BinaryIO
import os


class ProcessLease(AbstractContextManager):
    """Cross-platform exclusive process lease backed by a lock file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file: BinaryIO | None = None

    def acquire(self) -> None:
        file_handle = self.path.open("a+b")
        self._file = file_handle
        if os.name == "nt":
            import msvcrt

            try:
                file_handle.seek(0)
                msvcrt.locking(file_handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            except OSError as exc:
                file_handle.close()
                self._file = None
                raise RuntimeError("another WLS process holds the lease") from exc
        else:
            import fcntl

            try:
                fcntl.flock(file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                file_handle.close()
                self._file = None
                raise RuntimeError("another WLS process holds the lease") from exc
        file_handle.seek(0)
        file_handle.truncate()
        file_handle.write(str(os.getpid()).encode("ascii"))
        file_handle.flush()
        os.fsync(file_handle.fileno())

    def release(self) -> None:
        if self._file is None:
            return
        if os.name == "nt":
            import msvcrt

            self._file.seek(0)
            try:
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
            except OSError:
                pass
        else:
            import fcntl

            try:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        self._file.close()
        self._file = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False
