"""The pure command executor for machine-observed verification (epic:
machine-executed verification and dispatch observability, spec Sec2/Sec3,
task T1-executor).

Cross-reference: `wave_delivery/runner.py:585-615`'s dispatch path shares
the same shape -- `subprocess` capture -> wall-clock duration -> a
lossy-decoded tail -- but is NOT reused here. `runner.py` calls
`subprocess.run(..., timeout=...)`, which on expiry kills only the direct
child, not its process group; a dispatched runner command that backgrounds
a grandchild (`cmd &`) survives a runner timeout today. This epic's
contract (spec AC-5, the "child-survivor" test) requires that grandchild
be dead too, which needs `start_new_session=True` plus an explicit
`killpg` escalation that `subprocess.run`'s high-level timeout cannot
express. A parallel implementation is intentional and
constitution-sanctioned here because `runner.py` is dispatch-specific (its
contract, one shot in/one shot out, is deliberately different); it is not
something this task edits, since `wave_delivery/runner.py` is outside
T1-executor's conflict domains (`wave_delivery/verify_run.py`,
`tests/test_machine_verification.py`). `runner.py` does not currently
carry a reciprocal comment pointing back at this module's timeout fix --
adding one is deferred to whoever next touches `runner.py`'s
dispatch-timeout path, tracked via a controller note rather than done
silently here.

Pure of state writes: `execute()` returns the observed evidence structure;
callers (`review.py`'s `record_verification`, `finalize.py`'s
`record_final_verification`) are the ones that persist it. No CLI flags, no
state/config reads, no schema validation of the returned shape (T2 owns
validation) live in this module.
"""

from __future__ import annotations

import hashlib
import os
import select
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from .errors import ValidationError

# 64MB per-command capture cap (spec Sec2). A module-level constant rather
# than an `execute()` parameter -- the contract signature
# (commands, *, cwd, timeout_seconds, log_path) has no slot for it -- so
# tests exercise truncation by patching this name down rather than by
# threading a new parameter through the public contract.
_CAPTURE_CAP_BYTES = 64 * 1024 * 1024

# Tail length recorded on every executed entry (spec Sec2), lossy-decoded
# only for this display string -- the hash and the log file itself carry the
# raw bytes.
_TAIL_BYTES = 4096

# SIGTERM-then-grace-then-SIGKILL, per spec Sec2's process-group timeout.
_KILL_GRACE_SECONDS = 5.0

_READ_CHUNK_SIZE = 65536

# How long a `select()` call blocks per loop iteration while waiting on
# output before re-checking the deadline. Small enough that a timeout is
# detected promptly, large enough not to busy-loop.
_POLL_INTERVAL_SECONDS = 0.05

_RESERVED_FILE_MODE = 0o600

# Generous bound on numbered-path collision retries (mirrors
# handover.py's `_MAX_ATTEMPT_DIR_RETRIES` reasoning): should never bite a
# legitimate caller, just guarantees termination if a directory is somehow
# already saturated with numbered entries for this prefix.
_MAX_RESERVE_ATTEMPTS = 1000


def reserve_numbered_path(directory: Path | str, prefix: str, suffix: str) -> Path:
    """Reserve the next available ``{prefix}{n}{suffix}`` path under `directory`.

    Uses ``O_CREAT | O_EXCL`` so the reservation itself IS the collision
    check -- there is no separate exists()-then-create window (contrast
    `handover.py`'s `_next_attempt_number`, which counts existing entries
    and is racy under concurrent callers by design choice there; this
    variant is the O_EXCL-reservation form the spec calls for, for both
    verify-run logs and dispatch packet files). Numbering starts at 1 and
    is independent per distinct `prefix`. The reserved file is created
    empty at mode 0600 (the log-file permission the spec requires); the
    caller writes its actual content afterward.
    """
    directory_path = Path(directory)
    directory_path.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, _MAX_RESERVE_ATTEMPTS + 1):
        candidate = directory_path / f"{prefix}{attempt}{suffix}"
        try:
            descriptor = os.open(
                candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, _RESERVED_FILE_MODE
            )
        except FileExistsError:
            continue
        os.close(descriptor)
        return candidate
    raise ValidationError(
        f"could not reserve a numbered path under {directory_path} with prefix "
        f"{prefix!r} after {_MAX_RESERVE_ATTEMPTS} attempts"
    )


