"""Task 7 (2026-08-03 epic-scoped-state plan): CLI version identity and the
semver release automation (spec Sec3).

Pure-logic module + CAS loop, unit-tested with a fake `run_git` -- no
network, no real tags created against this repository's own git. Local
helpers only (no cross-file imports between test modules, per the
phase-6a/6b/7 test conventions).
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_release_module():
    """Load scripts/release.py by path (scripts/ has no package __init__)."""
    spec = importlib.util.spec_from_file_location("_wdd_release", ROOT / "scripts" / "release.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_installer_module():
    spec = importlib.util.spec_from_file_location(
        "_wdd_installer", ROOT / "scripts" / "install_wave_delivery.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


release = _load_release_module()


class _FakeGit:
    """Scripted `git` responses for CAS-loop tests.

    `local_tags` mirrors what a prior `git tag -f` (or an actual fetch of
    a pre-existing remote tag) has made visible locally -- used by
    `describe`. `remote_tags` is the authoritative server-side state,
    mutated only by a successful `push` -- used by `ls-remote`, matching
    `_tag_commit`'s deliberate choice to query the remote directly rather
    than trust local tag refs a rejected push may have left behind.
    """

    def __init__(
        self,
        *,
        origin_main: str,
        last_tag: str | None,
        remote_tags: dict[str, str] | None = None,
        push_should_fail_times: int = 0,
        log_messages: list[str] | None = None,
    ) -> None:
        self.origin_main = origin_main
        self.local_head = origin_main
        self.last_tag = last_tag
        self.local_tags = dict(remote_tags or {})
        self.remote_tags = dict(remote_tags or {})
        self.push_should_fail_times = push_should_fail_times
        self.log_messages = log_messages if log_messages is not None else ["feat: add thing"]
        self.push_calls = 0
        self.commit_counter = 0
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(list(args))
        cmd = args[0]
        if cmd in ("fetch", "add"):
            return ""
        if cmd == "switch":
            self.local_head = self.origin_main
            return ""
        if cmd == "rev-parse":
            target = args[-1]
            if target == "origin/main":
                return self.origin_main
            if target == "HEAD":
                return self.local_head
            raise release.GitError(f"unhandled rev-parse target: {target}")
        if cmd == "describe":
            if "--exact-match" in args:
                for name, sha in self.local_tags.items():
                    if sha == self.origin_main:
                        return name
                raise release.GitError("no tag points at HEAD")
            if self.last_tag is None:
                raise release.GitError("no tags found")
            return self.last_tag
        if cmd == "log":
            return release._RECORD_SEP.join(self.log_messages + [""])
        if cmd == "commit":
            self.commit_counter += 1
            self.local_head = f"release-sha-{self.commit_counter}"
            return ""
        if cmd == "tag":
            # ["tag", "-f", name, sha]
            self.local_tags[args[2]] = args[3]
            return ""
        if cmd == "ls-remote":
            name = args[-1].rsplit("/", 1)[-1]
            sha = self.remote_tags.get(name)
            return f"{sha}\trefs/tags/{name}" if sha else ""
        if cmd == "push":
            self.push_calls += 1
            tag_name = args[-1]
            if self.push_should_fail_times > 0:
                self.push_should_fail_times -= 1
                # Simulate a competing push landing between our fetch and ours.
                self.origin_main = f"competing-sha-{self.push_calls}"
                raise release.GitError("! [rejected] main -> main (fetch first)")
            self.remote_tags[tag_name] = self.local_tags[tag_name]
            self.origin_main = self.local_head
            return ""
        raise release.GitError(f"unhandled git command: {args}")


class ParseCommitTest(unittest.TestCase):
    def test_feat_is_minor(self) -> None:
        self.assertEqual(release.parse_commit("feat: add widgets"), "minor")

    def test_fix_is_patch(self) -> None:
        self.assertEqual(release.parse_commit("fix: correct off-by-one"), "patch")

    def test_chore_is_patch(self) -> None:
        self.assertEqual(release.parse_commit("chore: tidy scripts"), "patch")

    def test_scoped_feat_is_minor(self) -> None:
        self.assertEqual(release.parse_commit("feat(api): add users endpoint"), "minor")

    def test_bang_on_any_type_is_major(self) -> None:
        self.assertEqual(release.parse_commit("feat!: drop legacy field"), "major")
        self.assertEqual(release.parse_commit("fix(core)!: change return type"), "major")

    def test_footer_only_breaking_change_is_major(self) -> None:
        message = (
            "fix: adjust retry timing\n\n"
            "Longer body describing the fix.\n\n"
            "BREAKING CHANGE: retry() no longer accepts a float timeout\n"
        )
        self.assertEqual(release.parse_commit(message), "major")

    def test_footer_hyphenated_breaking_change_is_major(self) -> None:
        message = "fix: x\n\nBREAKING-CHANGE: y\n"
        self.assertEqual(release.parse_commit(message), "major")

    def test_release_bump_trailer_overrides_header(self) -> None:
        message = "fix: minor cleanup\n\nRelease-Bump: major\n"
        self.assertEqual(release.parse_commit(message), "major")

    def test_release_bump_trailer_overrides_even_upward(self) -> None:
        message = "feat!: big change\n\nRelease-Bump: minor\n"
        self.assertEqual(release.parse_commit(message), "minor")

    def test_squash_title_classified_by_its_own_header(self) -> None:
        message = "feat(api): add users endpoint (#42)\n\n* feat: add route\n* test: cover route\n"
        self.assertEqual(release.parse_commit(message), "minor")

    def test_bootstrap_no_tag(self) -> None:
        self.assertEqual(release.next_version(None, "major"), "0.1.0")
        self.assertEqual(release.next_version(None, "minor"), "0.1.0")
        self.assertEqual(release.next_version(None, "patch"), "0.1.0")

    def test_malformed_message_is_patch(self) -> None:
        self.assertEqual(release.parse_commit("fixed a thing"), "patch")
        self.assertEqual(release.parse_commit(""), "patch")
        self.assertEqual(release.parse_commit("   "), "patch")


class ComputeBumpTest(unittest.TestCase):
    def test_aggregates_to_highest_bump(self) -> None:
        messages = ["chore: tidy", "feat: add x", "fix: y"]
        self.assertEqual(release.compute_bump(messages), "minor")

    def test_merge_commit_subject_excluded_constituents_still_classified(self) -> None:
        messages = [
            "Merge pull request #7 from owner/feature-branch",
            "feat: add real feature",
        ]
        self.assertEqual(release.compute_bump(messages), "minor")

    def test_merge_branch_subject_excluded(self) -> None:
        messages = ["Merge branch 'develop' into main", "fix: small tweak"]
        self.assertEqual(release.compute_bump(messages), "patch")

    def test_empty_list_is_patch(self) -> None:
        self.assertEqual(release.compute_bump([]), "patch")

    def test_only_merge_commits_is_patch(self) -> None:
        self.assertEqual(release.compute_bump(["Merge pull request #1 from a/b"]), "patch")


class NextVersionTest(unittest.TestCase):
    def test_patch_bump(self) -> None:
        self.assertEqual(release.next_version("v1.2.3", "patch"), "1.2.4")

    def test_minor_bump_resets_patch(self) -> None:
        self.assertEqual(release.next_version("v1.2.3", "minor"), "1.3.0")

    def test_major_bump_resets_minor_and_patch(self) -> None:
        self.assertEqual(release.next_version("v1.2.3", "major"), "2.0.0")

    def test_no_prior_tag_bootstraps_regardless_of_bump(self) -> None:
        for bump in ("major", "minor", "patch"):
            self.assertEqual(release.next_version(None, bump), "0.1.0")

    def test_malformed_tag_raises(self) -> None:
        with self.assertRaises(ValueError):
            release.next_version("not-a-tag", "patch")


class DecideActionTest(unittest.TestCase):
    def test_no_existing_tag_is_push(self) -> None:
        self.assertEqual(release.decide_action(None, "sha-target"), "push")

    def test_tag_at_intended_commit_is_idempotent(self) -> None:
        self.assertEqual(release.decide_action("sha-target", "sha-target"), "idempotent")

    def test_tag_at_other_commit_is_foreign(self) -> None:
        self.assertEqual(release.decide_action("sha-other", "sha-target"), "foreign")


class ReleaseCASLoopTest(unittest.TestCase):
    """main()'s triage, driven end-to-end through a fake run_git."""

    def _run(self, fake: _FakeGit, **kwargs):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = release.main(
                run_git=fake,
                write_version=lambda version: None,
                sleep=lambda seconds: None,
                **kwargs,
            )
        return code, stdout.getvalue(), stderr.getvalue()

    def test_idempotent_success_when_intended_tag_already_at_head(self) -> None:
        fake = _FakeGit(
            origin_main="sha-A",
            last_tag="v1.2.0",
            remote_tags={"v1.2.0": "sha-A"},
        )
        code, out, _ = self._run(fake)
        self.assertEqual(code, 0)
        self.assertIn("idempotent", out)
        self.assertFalse(any(call[0] == "push" for call in fake.calls))

    def test_foreign_tag_fails_closed_and_names_both_commits(self) -> None:
        fake = _FakeGit(
            origin_main="sha-B",
            last_tag="v1.0.0",
            remote_tags={"v1.1.0": "sha-foreign"},
            log_messages=["feat: add x"],
        )
        code, _, err = self._run(fake)
        self.assertEqual(code, 1)
        self.assertIn("sha-foreign", err)
        self.assertIn("sha-B", err)
        self.assertFalse(any(call[0] == "push" for call in fake.calls))

    def test_moved_main_triggers_recompute_and_retry_succeeds(self) -> None:
        fake = _FakeGit(
            origin_main="sha-A0",
            last_tag="v1.0.0",
            remote_tags={},
            push_should_fail_times=1,
            log_messages=["feat: add x"],
        )
        code, out, _ = self._run(fake)
        self.assertEqual(code, 0)
        self.assertIn("pushed", out)
        self.assertEqual(fake.push_calls, 2)
        # The second attempt built and pushed a fresh release commit, not
        # the one abandoned when the first attempt's push was rejected.
        self.assertEqual(fake.remote_tags["v1.1.0"], "release-sha-2")

    def test_exhausts_retries_and_fails_closed(self) -> None:
        fake = _FakeGit(
            origin_main="sha-A0",
            last_tag="v1.0.0",
            remote_tags={},
            push_should_fail_times=5,
            log_messages=["feat: add x"],
        )
        code, _, err = self._run(fake, max_attempts=3)
        self.assertEqual(code, 1)
        self.assertIn("exhausted", err)
        self.assertEqual(fake.push_calls, 3)


