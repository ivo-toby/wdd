"""Tests for `wave_delivery.verify_run` — the machine-verification executor
(epic: machine-executed verification and dispatch observability, task
T1-executor) — and for `wddctl verify record --task T --run`, the CLI/review
wiring on top of it (task T3-task-verify). Covers `execute()`'s
observed-evidence contract (spec AC-1, AC-4, AC-5, AC-9) and
`reserve_numbered_path()`'s O_EXCL reservation, then the T3 surface: the
single admission-snapshot read, dirty/absent-worktree and empty-command
refusals, the `--run` vs `--status`/`--command` post-parse conflict, the
recorded evidence shape (execution/telemetry/logSha256/results), AC-10's
gate-inertness, and a digest-stability pin left by the merged-foundations
review.

Local helpers only (no cross-file imports between test modules, per the
phase-6a/6b test conventions -- see tests/test_execution_surfaces.py).
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path
from typing import Any

import wave_delivery.cli as cli_module
from wave_delivery.cli import main
from wave_delivery.config import (
    default_config,
    effective_config_digest,
    load_config,
    load_layers,
    save_config,
)
from wave_delivery.engine import task_gate
from wave_delivery.errors import ValidationError
from wave_delivery.store import StateStore
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


# ---------------------------------------------------------------------------
# `wddctl verify record --task T --run` (task T3-task-verify).
#
# Local CLI/git test helpers, deliberately duplicated rather than imported
# from tests/test_wave_delivery.py or tests/test_execution_surfaces.py (see
# module docstring: no cross-file imports between test modules). Mirrors
# test_execution_surfaces.py's `_bootstrap_ready_scope`/`_mark_legacy`
# idiom: a real `wddctl init`-produced config.json (so `--run` has a real
# admission snapshot to resolve commands and timeoutSeconds from) with the
# intake ladder short-circuited via `intake.legacy` -- these tests exercise
# --run's cli.py/review.py wiring, not the intake ladder.
# ---------------------------------------------------------------------------


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def _git_repo(tmp: str) -> Path:
    root = Path(tmp) / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(
        root, "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false",
        "commit", "-qm", "seed",
    )
    return root


def _cli(state: str, *argv: str) -> tuple[int, str]:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = main(["--state", state, *argv])
    return code, stdout.getvalue()


def _cli_full(state: str, *argv: str) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(["--state", state, *argv])
    return code, stdout.getvalue(), stderr.getvalue()


def _plan(scope_overrides: dict | None = None) -> dict:
    plan = {
        "schemaVersion": 1,
        "kind": "wdd_plan",
        "scope": {
            "id": "SCOPE-x",
            "baseRef": "wdd/scope-x",
            "maxConcurrent": 3,
            "reviewPolicy": "risk_based",
            "reconcileEveryNMerges": 3,
        },
        "tasks": [
            {
                "id": "T1",
                "title": "T1",
                "specPath": "tasks/T1.md",
                "risk": "normal",
                "dependsOn": [],
                "conflictDomains": ["src/t1/**"],
            }
        ],
    }
    if scope_overrides:
        plan["scope"].update(scope_overrides)
    return plan


def _mark_legacy(state: str) -> None:
    store = StateStore(Path(state))
    current = store.read()
    current["intake"] = {"legacy": True}
    store.write(current)


def _bootstrap_scope(
    tmp: str, *, commands: list[str] | None = None, timeout_seconds: int | None = None
) -> tuple[Path, str]:
    """A real config.json (so `--run` resolves commands/timeoutSeconds from
    a genuine admission snapshot) with the intake ladder short-circuited,
    scope `SCOPE-x` applied with one admissible task `T1`."""
    root = _git_repo(tmp)
    wdd = root / ".wdd"
    state = str(wdd / "state.json")
    assert _cli(state, "init", "--repo", str(root))[0] == 0
    assert _cli(state, "config", "set", "merge.surface", "local")[0] == 0
    assert (
        _cli(
            state, "config", "set", "models",
            '{"planning": null, "implementation": {"default": null, "highRisk": null}, '
            '"review": null}',
        )[0]
        == 0
    )
    if commands is not None:
        assert _cli(state, "config", "set", "verification.commands", json.dumps(commands))[0] == 0
    if timeout_seconds is not None:
        assert (
            _cli(state, "config", "set", "verification.timeoutSeconds", str(timeout_seconds))[0]
            == 0
        )
    assert _cli(state, "constitution", "ratify", "--by", "tester")[0] == 0
    _mark_legacy(state)
    (wdd / "tasks" / "T1.md").write_text("# T1\n\nBrief.\n", encoding="utf-8")
    plan_path = root / "plan.json"
    plan_path.write_text(json.dumps(_plan()), encoding="utf-8")
    code, out = _cli(state, "plan", "apply", "--plan", str(plan_path), "--repo", str(root))
    assert code == 0, out
    return root, state


def _start_commit_submit(state: str, root: Path, task_id: str = "T1") -> Path:
    code, out = _cli(state, "start", "--task", task_id, "--repo", str(root))
    assert code == 0, out
    worktree = Path(json.loads(out)["worktree"])
    (worktree / "change.txt").write_text("work\n", encoding="utf-8")
    _git(worktree, "add", "-A")
    _git(
        worktree, "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false",
        "commit", "-qm", "do work",
    )
    code, out = _cli(state, "submit", "--task", task_id, "--repo", str(root))
    assert code == 0, out
    return worktree


class VerifyRunFlagConflictTests(unittest.TestCase):
    """AC-3: `--run` refuses post-parse alongside `--status`/`--command`,
    with a message naming the conflict -- checked before the CLI ever
    touches the store, so no bootstrapped scope is needed here."""

    def test_run_with_status_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = str(Path(tmp) / "state.json")
            code, _out, err = _cli_full(
                state, "verify", "record", "--task", "T1", "--run", "--status", "passed"
            )
            self.assertNotEqual(code, 0)
            self.assertIn("--run", err)
            self.assertIn("--status", err)

    def test_run_with_command_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = str(Path(tmp) / "state.json")
            code, _out, err = _cli_full(
                state, "verify", "record", "--task", "T1", "--run", "--command", "pytest"
            )
            self.assertNotEqual(code, 0)
            self.assertIn("--run", err)
            self.assertIn("--command", err)

    def test_neither_status_nor_run_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = str(Path(tmp) / "state.json")
            code, _out, err = _cli_full(state, "verify", "record", "--task", "T1")
            self.assertNotEqual(code, 0)
            self.assertIn("--status", err)
            self.assertIn("--run", err)


class VerifyRunEmptyCommandListTests(unittest.TestCase):
    """AC-4: an empty effective command list refuses `--run` outright, with
    no command ever executed and no dispatch log left behind."""

    def test_empty_verification_commands_refuses_before_executing_anything(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _bootstrap_scope(tmp, commands=[])
            _start_commit_submit(state, root)
            code, _out, err = _cli_full(state, "verify", "record", "--task", "T1", "--run", "--repo", str(root))
            self.assertNotEqual(code, 0)
            self.assertIn("verification.commands is empty", err)
            dispatch_dir = root / ".wdd" / "dispatch"
            self.assertFalse(list(dispatch_dir.glob("verify-*.log")))
            self.assertIsNone(StateStore(Path(state)).read()["tasks"]["T1"].get("verification"))


class VerifyRunAbsentWorktreeTests(unittest.TestCase):
    """AC-2: `--run` refuses when the task's worktree is absent, naming
    start's reattach remedy -- checked before any dirtiness check or
    execution, so a never-started task (still `todo`) is enough."""

    def test_absent_worktree_refuses_naming_the_reattach_remedy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _bootstrap_scope(tmp, commands=["true"])
            code, _out, err = _cli_full(state, "verify", "record", "--task", "T1", "--run", "--repo", str(root))
            self.assertNotEqual(code, 0)
            self.assertIn("worktree is missing", err)
            self.assertIn("wddctl start --task T1 --repo .", err)
            self.assertEqual(StateStore(Path(state)).read()["tasks"]["T1"]["status"], "todo")


class VerifyRunDirtyWorktreeTests(unittest.TestCase):
    """AC-2: `--run` refuses on an uncommitted worktree, naming the files --
    evidence binds committed bytes only."""

    def test_dirty_worktree_refuses_naming_the_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _bootstrap_scope(tmp, commands=["true"])
            worktree = _start_commit_submit(state, root)
            (worktree / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")
            code, _out, err = _cli_full(state, "verify", "record", "--task", "T1", "--run", "--repo", str(root))
            self.assertNotEqual(code, 0)
            self.assertIn("uncommitted changes", err)
            self.assertIn("uncommitted.txt", err)
            dispatch_dir = root / ".wdd" / "dispatch"
            self.assertFalse(list(dispatch_dir.glob("verify-*.log")))
            self.assertIsNone(StateStore(Path(state)).read()["tasks"]["T1"].get("verification"))


class VerifyRunSingleLoadLayersCallTests(unittest.TestCase):
    """Pin: `--run` resolves commands from the admission snapshot the
    chokepoint already read (`_governed_config`), never a second
    `load_layers` call of its own."""

    def test_run_calls_load_layers_exactly_once_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _bootstrap_scope(tmp, commands=["true"])
            _start_commit_submit(state, root)
            calls: list[int] = []
            real_load_layers = cli_module.load_layers

            def counting(*args: Any, **kwargs: Any) -> Any:
                calls.append(1)
                return real_load_layers(*args, **kwargs)

            with unittest.mock.patch.object(cli_module, "load_layers", side_effect=counting):
                code, out = _cli(state, "verify", "record", "--task", "T1", "--run", "--repo", str(root))
            self.assertEqual(code, 0, out)
            self.assertEqual(len(calls), 1)


class VerifyRunHappyPathTests(unittest.TestCase):
    """AC-1/AC-7/AC-9: a successful `--run` records execution: "wddctl",
    per-command results from `verify_run.execute`, `logSha256` over the
    actual log bytes, and an auto-filled `telemetry.durationMs`."""

    def test_single_passing_command_is_recorded_as_machine_observed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _bootstrap_scope(tmp, commands=["true"])
            _start_commit_submit(state, root)
            code, out = _cli(state, "verify", "record", "--task", "T1", "--run", "--repo", str(root))
            self.assertEqual(code, 0, out)
            verification = StateStore(Path(state)).read()["tasks"]["T1"]["verification"]
            self.assertEqual(verification["status"], "passed")
            self.assertEqual(verification["execution"], "wddctl")
            self.assertIsNone(verification["command"])
            self.assertEqual(len(verification["results"]), 1)
            entry = verification["results"][0]
            self.assertEqual(entry["command"], "true")
            self.assertEqual(entry["status"], "passed")
            self.assertEqual(entry["exitCode"], 0)
            self.assertIsInstance(verification["telemetry"]["durationMs"], int)
            self.assertGreaterEqual(verification["telemetry"]["durationMs"], 0)
            dispatch_dir = root / ".wdd" / "dispatch"
            logs = list(dispatch_dir.glob("verify-T1-*.log"))
            self.assertEqual(len(logs), 1)
            self.assertEqual(
                verification["logSha256"], hashlib.sha256(logs[0].read_bytes()).hexdigest()
            )
            self.assertEqual(
                StateStore(Path(state)).read()["tasks"]["T1"]["status"], "merge_ready"
            )

    def test_failing_command_is_recorded_failed_with_its_real_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _bootstrap_scope(tmp, commands=["false"])
            _start_commit_submit(state, root)
            code, out = _cli(state, "verify", "record", "--task", "T1", "--run", "--repo", str(root))
            self.assertEqual(code, 0, out)
            verification = StateStore(Path(state)).read()["tasks"]["T1"]["verification"]
            self.assertEqual(verification["status"], "failed")
            self.assertEqual(verification["execution"], "wddctl")
            self.assertEqual(verification["results"][0]["exitCode"], 1)
            self.assertEqual(
                StateStore(Path(state)).read()["tasks"]["T1"]["status"], "in_progress"
            )


class VerifyRunEndToEndOracleTests(unittest.TestCase):
    """Independent oracle (brief): drives a scratch repo through the real
    epic/intake ladder (not the `intake.legacy` shortcut the other tests in
    this section use) to `start` -> commit-in-worktree -> `verify record
    --run`, first against a command that really fails (`false`) then, after
    reconfiguring, one that really passes (`true`). The expected exitCodes
    (1, then 0) come from what `sh` itself returns for those commands, not
    from anything this test asserts about the implementation's internals.
    """

    def test_run_records_exit_codes_sh_actually_returned_across_a_config_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            state = str(wdd / "state.json")
            self.assertEqual(_cli(state, "init", "--repo", str(root))[0], 0)
            self.assertEqual(_cli(state, "config", "set", "merge.surface", "local")[0], 0)
            self.assertEqual(
                _cli(
                    state, "config", "set", "models",
                    '{"planning": null, "implementation": {"default": null, "highRisk": null}, '
                    '"review": null}',
                )[0],
                0,
            )
            self.assertEqual(
                _cli(state, "config", "set", "verification.commands", '["false"]')[0], 0
            )
            self.assertEqual(_cli(state, "constitution", "ratify", "--by", "tester")[0], 0)

            self.assertEqual(_cli(state, "epic", "new", "--slug", "demo")[0], 0)
            epic_dir = wdd / "epics" / "demo"
            self.assertEqual(
                _cli(state, "intake", "configure", "--use-defaults", "--by", "tester")[0], 0
            )
            (epic_dir / "spec.md").write_text(
                "# Spec\n\n## Goal\n\nShip it.\n\n## In scope\n\n- x\n\n"
                "## Out of scope\n\n- y\n\n## Acceptance criteria\n\n- [ ] AC-1: it works\n",
                encoding="utf-8",
            )
            self.assertEqual(_cli(state, "intake", "spec", "--approved-by", "tester")[0], 0)
            self.assertEqual(
                _cli(
                    state, "intake", "research", "--skip", "--by", "tester",
                    "--reason", "no external contracts",
                )[0],
                0,
            )
            (epic_dir / "design.md").write_text(
                "# Design\n\n## Components\n\n- core\n\n## Interfaces\n\n"
                "- core: consumes nothing, produces lib\n\n"
                "## Integration surfaces\n\n- `src/core.py` — owned by: core task\n\n"
                "## Epic deliverable\n\nThe lib imports.\n",
                encoding="utf-8",
            )
            self.assertEqual(
                _cli(
                    state, "intake", "design", "--approved-by", "tester",
                    "--deliverable-command", "true",
                )[0],
                0,
            )

            plan = _plan({"id": "SCOPE-demo", "baseRef": "wdd/demo"})
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            (epic_dir / "tasks").mkdir(exist_ok=True)
            for task in plan["tasks"]:
                (epic_dir / task.get("specPath", f"tasks/{task['id']}.md")).write_text(
                    f"# {task['id']}\n\nBrief.\n", encoding="utf-8"
                )
            code, out = _cli(
                state, "plan", "apply", "--plan", str(plan_path), "--repo", str(root),
                "--approved-by", "tester",
            )
            self.assertEqual(code, 0, out)

            code, out = _cli(state, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            worktree = Path(json.loads(out)["worktree"])
            (worktree / "change.txt").write_text("work\n", encoding="utf-8")
            _git(worktree, "add", "-A")
            _git(
                worktree, "-c", "user.email=t@t", "-c", "user.name=t",
                "-c", "commit.gpgsign=false", "commit", "-qm", "do work",
            )
            self.assertEqual(_cli(state, "submit", "--task", "T1", "--repo", str(root))[0], 0)

            code, out = _cli(state, "verify", "record", "--task", "T1", "--run", "--repo", str(root))
            self.assertEqual(code, 0, out)
            failed_verification = StateStore(Path(state)).read()["tasks"]["T1"]["verification"]
            self.assertEqual(failed_verification["status"], "failed")
            self.assertEqual(failed_verification["results"][0]["command"], "false")
            self.assertEqual(failed_verification["results"][0]["exitCode"], 1)

            self.assertEqual(
                _cli(state, "config", "set", "verification.commands", '["true"]')[0], 0
            )
            self.assertEqual(_cli(state, "constitution", "amend", "--by", "tester")[0], 0)
            self.assertEqual(
                _cli(state, "intake", "configure", "--use-defaults", "--by", "tester")[0], 0
            )
            # Re-approving the configure rung cascades and clears the plan's
            # own composite approval (spec: a rung re-approval clears every
            # downstream rung) -- re-stamp it before any governed verb.
            code, out = _cli(
                state, "plan", "apply", "--plan", str(plan_path), "--repo", str(root),
                "--approved-by", "tester",
            )
            self.assertEqual(code, 0, out)

            code, out = _cli(state, "verify", "record", "--task", "T1", "--run", "--repo", str(root))
            self.assertEqual(code, 0, out)
            passed_verification = StateStore(Path(state)).read()["tasks"]["T1"]["verification"]
            self.assertEqual(passed_verification["status"], "passed")
            self.assertEqual(passed_verification["results"][0]["command"], "true")
            self.assertEqual(passed_verification["results"][0]["exitCode"], 0)


class VerifyRunTelemetryGateInertnessTests(unittest.TestCase):
    """AC-10: a `telemetry` object on a verification record is stored but
    provably ignored by the merge gate -- flipping it leaves `task_gate`'s
    outcome unchanged."""

    def test_flipping_recorded_telemetry_does_not_change_the_merge_gate_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _bootstrap_scope(tmp, commands=["true"])
            _start_commit_submit(state, root)
            code, out = _cli(state, "verify", "record", "--task", "T1", "--run", "--repo", str(root))
            self.assertEqual(code, 0, out)
            recorded = StateStore(Path(state)).read()
            gate_before = task_gate(recorded, recorded["tasks"]["T1"])

            flipped = copy.deepcopy(recorded)
            flipped["tasks"]["T1"]["verification"]["telemetry"] = {
                "model": "a-completely-different-model",
                "durationMs": 999999999,
                "tokens": 123456,
            }
            gate_after = task_gate(flipped, flipped["tasks"]["T1"])
            self.assertEqual(gate_before, gate_after)

            stripped = copy.deepcopy(recorded)
            del stripped["tasks"]["T1"]["verification"]["telemetry"]
            gate_without = task_gate(stripped, stripped["tasks"]["T1"])
            self.assertEqual(gate_before, gate_without)


class ConfigDigestBackfillStabilityTests(unittest.TestCase):
    """Reconciliation addendum: a backfill-only config write -- setting
    `verification.timeoutSeconds` explicitly to the exact value hydration
    already supplies for its absence -- must leave the effective config
    digest unchanged. Exercises config.py's existing hydration (T2), not
    anything this task implements; pinned here per the merged-foundations
    review addenda."""

    def test_backfilling_the_hydrated_default_leaves_the_effective_digest_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            wdd.mkdir()
            legacy_config = default_config()
            del legacy_config["verification"]["timeoutSeconds"]
            save_config(wdd, legacy_config)
            before = effective_config_digest(load_layers(wdd, None)["effective"])

            raw = load_config(wdd)
            self.assertNotIn("timeoutSeconds", raw["verification"])
            raw["verification"]["timeoutSeconds"] = default_config()["verification"]["timeoutSeconds"]
            save_config(wdd, raw)

            after = effective_config_digest(load_layers(wdd, None)["effective"])
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
