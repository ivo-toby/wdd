"""Atomic, revisioned local JSON storage for controller state."""

from __future__ import annotations

import json
import os
import socket
import tempfile
import time
import uuid
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

    # `read_raw_unlocked` / `recover_locked` / `load_recovered` (spec Sec1's
    # "locking layers"): the archive transaction (finalize.archive_scope) is
    # a multi-step, crash-recoverable mutation, and the lock this class hands
    # out via `locked()` is NOT reentrant -- so recovery needs its own,
    # explicit layering rather than happening inside plain `read()`:
    #
    #   - `read_raw_unlocked()`: parse + validate only, no recovery pass --
    #     the exact `read()` behavior above, under the explicit name that
    #     documents "no recovery happened here" at call sites that care.
    #   - `recover_locked()`: run the archive-recovery matrix, ASSUMING the
    #     caller already holds `locked()` (e.g. `apply_mutation`, right after
    #     acquiring its own lock, before running its mutator). Never
    #     acquires the lock itself -- doing so would deadlock.
    #   - `load_recovered()`: the read-only caller's layer -- acquires the
    #     lock itself, recovers, and returns the (possibly healed) state.
    #
    # Read-only commands (`status`/`next`/`doctor`) use `load_recovered()`;
    # every governed mutation goes through `apply_mutation`, which already
    # calls `recover_locked()` under its own lock. `read()` itself is left
    # unchanged (still recovery-free) since most call sites read state for
    # reasons that have nothing to do with the archive transaction.
    def read_raw_unlocked(self) -> dict[str, Any]:
        return self.read()

    def recover_locked(self) -> dict[str, Any]:
        # Local import: finalize.py already imports StateStore from this
        # module at its own top level, so importing finalize.py back from
        # here at module scope would cycle. Deferring the import into this
        # method body is safe -- by the time any caller actually invokes
        # `recover_locked()`, both modules have long since finished loading.
        from .finalize import recover_archive_transaction

        state = self.read_raw_unlocked()
        recovered = recover_archive_transaction(state, self.path.parent)
        if recovered is state:
            return state
        self.write(recovered)
        return recovered

    def load_recovered(self) -> dict[str, Any]:
        with self.locked():
            return self.recover_locked()

    def write(self, state: dict[str, Any]) -> None:
        validate_state(state)
        atomic_write_text(self.path, json.dumps(state, indent=2, sort_keys=True) + "\n")

    def _lock_holder(self) -> dict[str, str]:
        try:
            raw = self.lock_path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return {}
        fields = {}
        for line in raw.splitlines():
            name, _, value = line.partition("=")
            if value:
                fields[name.strip()] = value.strip()
        return fields

    def _holder_is_gone(self, holder: dict[str, str]) -> bool:
        """True when the recorded holder cannot possibly still be running.

        A crash inside the lock never runs the release, so without this the
        whole scope stays frozen until a human deletes the file — and manual
        deletion is itself unsafe, because it can remove a live holder's lock.
        The check is deliberately conservative: an unparseable file, a
        different host, or a live pid all mean "leave it alone".
        """
        if holder.get("host") != socket.gethostname():
            return False
        try:
            pid = int(holder.get("pid", ""))
        except ValueError:
            return False
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        except OSError:
            return False
        return False

    @contextmanager
    def locked(self) -> Iterator[None]:
        deadline = time.monotonic() + self.lock_timeout_seconds
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        # A token, not just a pid: releasing must never unlink a lock this
        # process does not hold. Reclaiming a stale lock (or a human deleting
        # one) otherwise lets the previous holder's release remove the new
        # holder's lock, admitting a second writer under an "exclusive" lock.
        token = f"{os.getpid()}:{uuid.uuid4().hex}"
        while True:
            try:
                descriptor = os.open(
                    self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                )
            except FileExistsError:
                holder = self._lock_holder()
                if self._holder_is_gone(holder):
                    try:
                        # Only remove the exact file we just inspected.
                        if self._lock_holder().get("token") == holder.get("token"):
                            self.lock_path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                if time.monotonic() >= deadline:
                    raise LockUnavailable(
                        f"state lock is held: {self.lock_path} ({holder.get('pid', 'unknown pid')} "
                        f"on {holder.get('host', 'unknown host')}); inspect it before retrying"
                    )
                time.sleep(0.05)
                continue
            else:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(
                        f"pid={os.getpid()}\nhost={socket.gethostname()}\ntoken={token}\n"
                    )
                    handle.flush()
                    os.fsync(handle.fileno())
                break
        try:
            yield
        finally:
            if self._lock_holder().get("token") == token:
                try:
                    self.lock_path.unlink()
                except FileNotFoundError:
                    pass