def execute(
    commands: list[str],
    *,
    cwd: Path | str,
    timeout_seconds: float,
    log_path: Path | str,
) -> dict[str, Any]:
    """Execute `commands` sequentially, observing (never trusting) the results.

    Each command runs in a fresh `sh -c`, its own process group
    (`start_new_session=True`), stdout+stderr merged, with the calling
    process's environment inherited verbatim (no `env=` override) and sh's
    own pipeline semantics (no pipefail injected). The first command whose
    status is not "passed" (a nonzero exit or a timeout) stops the
    sequence; every later command is recorded as ``{"command": ...,
    "status": "skipped"}`` with no other fields -- nothing about a command
    that never ran is knowable, so nothing else is claimed.

    Returns ``{"results": [entry, ...], "logSha256": <hex>}``. Raises
    `ValidationError` on an empty command list: "nothing observed is not a
    pass" (spec Sec2) applies before any process is spawned.
    """
    if not commands:
        raise ValidationError(
            "verify_run.execute requires a non-empty command list: "
            "nothing observed is not a pass"
        )

    results: list[dict[str, Any]] = []
    log_hasher = hashlib.sha256()
    stop = False
    # `open(log_path, "wb")` would create a fresh file at the umask-derived
    # default mode (typically 0644) if `log_path` was not already created
    # by `reserve_numbered_path` (which pre-creates it at 0600). Using
    # `os.open` with an explicit mode is defensive for the non-reserved
    # case; if the path already exists (the reserved case) the mode is
    # ignored and the existing 0600 permissions are left untouched.
    log_descriptor = os.open(
        log_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, _RESERVED_FILE_MODE
    )
    with os.fdopen(log_descriptor, "wb") as log_file:
        for command in commands:
            if stop:
                results.append({"command": command, "status": "skipped"})
                continue
            # Per-command framing in the shared log file, ahead of that
            # command's own captured bytes -- purely for a human reading the
            # merged log; framing bytes are folded into `logSha256` (the
            # record-level hash is over the log file's actual bytes) but
            # never into the per-command `outputSha256`, which is the raw
            # captured bytes only.
            frame = f"$ {command}\n".encode("utf-8", errors="replace")
            log_file.write(frame)
            log_hasher.update(frame)
            entry = _run_one(
                command,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                log_file=log_file,
                log_hasher=log_hasher,
            )
            results.append(entry)
            if entry["status"] != "passed":
                stop = True
        log_file.flush()
        os.fsync(log_file.fileno())

    return {"results": results, "logSha256": log_hasher.hexdigest()}


