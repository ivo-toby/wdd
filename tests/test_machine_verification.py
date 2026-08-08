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
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

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
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)


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
