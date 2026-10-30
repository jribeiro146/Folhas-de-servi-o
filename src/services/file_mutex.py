"""Non-blocking process/thread exclusion, released by the OS after a crash."""

import os
from contextlib import contextmanager
from pathlib import Path


class FileMutexBusy(Exception):
    pass


@contextmanager
def file_mutex(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Never delete lock files: another process may already hold the same inode.
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            # Windows permits byte-range locks beyond EOF. Writing a filler
            # before acquiring the lock races with another holder of that byte.
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise FileMutexBusy("Operação já em curso.") from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise FileMutexBusy("Operação já em curso.") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