def _run_one(
    command: str,
    *,
    cwd: Path | str,
    timeout_seconds: float,
    log_file: Any,
    log_hasher: "hashlib._Hash",
) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + timeout_seconds

    try:
        process = subprocess.Popen(
            ["sh", "-c", command],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # own process group -- see module docstring / spec AC-5
        )
    except OSError as error:
        # Mirrors runner.py:590's precedent for the same failure mode: a
        # Popen construction failure (missing cwd, fork/exec resource
        # exhaustion) is execution evidence to record, not an exception to
        # let escape this module's observed-evidence contract. The process
        # never started, so there is no real exit code -- but T2's
        # `_validate_result_entry` (wave_delivery/schema.py, spec
        # AC-1/AC-9) requires the full executed-failure shape whenever
        # `timedOut` isn't true (`exitCode: null` is reserved for the
        # timeout shape only). 127 (the shell "command not found"
        # convention) is used here as an explicitly synthetic marker for
        # "never executed", not an observed value; durationMs and
        # outputSha256 (of the empty, never-started capture) ARE observed.
        return {
            "command": command,
            "status": "failed",
            "exitCode": 127,
            "durationMs": int((time.monotonic() - started) * 1000),
            "outputSha256": hashlib.sha256(b"").hexdigest(),
            "tail": f"verify_run: could not exec command: {error}",
        }
    stdout = process.stdout
    assert stdout is not None

    output_hasher = hashlib.sha256()
    tail_buffer = bytearray()
    state = {"captured": 0, "truncated": False}

    def consume(chunk: bytes) -> None:
        if not chunk or state["truncated"]:
            return
        remaining_capacity = _CAPTURE_CAP_BYTES - state["captured"]
        if remaining_capacity <= 0:
            state["truncated"] = True
            return
        if len(chunk) > remaining_capacity:
            chunk = chunk[:remaining_capacity]
            state["truncated"] = True
        output_hasher.update(chunk)
        state["captured"] += len(chunk)
        tail_buffer.extend(chunk)
        if len(tail_buffer) > _TAIL_BYTES:
            del tail_buffer[: len(tail_buffer) - _TAIL_BYTES]
        log_file.write(chunk)
        log_hasher.update(chunk)

    timed_out = False
    exit_code: int | None = None
    try:
        # EOF on this fd -- not "the direct child exited" -- is the only
        # signal that every holder of the write end is gone. A backgrounded
        # grandchild (`cmd &`) that outlives its parent `sh` (which may exit
        # on its own, e.g. `sleep 20 & exit 0`) keeps the pipe open after
        # `process.poll()` already shows the child dead; breaking out early
        # on that poll (as this loop used to) means the deadline is never
        # consulted again and a later blocking read can hang for as long as
        # the grandchild lived. So this loop only ever stops on a real EOF
        # or on deadline expiry -- never on the direct child's exit alone.
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            ready, _, _ = select.select(
                [stdout], [], [], min(remaining, _POLL_INTERVAL_SECONDS)
            )
            if not ready:
                continue
            chunk = os.read(stdout.fileno(), _READ_CHUNK_SIZE)
            if not chunk:
                break
            consume(chunk)

        if not timed_out:
            # EOF on the pipe proves every write-end holder is gone, but
            # NOT that the direct child has exited or been reaped -- e.g.
            # `exec 1>/dev/null 2>/dev/null; sleep 15` closes the pipe
            # almost instantly while `sh` (now running `sleep 15` in the
            # same process image) keeps running well past the deadline. An
            # unbounded `process.wait()` here used to block for the rest
            # of that command's runtime and record "passed" once it
            # finally exited, silently ignoring the timeout. Bounding this
            # wait by the remaining deadline and falling into the same
            # timeout+group-kill path on expiry closes that gap.
            remaining = deadline - time.monotonic()
            try:
                exit_code = process.wait(timeout=max(remaining, 0))
            except subprocess.TimeoutExpired:
                timed_out = True

        if timed_out:
            exit_code = None
            # `_kill_process_group` used to decide whether to escalate to
            # SIGKILL by calling `process.wait()` on the DIRECT child
            # only. When `sh` has already exited (a backgrounded
            # grandchild, `sh` itself exits) that wait() returns instantly
            # and looked like "the group is handled", even though a
            # SIGTERM-immune grandchild (`trap '' TERM`) was still holding
            # this pipe open -- so the SIGKILL escalation was skipped
            # entirely and a later blocking read hung for as long as the
            # grandchild lived. The group is now killed with the SIGKILL
            # escalation unconditional on elapsed grace time, never on the
            # direct child's wait() outcome.
            try:
                _kill_process_group(process, stdout, consume)
            except OSError as error:
                # `_kill_process_group`'s grace-window drain writes into
                # `consume`, which can hit ENOSPC/EIO on the shared log
                # file; that function guarantees the group is SIGKILLed
                # and reaped before this propagates (see its docstring).
                # Mirroring the Popen-OSError precedent above, this is
                # recorded as failed evidence rather than left to crash
                # `execute()` mid-run with the group already handled.
                duration_ms = int((time.monotonic() - started) * 1000)
                tail_text = bytes(tail_buffer).decode("utf-8", errors="replace")
                entry: dict[str, Any] = {
                    "command": command,
                    "status": "failed",
                    "exitCode": None,
                    "durationMs": duration_ms,
                    "outputSha256": output_hasher.hexdigest(),
                    "tail": (
                        f"{tail_text}\nverify_run: error draining output "
                        f"during kill: {error}"
                    ),
                    "timedOut": True,
                }
                if state["truncated"]:
                    entry["truncated"] = True
                return entry
            # A final bounded drain as a safety net: SIGKILL isn't
            # interceptable, so every pipe holder should already be gone
            # by the time `_kill_process_group` returns, but this still
            # gets its own hard deadline (rather than a blocking read())
            # so a pathological case -- an fd surviving outside the killed
            # group -- can't hang the drain forever. On expiry the fd is
            # simply closed (in the `finally` below) and whatever was
            # captured up to that point is what gets recorded.
            drain_deadline = time.monotonic() + _KILL_GRACE_SECONDS
            while True:
                remaining = drain_deadline - time.monotonic()
                if remaining <= 0:
                    break
                ready, _, _ = select.select(
                    [stdout], [], [], min(remaining, _POLL_INTERVAL_SECONDS)
                )
                if not ready:
                    continue
                chunk = os.read(stdout.fileno(), _READ_CHUNK_SIZE)
                if not chunk:
                    break
                consume(chunk)
    finally:
        stdout.close()

    duration_ms = int((time.monotonic() - started) * 1000)
    tail_text = bytes(tail_buffer).decode("utf-8", errors="replace")

    entry: dict[str, Any] = {
        "command": command,
        "status": "failed" if (timed_out or exit_code != 0) else "passed",
        "exitCode": exit_code,
        "durationMs": duration_ms,
        "outputSha256": output_hasher.hexdigest(),
        "tail": tail_text,
    }
    if timed_out:
        entry["timedOut"] = True
    if state["truncated"]:
        entry["truncated"] = True
    return entry


