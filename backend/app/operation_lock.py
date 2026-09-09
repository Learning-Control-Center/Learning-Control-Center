from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO


def database_path(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise RuntimeError("Operations require a file-backed SQLite database URL.")
    raw_path = database_url.removeprefix(prefix)
    if not raw_path or raw_path == ":memory:":
        raise RuntimeError("Operations require a file-backed SQLite database URL.")
    return Path(raw_path).resolve()


@contextmanager
def exclusive_operation_lock(database_url: str) -> Iterator[BinaryIO]:
    path = database_path(database_url)
    parent_missing = not path.parent.exists()
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if parent_missing:
        path.parent.chmod(0o700)
    lock_path = path.with_suffix(path.suffix + ".operations.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_APPEND | os.O_RDWR, 0o600)
    os.chmod(lock_path, 0o600)
    with os.fdopen(descriptor, "a+b") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("The database is already open by LCC or another operation.") from exc
        yield lock_file