class VersionOutputShapeTest(unittest.TestCase):
    def test_wddctl_version_reads_repo_root_version_file(self) -> None:
        sys.path.insert(0, str(ROOT))
        try:
            from wave_delivery.version import wddctl_version
        finally:
            sys.path.remove(str(ROOT))
        version = wddctl_version()
        self.assertRegex(version, r"^\d+\.\d+\.\d+(\+\S+)?$")
        self.assertEqual((ROOT / "VERSION").read_text(encoding="utf-8").strip(), version)

    def test_cli_version_flag_prints_and_exits_zero(self) -> None:
        sys.path.insert(0, str(ROOT))
        try:
            from wave_delivery.cli import build_parser
        finally:
            sys.path.remove(str(ROOT))
        parser = build_parser()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as raised:
                parser.parse_args(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertRegex(stdout.getvalue().strip(), r"^wddctl \d+\.\d+\.\d+(\+\S+)?( \(\S+\))?$")

    def test_doctor_report_includes_version_field(self) -> None:
        sys.path.insert(0, str(ROOT))
        try:
            from wave_delivery.doctor import inspect_capabilities
        finally:
            sys.path.remove(str(ROOT))
        payload = inspect_capabilities()
        self.assertIn("version", payload)
        self.assertRegex(payload["version"], r"^\d+\.\d+\.\d+(\+\S+)?$")


class InstallerCarriesVersionTest(unittest.TestCase):
    def test_install_copies_version_next_to_package(self) -> None:
        import tempfile

        installer = _load_installer_module()
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "wdd"
            installer.install(prefix)
            installed_version = prefix / "lib" / "VERSION"
            self.assertTrue(installed_version.exists())
            self.assertEqual(
                installed_version.read_text(encoding="utf-8"),
                (ROOT / "VERSION").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