def _kill_process_group(
    process: "subprocess.Popen[bytes]",
    stdout: Any,
    consume: Any,
    *,
    grace_seconds: float = _KILL_GRACE_SECONDS,
) -> None:
    """SIGTERM the command's process group, then escalate to SIGKILL once
    `grace_seconds` has elapsed -- regardless of whether the DIRECT child
    (`process`) has already exited (spec Sec2 / AC-5: "SIGTERM, 5-second
    grace, SIGKILL"). `process.wait()` only proves `sh` itself is gone; a
    backgrounded grandchild that inherited the pgid (and the write end of
    `stdout`) can outlive `sh`'s own exit and even ignore SIGTERM outright
    (`trap '' TERM`), so treating the direct child's exit as "the group is
    handled" -- as this used to, by returning right after a successful
    `process.wait(timeout=grace_seconds)` on the direct child alone --
    skips the SIGKILL escalation while a SIGTERM-immune grandchild is
    still holding the pipe open, and a caller blocking on that pipe next
    hangs for as long as the grandchild lives.

    While waiting out the grace window this also drains `stdout` (via
    `consume`) so bytes written before the kill lands aren't lost; EOF on
    the pipe (every write-end holder gone) ends the wait early, but
    `grace_seconds` elapsing with no EOF always falls through to SIGKILL
    regardless -- the grace window is a courtesy to a cooperating process,
    not a promise this function returns before it elapses.
    `start_new_session=True` at spawn makes the process its own
    session/group leader, so its pgid equals its pid -- no `os.getpgid`
    race against the process having already exited.

    Real worst-case bound past `timeout_seconds`, contrary to what the
    spec prose above ("SIGTERM, 5-second grace, SIGKILL") reads as a
    single grace window: this function alone can take up to
    `2 * grace_seconds` (the drain above, then the post-SIGKILL reap
    below), and `_run_one` layers its own bounded safety drain of a
    further `_KILL_GRACE_SECONDS` on top once this returns -- so
    `execute()`'s measured worst case is `timeout_seconds + 3 *
    grace_seconds`, not `timeout_seconds + grace_seconds`.
    """
    pgid = process.pid
    drain_error: OSError | None = None
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    else:
        grace_deadline = time.monotonic() + grace_seconds
        try:
            while True:
                remaining = grace_deadline - time.monotonic()
                if remaining <= 0:
                    break
                ready, _, _ = select.select(
                    [stdout], [], [], min(remaining, _POLL_INTERVAL_SECONDS)
                )
                if not ready:
                    continue
                chunk = os.read(stdout.fileno(), _READ_CHUNK_SIZE)
                if not chunk:
                    break  # EOF -- every holder of the write end is gone
                consume(chunk)
        except OSError as error:
            # `consume` writes each drained chunk to the shared log file;
            # ENOSPC/EIO there must not skip the SIGKILL escalation below
            # -- doing so would leave the process group alive with
            # nothing left to ever kill it. Stash the error and re-raise
            # once the group is confirmed dead and reaped (below), so the
            # caller (`_run_one`) can turn it into recorded evidence --
            # mirroring the Popen-OSError precedent there -- instead of
            # it vanishing along with a live process group.
            drain_error = error
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    # Reap the direct child so it doesn't linger as this process's zombie;
    # SIGKILL is not interceptable, so this should return almost at once.
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass
    if drain_error is not None:
        raise drain_error
