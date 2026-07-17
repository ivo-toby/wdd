"""Atomic, revisioned local JSON storage for controller state."""

from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .errors import LockUnavailable, ValidationError
from .schema import validate_state


def atomic_write_text(path: Path, content: str) -> None:
    """Replace a file atomically after flushing its contents to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


class StateStore:
    def __init__(self, path: Path | str, *, lock_timeout_seconds: float = 5.0) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(f".{self.path.name}.lock")
        self.lock_timeout_seconds = lock_timeout_seconds

    def exists(self) -> bool:
        return self.path.exists()

    def read(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise ValidationError(f"state file does not exist: {self.path}") from error
        try:
            state = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValidationError(f"state file is not valid JSON: {self.path}: {error}") from error
        validate_state(state)
        return state

    def write(self, state: dict[str, Any]) -> None:
        validate_state(state)
        atomic_write_text(self.path, json.dumps(state, indent=2, sort_keys=True) + "\n")

    @contextmanager
    def locked(self) -> Iterator[None]:
        deadline = time.monotonic() + self.lock_timeout_seconds
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                descriptor = os.open(
                    self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                )
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise LockUnavailable(
                        f"state lock is held: {self.lock_path}; inspect it before retrying"
                    )
                time.sleep(0.05)
                continue
            else:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(f"pid={os.getpid()}\n")
                break
        try:
            yield
        finally:
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass
