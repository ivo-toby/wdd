"""Tests for `wave_delivery.verify_run` — the machine-verification executor
(epic: machine-executed verification and dispatch observability, task
T1-executor). Covers `execute()`'s observed-evidence contract (spec AC-1,
AC-4, AC-5, AC-9) and `reserve_numbered_path()`'s O_EXCL reservation.

Local helpers only (no cross-file imports between test modules, per the
phase-6a/6b test conventions -- see tests/test_execution_surfaces.py).
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path
from typing import Any

from wave_delivery.errors import ValidationError
from wave_delivery.verify_run import execute, reserve_numbered_path


class ExecuteEmptyCommandListTests(unittest.TestCase):
    def test_empty_command_list_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "verify-1.log"
            with self.assertRaises(ValidationError):
                execute([], cwd=tmp, timeout_seconds=5, log_path=log_path)
            # Refusal happens before anything executes: no log file materializes.
            self.assertFalse(log_path.exists())


class ExecuteHappyPathTests(unittest.TestCase):
    def test_single_passing_command_shape_and_log_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = reserve_numbered_path(Path(tmp), "verify-task-", ".log")
            result = execute(
                ["echo hello"], cwd=tmp, timeout_seconds=5, log_path=log_path
            )
            self.assertEqual(list(result.keys()), ["results", "logSha256"])
            self.assertEqual(len(result["results"]), 1)
            entry = result["results"][0]
            self.assertEqual(entry["command"], "echo hello")
            self.assertEqual(entry["status"], "passed")
            self.assertEqual(entry["exitCode"], 0)
            self.assertIsInstance(entry["durationMs"], int)
            self.assertGreaterEqual(entry["durationMs"], 0)
            self.assertEqual(entry["outputSha256"], hashlib.sha256(b"hello\n").hexdigest())
            self.assertEqual(entry["tail"], "hello\n")
            # No fabricated fields on a plain passing entry.
            self.assertEqual(
                set(entry.keys()),
                {"command", "status", "exitCode", "durationMs", "outputSha256", "tail"},
            )
            # logSha256 is over the actual log file bytes on disk.
            self.assertEqual(
                result["logSha256"], hashlib.sha256(log_path.read_bytes()).hexdigest()
            )
            self.assertIn(b"hello", log_path.read_bytes())

    def test_merged_stdout_and_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "verify.log"
            result = execute(
                ["echo out; echo err >&2"], cwd=tmp, timeout_seconds=5, log_path=log_path
            )
            entry = result["results"][0]
            self.assertEqual(entry["status"], "passed")
            self.assertIn("out", entry["tail"])
            self.assertIn("err", entry["tail"])

    def test_runs_in_given_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "marker.txt"
            marker.write_text("present\n", encoding="utf-8")
            log_path = Path(tmp) / "verify.log"
            result = execute(
                ["cat marker.txt"], cwd=tmp, timeout_seconds=5, log_path=log_path
            )
            entry = result["results"][0]
            self.assertEqual(entry["status"], "passed")
            self.assertEqual(entry["tail"], "present\n")


class ExecuteSkippedShapeTests(unittest.TestCase):
    def test_first_failure_stops_sequence_and_skips_have_no_other_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "verify.log"
            result = execute(
                ["echo one", "exit 1", "echo three", "echo four"],
                cwd=tmp,
                timeout_seconds=5,
                log_path=log_path,
            )
            results = result["results"]
            self.assertEqual(len(results), 4)
            self.assertEqual(results[0]["status"], "passed")
            self.assertEqual(results[1]["status"], "failed")
            self.assertEqual(results[1]["exitCode"], 1)
            self.assertEqual(results[2], {"command": "echo three", "status": "skipped"})
            self.assertEqual(results[3], {"command": "echo four", "status": "skipped"})


class ExecuteTimeoutShapeTests(unittest.TestCase):
    def test_timeout_entry_shape_and_sequence_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "verify.log"
            result = execute(
                ["sleep 5", "echo never"],
                cwd=tmp,
                timeout_seconds=1,
                log_path=log_path,
            )
            results = result["results"]
            self.assertEqual(len(results), 2)
            timed_out_entry = results[0]
            self.assertEqual(timed_out_entry["command"], "sleep 5")
            self.assertEqual(timed_out_entry["status"], "failed")
            self.assertIsNone(timed_out_entry["exitCode"])
            self.assertTrue(timed_out_entry["timedOut"])
            # Capture fields are still present alongside timedOut.
            self.assertIn("durationMs", timed_out_entry)
            self.assertIn("outputSha256", timed_out_entry)
            self.assertIn("tail", timed_out_entry)
            self.assertEqual(results[1], {"command": "echo never", "status": "skipped"})


def _process_is_dead(pid: int) -> bool:
    """True once `pid` is gone or lingering only as an unreaped zombie.

    A killed grandchild is reparented away from our process (its parent,
    `sh`, died with it), so nothing in this test tree ever calls `wait()`
    on it -- some ancestor (PID 1, or a subreaper) reaps it on its own
    schedule, and that schedule is not something this test controls or
    can assume is slow: on a system with a prompt subreaper (systemd,
    tini, s6, and most container inits, including the one this sandbox
    runs under -- empirically confirmed: `/proc/<pid>/status` is already
    gone on the very first check after every observed group kill here,
    never caught mid-zombie) the grandchild can be fully reaped before
    this test ever gets a chance to look. `FileNotFoundError` -- the pid
    absent from the process table entirely -- is therefore at least as
    strong evidence of "no longer running" as an observed zombie line;
    there is no code path by which a pid we captured as running moments
    ago (via `$!`) reappears as a *different*, still-alive process inside
    this single test's lifetime. So both branches -- zombie or vanished --
    are treated as dead here.

    This check is deliberately Linux-only (the caller below is gated with
    `skipUnless`): on a platform without `/proc` at all, this would report
    "dead" on the very first call regardless of whether the kill has had
    any chance to take effect yet, which is a genuinely vacuous oracle --
    a different failure mode than the reap-timing question above, and the
    one the platform gate exists to rule out.
    """
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("State:"):
                    return "zombie" in line.lower()
        return False
    except FileNotFoundError:
        return True


@unittest.skipUnless(
    sys.platform.startswith("linux"),
    "child-survivor oracle reads /proc/<pid>/status, which is Linux-only",
)
class ExecuteChildSurvivorTests(unittest.TestCase):
    def test_backgrounded_grandchild_is_killed_with_the_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = Path(tmp) / "child.pid"
            log_path = Path(tmp) / "verify.log"
            command = f"sleep 60 & echo $! > {pidfile}; wait"
            started = time.monotonic()
            execute([command], cwd=tmp, timeout_seconds=1, log_path=log_path)
            elapsed = time.monotonic() - started
            # Must return promptly (well under the child's 60s sleep), proving
            # the group was killed rather than waited out.
            self.assertLess(elapsed, 10)
            child_pid = int(pidfile.read_text(encoding="utf-8").strip())
            deadline = time.monotonic() + 2.0
            while not _process_is_dead(child_pid) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(
                _process_is_dead(child_pid),
                f"child pid {child_pid} is still running after group kill",
            )


class ExecuteNaturalExitPipeHolderTests(unittest.TestCase):
    def test_deadline_enforced_when_child_exits_but_grandchild_holds_pipe(self) -> None:
        """Regression test for the probe: `sleep 20 & exit 0` with a 1s
        timeout used to return after the full 20s with status "passed".

        Unlike `ExecuteChildSurvivorTests` (where `sh` blocks on `wait` and
        so never exits on its own before the group kill), here `sh`
        backgrounds the sleep and exits immediately -- `process.poll()` on
        the direct child goes non-None almost at once, while the
        grandchild still holds the write end of the stdout pipe open. The
        old code treated the direct child's exit as proof the pipe was
        fully drained and fell into an unbounded blocking read; the fix
        keeps waiting on EOF-or-deadline and only trusts the pipe is done
        once every holder -- including the grandchild -- is confirmed dead
        via a group kill.
        """
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = Path(tmp) / "child.pid"
            log_path = Path(tmp) / "verify.log"
            command = f"sleep 20 & echo $! > {pidfile}; exit 0"
            started = time.monotonic()
            result = execute([command], cwd=tmp, timeout_seconds=1, log_path=log_path)
            elapsed = time.monotonic() - started
            self.assertLess(
                elapsed,
                3,
                "execute() must enforce the deadline instead of waiting out "
                "the backgrounded grandchild",
            )
            entry = result["results"][0]
            self.assertEqual(entry["status"], "failed")
            self.assertNotEqual(entry["status"], "passed")
            self.assertTrue(entry.get("timedOut"))
            if sys.platform.startswith("linux"):
                child_pid = int(pidfile.read_text(encoding="utf-8").strip())
                deadline = time.monotonic() + 2.0
                while not _process_is_dead(child_pid) and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(
                    _process_is_dead(child_pid),
                    f"grandchild pid {child_pid} is still running after the "
                    "deadline-triggered group kill",
                )


@unittest.skipUnless(
    sys.platform.startswith("linux"),
    "child-survivor oracle reads /proc/<pid>/status, which is Linux-only",
)
class ExecuteSigtermImmuneGrandchildTests(unittest.TestCase):
    def test_deadline_enforced_past_a_sigterm_ignoring_grandchild(self) -> None:
        """Regression test for the review finding: `_kill_process_group`
        used to decide whether to escalate to SIGKILL by calling
        `process.wait()` on the DIRECT child only. When that direct child
        (the outer `sh`) has already exited -- as here, where it
        backgrounds the grandchild and exits immediately -- that wait()
        returned instantly and looked like "handled", so the SIGKILL
        escalation never ran. A grandchild that ignores SIGTERM (`trap ''
        TERM`) then held the pipe open forever, and the post-timeout
        drain blocked for as long as it lived (its full 30s sleep here).
        The fix always escalates to SIGKILL once the grace window elapses,
        regardless of the direct child's own exit status.
        """
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = Path(tmp) / "child.pid"
            log_path = Path(tmp) / "verify.log"
            command = (
                f"sh -c 'trap \"\" TERM; echo $$ > {pidfile}; sleep 30' & exit 0"
            )
            started = time.monotonic()
            result = execute([command], cwd=tmp, timeout_seconds=1, log_path=log_path)
            elapsed = time.monotonic() - started
            # Pre-fix this hung for the full 30s. Post-fix it's bounded by
            # the 1s command timeout plus the spec-mandated 5s SIGTERM
            # grace (AC-5, .wdd/epics/machine-verification/spec.md:37)
            # before the SIGKILL escalation fires -- ~6s expected. 8s
            # leaves comfortable scheduling margin while still failing
            # loudly on any regression back toward the 30s hang.
            self.assertLess(elapsed, 8)
            entry = result["results"][0]
            self.assertEqual(entry["status"], "failed")
            self.assertTrue(entry.get("timedOut"))
            child_pid = int(pidfile.read_text(encoding="utf-8").strip())
            deadline = time.monotonic() + 2.0
            while not _process_is_dead(child_pid) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(
                _process_is_dead(child_pid),
                f"SIGTERM-ignoring grandchild pid {child_pid} is still "
                "running after the deadline-triggered group kill",
            )


@unittest.skipUnless(
    sys.platform.startswith("linux"),
    "child-survivor oracle reads /proc/<pid>/status, which is Linux-only",
)
class ExecuteKillDrainLogWriteFailureTests(unittest.TestCase):
    def test_log_write_oserror_during_kill_drain_still_kills_group_and_records_failure(
        self,
    ) -> None:
        """Regression test for the review finding: `_kill_process_group`'s
        grace-window drain calls `consume`, which writes each drained
        chunk straight to the shared log file with no try/finally around
        the SIGKILL escalation below it. An OSError there (ENOSPC/EIO)
        used to propagate straight out of `_kill_process_group`, skipping
        `killpg(pgid, SIGKILL)` entirely and leaking the process group.
        The fix wraps the drain in try/finally so SIGKILL always fires
        before the error is re-raised, and `_run_one` converts that
        re-raised error into a recorded failed entry (mirroring the
        Popen-OSError precedent) instead of letting it crash `execute()`
        mid-run.

        The command traps SIGTERM to emit output (`caught`) and exit
        cleanly, without ever backgrounding a grandchild -- so the only
        process in the group is the direct child itself, and that output
        is what reaches `consume` inside `_kill_process_group`'s grace
        window (the command produces nothing before the timeout, so the
        first and only chunk write happens there, not in the main read
        loop or the caller's post-kill safety drain).
        """
        real_fdopen = os.fdopen

        class _FailAfterNWrites:
            def __init__(self, real_file: Any, allowed: int) -> None:
                self._real = real_file
                self._allowed = allowed
                self._calls = 0

            def write(self, data: bytes) -> int:
                self._calls += 1
                if self._calls > self._allowed:
                    raise OSError(28, "No space left on device")
                return self._real.write(data)

            def __getattr__(self, name: str) -> Any:
                return getattr(self._real, name)

            def __enter__(self) -> "_FailAfterNWrites":
                return self

            def __exit__(self, *exc_info: Any) -> None:
                self._real.__exit__(*exc_info)

        def fake_fdopen(fd: int, mode: str) -> Any:
            # allowed=1 lets the per-command "$ <command>\n" framing
            # write through untouched; the next write -- the "caught\n"
            # chunk drained inside `_kill_process_group` -- is the one
            # that must fail.
            return _FailAfterNWrites(real_fdopen(fd, mode), allowed=1)

        with tempfile.TemporaryDirectory() as tmp:
            pidfile = Path(tmp) / "child.pid"
            log_path = Path(tmp) / "verify.log"
            command = f"trap 'echo caught; exit 0' TERM; echo $$ > {pidfile}; sleep 5"
            with unittest.mock.patch(
                "wave_delivery.verify_run.os.fdopen", side_effect=fake_fdopen
            ):
                result = execute(
                    [command], cwd=tmp, timeout_seconds=1, log_path=log_path
                )

            entry = result["results"][0]
            self.assertEqual(entry["status"], "failed")
            self.assertIsNone(entry["exitCode"])
            self.assertTrue(entry.get("timedOut"))
            self.assertIn("error draining output during kill", entry["tail"])

            child_pid = int(pidfile.read_text(encoding="utf-8").strip())
            deadline = time.monotonic() + 2.0
            while not _process_is_dead(child_pid) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(
                _process_is_dead(child_pid),
                f"pid {child_pid} is still running after a log-write OSError "
                "during the kill drain -- SIGKILL escalation was skipped",
            )


class ExecutePostEofWaitTimeoutTests(unittest.TestCase):
    def test_deadline_enforced_when_pipe_closes_but_process_keeps_running(self) -> None:
        """Regression test for the review finding: once the read loop saw
        EOF (every write-end holder of the pipe gone), the old code called
        `process.wait()` with no timeout at all -- fine for the common
        case where the process that closed the pipe has also exited, but
        wrong when a process closes its own stdout/stderr via `exec` and
        then keeps running: `exec 1>/dev/null 2>/dev/null; sleep 15`
        returns EOF on the pipe almost instantly while `sh` (now running
        `sleep 15` in the same process image) keeps running for the full
        15s. The old unbounded `wait()` blocked for all 15s and recorded
        "passed". The fix bounds that wait by the remaining deadline and
        falls into the timeout+group-kill path on expiry.
        """
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "verify.log"
            command = "exec 1>/dev/null 2>/dev/null; sleep 15"
            started = time.monotonic()
            result = execute([command], cwd=tmp, timeout_seconds=1, log_path=log_path)
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 5)
            entry = result["results"][0]
            self.assertEqual(entry["status"], "failed")
            self.assertIsNone(entry["exitCode"])
            self.assertTrue(entry.get("timedOut"))


class ExecuteTailBoundTests(unittest.TestCase):
    def test_tail_bounded_to_last_4096_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "verify.log"
            result = execute(
                ["printf '%0.sa' $(seq 1 10000)"],
                cwd=tmp,
                timeout_seconds=5,
                log_path=log_path,
            )
            entry = result["results"][0]
            self.assertEqual(entry["status"], "passed")
            tail_bytes = entry["tail"].encode("utf-8")
            self.assertEqual(len(tail_bytes), 4096)
            self.assertEqual(tail_bytes, b"a" * 4096)
            # The tail is bounded, but the hash (and capture) still cover
            # all 10000 bytes -- truncation to 4096 is display-only.
            self.assertEqual(
                entry["outputSha256"], hashlib.sha256(b"a" * 10000).hexdigest()
            )
            self.assertNotIn("truncated", entry)


class ExecutePopenFailureTests(unittest.TestCase):
    def test_popen_oserror_recorded_as_schema_valid_failed_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing_cwd = Path(tmp) / "does-not-exist"
            log_path = Path(tmp) / "verify.log"
            result = execute(
                ["echo hi"], cwd=missing_cwd, timeout_seconds=5, log_path=log_path
            )
            entry = result["results"][0]
            # Full executed-failure shape (T2's `_validate_result_entry`,
            # wave_delivery/schema.py, spec AC-1/AC-9): a Popen OSError
            # never produces a real exit code (the process never
            # started), but the executed-entry contract requires an int
            # `exitCode` whenever `timedOut` is not true -- `exitCode:
            # null` is reserved for the timeout shape only. 127 is the
            # shell "command not found" convention, used here as an
            # explicitly synthetic marker for "never executed", not an
            # observed value.
            self.assertEqual(
                set(entry.keys()),
                {"command", "status", "exitCode", "durationMs", "outputSha256", "tail"},
            )
            self.assertEqual(entry["command"], "echo hi")
            self.assertEqual(entry["status"], "failed")
            self.assertEqual(entry["exitCode"], 127)
            self.assertIsInstance(entry["durationMs"], int)
            self.assertGreaterEqual(entry["durationMs"], 0)
            self.assertEqual(entry["outputSha256"], hashlib.sha256(b"").hexdigest())
            self.assertIn("does-not-exist", entry["tail"])
            # "No fabricated fields" doctrine: timedOut/truncated are
            # optional and must be absent when they don't apply.
            self.assertNotIn("timedOut", entry)
            self.assertNotIn("truncated", entry)

    def test_popen_oserror_shape_pins_t2_validator_contract_fields(self) -> None:
        """Cross-check against T2's `_validate_result_entry` contract
        (wave_delivery/schema.py, spec AC-1/AC-9). T2's schema changes
        live on another task's branch (T2-config-schema) and are not
        importable from this worktree, so this pins the exact rules that
        validator enforces for a non-skipped, non-timedOut entry rather
        than importing it directly:
          - required keys: command, status, exitCode, durationMs,
            outputSha256, tail (`_EXECUTED_ENTRY_REQUIRED_KEYS`)
          - status must be "passed" or "failed" (never bare "skipped"
            fields mixed in)
          - exitCode must be a real int (not None, not bool) whenever
            timedOut is not true (`_validate_result_entry`)
          - durationMs must be a non-negative int
          - outputSha256 and tail must be strings
        """
        with tempfile.TemporaryDirectory() as tmp:
            missing_cwd = Path(tmp) / "does-not-exist"
            log_path = Path(tmp) / "verify.log"
            result = execute(
                ["echo hi"], cwd=missing_cwd, timeout_seconds=5, log_path=log_path
            )
            entry = result["results"][0]
            required = {
                "command", "status", "exitCode", "durationMs", "outputSha256", "tail",
            }
            self.assertEqual(set(entry) - {"timedOut", "truncated"}, required)
            self.assertIn(entry["status"], {"passed", "failed"})
            self.assertIsInstance(entry["exitCode"], int)
            self.assertNotIsInstance(entry["exitCode"], bool)
            self.assertIsInstance(entry["durationMs"], int)
            self.assertNotIsInstance(entry["durationMs"], bool)
            self.assertGreaterEqual(entry["durationMs"], 0)
            self.assertIsInstance(entry["outputSha256"], str)
            self.assertIsInstance(entry["tail"], str)


class ExecuteLogFilePermissionTests(unittest.TestCase):
    def test_log_file_created_at_0600_when_not_pre_reserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Deliberately not created via reserve_numbered_path, to cover
            # the caller path that used to fall through to a plain
            # open(..., "wb") at the umask-derived default mode.
            log_path = Path(tmp) / "verify.log"
            execute(["echo hi"], cwd=tmp, timeout_seconds=5, log_path=log_path)
            mode = log_path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)


class ExecuteTruncationTests(unittest.TestCase):
    def test_truncation_marker_recorded_when_output_exceeds_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "verify.log"
            with unittest.mock.patch("wave_delivery.verify_run._CAPTURE_CAP_BYTES", 16):
                result = execute(
                    ["printf '%0.saaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'"],
                    cwd=tmp,
                    timeout_seconds=5,
                    log_path=log_path,
                )
            entry = result["results"][0]
            self.assertEqual(entry["status"], "passed")
            self.assertTrue(entry["truncated"])
            # The hash and tail only ever cover the capped bytes.
            self.assertEqual(len(entry["tail"].encode("utf-8")), 16)

    def test_no_truncation_field_when_under_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "verify.log"
            result = execute(["echo small"], cwd=tmp, timeout_seconds=5, log_path=log_path)
            self.assertNotIn("truncated", result["results"][0])


class ReserveNumberedPathTests(unittest.TestCase):
    def test_sequential_reservation_and_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            first = reserve_numbered_path(directory, "packet-worker-", ".md")
            second = reserve_numbered_path(directory, "packet-worker-", ".md")
            self.assertEqual(first.name, "packet-worker-1.md")
            self.assertEqual(second.name, "packet-worker-2.md")
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())
            mode = first.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_skips_over_a_preexisting_numbered_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "verify-t-1.log").touch()
            reserved = reserve_numbered_path(directory, "verify-t-", ".log")
            self.assertEqual(reserved.name, "verify-t-2.log")

    def test_distinct_prefixes_number_independently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            a = reserve_numbered_path(directory, "verify-a-", ".log")
            b = reserve_numbered_path(directory, "verify-b-", ".log")
            self.assertEqual(a.name, "verify-a-1.log")
            self.assertEqual(b.name, "verify-b-1.log")


if __name__ == "__main__":
    unittest.main()
