# Plan Quality & Derived Risk Implementation Plan (Phase 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make plan quality deterministic — task risk derived from config `riskRules` instead of agent guesswork, and a `wddctl plan lint` verb that flags the failure modes observed in the appie-mcp trial (full serialization, all-one-risk, per-file domain lists) — plus four hardening deferrals recorded by phase 1's final review.

**Architecture:** A new `wave_delivery/lint.py` owns lint checks as pure functions over a validated plan dict (reusing `engine.admission_schedule` for the serialization view and `domains.py` for overlap semantics). Risk derivation is an apply-time overlay in `plan.py` (`apply_risk_rules`, sibling of `apply_config_defaults`) so `validate_plan` stays file-local. Deferral fixes land where the phase-1 review pointed: `cli.py` (event-apply gate), `setup.py` (migrate lock, repair hint).

**Tech Stack:** Python 3 stdlib only. Tests: `unittest` via `python3 -m unittest`.

## Global Constraints

- No new runtime dependencies; stdlib only.
- Errors raise `ValidationError` / `IllegalTransition` from `wave_delivery/errors.py`.
- Risk levels stay exactly `{"normal", "high"}` (`schema.RISK_LEVELS`); derivation may only raise risk (`normal` → `high`), never lower an explicit `high`.
- Lint findings are warnings by default; `--strict` turns any finding into a nonzero exit. Lint NEVER mutates anything.
- Domain/pattern matching reuses `wave_delivery/domains.py` (`domains_overlap`, `literal_prefix`) — no second glob implementation.
- New tests go in a NEW file `tests/test_plan_quality.py` (unittest style; reuse the `_git_repo`-style helpers by defining local equivalents — do not import from `tests/test_setup_config.py`).
- Conventional commits; never skip hooks; do not push.
- Full suite green at the end of every task: `python3 -m unittest discover -s tests -q` (sandbox gpg workaround if needed: `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false`).
- Spec: `docs/superpowers/specs/2026-07-28-onboarding-and-workflow-redesign-design.md` §4; deferrals list: the `fix(governance)` commit `ad62d27` and this plan's Task 6/7.

---

### Task 1: `apply_risk_rules` — derived task risk

Spec §4: "`plan apply` computes task risk by matching `riskRules` patterns against each task's `conflictDomains`. Risk levels stay the existing two. A plan may override risk only upward."

**Files:**
- Modify: `wave_delivery/plan.py` (new function after `apply_config_defaults`, ~line 150)
- Modify: `wave_delivery/cli.py` (plan-apply handler, directly after the `apply_config_defaults` call)
- Test: `tests/test_plan_quality.py` (new file)

**Interfaces:**
- Produces: `apply_risk_rules(plan_dict: dict, config: dict) -> dict` — returns a new plan dict where each task's risk is `"high"` if the plan said high OR any `config["riskRules"]` entry with `"risk": "high"` overlaps (via `domains.domains_overlap`) any of the task's `conflictDomains`; `"normal"` otherwise. Rules with `"risk": "normal"` never lower anything (upward-only). Tasks with empty `conflictDomains` keep their plan risk.
- Consumes: `domains.domains_overlap(first, second)`; `config["riskRules"]` shape `[{"pattern": str, "risk": "normal"|"high"}]` (validated by `config.validate_config` since phase 1).
- CLI: applied only when `.wdd/config.json` exists, immediately after `apply_config_defaults`, before `apply_plan`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_plan_quality.py`:

```python
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from wave_delivery.config import default_config
from wave_delivery.plan import apply_risk_rules


def _plan(tasks: list[dict]) -> dict:
    return {
        "schemaVersion": 1,
        "kind": "wdd_plan",
        "scope": {
            "id": "SCOPE-q",
            "baseRef": None,
            "maxConcurrent": 3,
            "reviewPolicy": "risk_based",
            "reconcileEveryNMerges": 3,
        },
        "tasks": tasks,
    }


def _task(task_id: str, *, risk: str = "normal", depends_on: list | None = None,
          domains: list | None = None) -> dict:
    return {
        "id": task_id,
        "title": task_id,
        "specPath": f"tasks/{task_id}.md",
        "risk": risk,
        "dependsOn": depends_on or [],
        "conflictDomains": domains if domains is not None else [f"src/{task_id}/**"],
    }


class RiskRulesTest(unittest.TestCase):
    def _config(self, rules: list[dict]) -> dict:
        config = default_config()
        config["riskRules"] = rules
        return config

    def test_matching_high_rule_raises_risk(self) -> None:
        plan = _plan([_task("T1", domains=["src/auth/token.py"])])
        out = apply_risk_rules(plan, self._config([{"pattern": "src/auth/**", "risk": "high"}]))
        self.assertEqual(out["tasks"][0]["risk"], "high")

    def test_non_matching_rule_leaves_risk(self) -> None:
        plan = _plan([_task("T1", domains=["docs/readme.md"])])
        out = apply_risk_rules(plan, self._config([{"pattern": "src/auth/**", "risk": "high"}]))
        self.assertEqual(out["tasks"][0]["risk"], "normal")

    def test_explicit_high_never_lowered(self) -> None:
        plan = _plan([_task("T1", risk="high", domains=["docs/readme.md"])])
        out = apply_risk_rules(plan, self._config([{"pattern": "docs/**", "risk": "normal"}]))
        self.assertEqual(out["tasks"][0]["risk"], "high")

    def test_input_plan_is_not_mutated(self) -> None:
        plan = _plan([_task("T1", domains=["src/auth/x.py"])])
        apply_risk_rules(plan, self._config([{"pattern": "src/auth/**", "risk": "high"}]))
        self.assertEqual(plan["tasks"][0]["risk"], "normal")

    def test_empty_domains_keep_plan_risk(self) -> None:
        plan = _plan([_task("T1", domains=[])])
        out = apply_risk_rules(plan, self._config([{"pattern": "**", "risk": "high"}]))
        self.assertEqual(out["tasks"][0]["risk"], "normal")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_plan_quality -v`
Expected: FAIL — `ImportError: cannot import name 'apply_risk_rules'`

- [ ] **Step 3: Implement in `wave_delivery/plan.py`**

After `apply_config_defaults` (import `domains_overlap` from `.domains` at the top):

```python
def apply_risk_rules(plan_dict: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Derive each task's risk from config riskRules; upward only.

    Overlap semantics come from domains.py deliberately: where a rule and a
    domain might cover the same file, the answer is "they overlap" — a task
    wrongly reviewed costs a review, a task wrongly unreviewed costs a merge.
    """
    high_patterns = [
        rule["pattern"] for rule in config.get("riskRules", []) if rule["risk"] == "high"
    ]
    if not high_patterns:
        return plan_dict
    tasks = []
    for entry in plan_dict["tasks"]:
        derived = entry["risk"]
        if derived != "high" and any(
            domains_overlap(pattern, domain)
            for pattern in high_patterns
            for domain in entry["conflictDomains"]
        ):
            derived = "high"
        tasks.append({**entry, "risk": derived})
    return {**plan_dict, "tasks": tasks}
```

- [ ] **Step 4: Wire into the CLI**

In `cli.py`'s plan-apply handler, right after the `apply_config_defaults` overlay (keep the same `config.json`-exists guard; import `apply_risk_rules` from `.plan`):

```python
                plan_dict = apply_risk_rules(plan_dict, config)
```

(Adapt the variable names to the handler as it exists — read it first; the overlay result must be what `apply_plan` receives.)

- [ ] **Step 5: Run tests, full suite, commit**

Run: `python3 -m unittest tests.test_plan_quality -v` — PASS.
Run: `python3 -m unittest discover -s tests -q` — PASS.

```bash
git add wave_delivery/plan.py wave_delivery/cli.py tests/test_plan_quality.py
git commit -m "feat(plan): derive task risk from config riskRules at apply time"
```

---

### Task 2: Lint module — serialization and risk-distribution checks

**Files:**
- Create: `wave_delivery/lint.py`
- Test: `tests/test_plan_quality.py`

**Interfaces:**
- Produces: `lint_plan(plan_dict: dict, wdd_dir: Path | str | None = None) -> list[dict]` — each finding is `{"code": str, "severity": "warning", "message": str}` plus optional `"task"`. This task implements codes `serialized_plan` and `uniform_risk`; Tasks 3–4 add more codes to the same function. `wdd_dir` is only used by Task 4's brief checks; pass-through now.
- Consumes: `plan.state_from_plan(plan_dict)` + `engine.admission_schedule(state)` for the projected rounds.

**Check definitions (exact):**
- `serialized_plan`: with N = task count ≥ 3, compute `rounds = admission_schedule(state_from_plan(plan))`. Warn when every round admits exactly one task (full serialization), OR when N ≥ 4 and `len(rounds) > 0.75 * N` (near-full). Message names the round count vs task count and says which lever to check (dependsOn fan-out, domain overlap).
- `uniform_risk`: N ≥ 4 and every task has the same risk. Message differs by direction: all `high` → "risk_based review degenerates to review-everything"; all `normal` → "risk_based review will review nothing — confirm no task touches a high-risk area".

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_plan_quality.py`:

```python
from wave_delivery.lint import lint_plan


def _codes(findings: list[dict]) -> set[str]:
    return {finding["code"] for finding in findings}


class LintSerializationTest(unittest.TestCase):
    def test_full_chain_warns(self) -> None:
        plan = _plan([
            _task("T1"),
            _task("T2", depends_on=["T1"]),
            _task("T3", depends_on=["T2"]),
        ])
        self.assertIn("serialized_plan", _codes(lint_plan(plan)))

    def test_parallel_plan_is_clean(self) -> None:
        plan = _plan([_task("T1"), _task("T2"), _task("T3")])
        self.assertNotIn("serialized_plan", _codes(lint_plan(plan)))

    def test_two_tasks_never_warn_serialization(self) -> None:
        plan = _plan([_task("T1"), _task("T2", depends_on=["T1"])])
        self.assertNotIn("serialized_plan", _codes(lint_plan(plan)))


class LintRiskDistributionTest(unittest.TestCase):
    def test_all_high_warns(self) -> None:
        plan = _plan([_task(f"T{n}", risk="high") for n in range(1, 5)])
        self.assertIn("uniform_risk", _codes(lint_plan(plan)))

    def test_all_normal_warns(self) -> None:
        plan = _plan([_task(f"T{n}") for n in range(1, 5)])
        self.assertIn("uniform_risk", _codes(lint_plan(plan)))

    def test_mixed_risk_is_clean(self) -> None:
        tasks = [_task(f"T{n}") for n in range(1, 5)]
        tasks[0]["risk"] = "high"
        self.assertNotIn("uniform_risk", _codes(lint_plan(_plan(tasks))))

    def test_three_tasks_never_warn_uniform(self) -> None:
        plan = _plan([_task(f"T{n}") for n in range(1, 4)])
        self.assertNotIn("uniform_risk", _codes(lint_plan(plan)))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_plan_quality -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wave_delivery.lint'`

- [ ] **Step 3: Create `wave_delivery/lint.py`**

```python
"""Deterministic plan-quality checks.

Every check here exists because an agent-authored plan exhibited the failure
in the wild: a fully serialized dependency chain, every task marked high
risk, and per-file conflict-domain lists so exhaustive they were pure
ceremony. Lint warns; it never blocks unless the caller passes --strict.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .engine import admission_schedule
from .plan import state_from_plan


def _check_serialization(plan_dict: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = plan_dict["tasks"]
    if len(tasks) < 3:
        return []
    rounds = admission_schedule(state_from_plan(plan_dict))
    fully_serial = all(len(round_["tasks"]) == 1 for round_ in rounds)
    near_serial = len(tasks) >= 4 and len(rounds) > 0.75 * len(tasks)
    if not (fully_serial or near_serial):
        return []
    return [
        {
            "code": "serialized_plan",
            "severity": "warning",
            "message": (
                f"{len(tasks)} tasks admit in {len(rounds)} rounds — the plan is "
                "effectively serialized. Check dependsOn for vague sequencing and "
                "conflictDomains for accidental overlap; maxConcurrent buys nothing here."
            ),
        }
    ]


def _check_risk_distribution(plan_dict: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = plan_dict["tasks"]
    if len(tasks) < 4:
        return []
    risks = {entry["risk"] for entry in tasks}
    if len(risks) != 1:
        return []
    direction = (
        "risk_based review degenerates to review-everything"
        if risks == {"high"}
        else "risk_based review will review nothing — confirm no task touches a high-risk area"
    )
    return [
        {
            "code": "uniform_risk",
            "severity": "warning",
            "message": f"every task is risk={next(iter(risks))!r}: {direction}.",
        }
    ]


def lint_plan(
    plan_dict: dict[str, Any], wdd_dir: Path | str | None = None
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    findings.extend(_check_serialization(plan_dict))
    findings.extend(_check_risk_distribution(plan_dict))
    return findings
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_plan_quality -v` — PASS.
Run: `python3 -m unittest discover -s tests -q` — PASS.

- [ ] **Step 5: Commit**

```bash
git add wave_delivery/lint.py tests/test_plan_quality.py
git commit -m "feat(lint): serialization and risk-distribution checks"
```

---

### Task 3: Lint — domain-granularity checks

**Files:**
- Modify: `wave_delivery/lint.py`
- Test: `tests/test_plan_quality.py`

**Interfaces:**
- Produces: two more codes emitted by `lint_plan`:
  - `enumerated_domains` (per task): a task lists ≥ 4 wildcard-free domains sharing the same directory (the path up to the final `/`; top-level files share directory `""` but are exempt). Message suggests the `<dir>/**` glob. Finding carries `"task": <id>`.
  - `coarse_domain` (per domain): one domain string overlaps (via `domains.domains_overlap`) the domains of ≥ 3 OTHER tasks. Reported once per offending domain, naming the count. Finding carries `"task": <owning task id>`.
- Consumes: `domains.domains_overlap`, `domains.WILDCARDS`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_plan_quality.py`:

```python
class LintDomainGranularityTest(unittest.TestCase):
    def test_enumerated_file_list_warns_with_glob_suggestion(self) -> None:
        plan = _plan([
            _task("T1", domains=[
                "src/ah/constants.py", "src/ah/endpoints.py",
                "src/ah/errors.py", "src/ah/types.py",
            ]),
            _task("T2", domains=["docs/**"]),
        ])
        findings = [f for f in lint_plan(plan) if f["code"] == "enumerated_domains"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["task"], "T1")
        self.assertIn("src/ah/**", findings[0]["message"])

    def test_three_files_same_dir_is_clean(self) -> None:
        plan = _plan([
            _task("T1", domains=["src/a.py", "src/b.py", "src/c.py"]),
            _task("T2", domains=["docs/**"]),
        ])
        self.assertNotIn("enumerated_domains", _codes(lint_plan(plan)))

    def test_coarse_domain_overlapping_most_tasks_warns(self) -> None:
        plan = _plan([
            _task("T1", domains=["src/**"]),
            _task("T2", domains=["src/api/**"]),
            _task("T3", domains=["src/db/**"]),
            _task("T4", domains=["src/ui/**"]),
        ])
        findings = [f for f in lint_plan(plan) if f["code"] == "coarse_domain"]
        self.assertTrue(any("src/**" in f["message"] and f["task"] == "T1" for f in findings))

    def test_disjoint_domains_are_clean(self) -> None:
        plan = _plan([
            _task("T1", domains=["src/api/**"]),
            _task("T2", domains=["src/db/**"]),
            _task("T3", domains=["docs/**"]),
            _task("T4", domains=["tests/**"]),
        ])
        self.assertNotIn("coarse_domain", _codes(lint_plan(plan)))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_plan_quality.LintDomainGranularityTest -v`
Expected: FAIL — findings lists are empty (codes not implemented)

- [ ] **Step 3: Implement in `wave_delivery/lint.py`**

Add (import `WILDCARDS, domains_overlap` from `.domains`):

```python
def _check_enumerated_domains(plan_dict: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for entry in plan_dict["tasks"]:
        by_dir: dict[str, int] = {}
        for domain in entry["conflictDomains"]:
            if any(wildcard in domain for wildcard in WILDCARDS):
                continue
            directory, _, _ = domain.rpartition("/")
            if directory:
                by_dir[directory] = by_dir.get(directory, 0) + 1
        for directory, count in sorted(by_dir.items()):
            if count >= 4:
                findings.append(
                    {
                        "code": "enumerated_domains",
                        "severity": "warning",
                        "task": entry["id"],
                        "message": (
                            f"{entry['id']} lists {count} individual files under "
                            f"{directory}/ — consider the glob {directory}/** unless "
                            "another task must write there concurrently."
                        ),
                    }
                )
    return findings


def _check_coarse_domains(plan_dict: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    tasks = plan_dict["tasks"]
    for entry in tasks:
        for domain in entry["conflictDomains"]:
            others = sum(
                1
                for other in tasks
                if other["id"] != entry["id"]
                and any(domains_overlap(domain, other_domain)
                        for other_domain in other["conflictDomains"])
            )
            if others >= 3:
                findings.append(
                    {
                        "code": "coarse_domain",
                        "severity": "warning",
                        "task": entry["id"],
                        "message": (
                            f"domain {domain!r} on {entry['id']} overlaps {others} other "
                            "tasks — it will serialize them all; narrow it to what the "
                            "task actually writes."
                        ),
                    }
                )
    return findings
```

Wire both into `lint_plan` after the existing checks.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_plan_quality -v` — PASS.
Run: `python3 -m unittest discover -s tests -q` — PASS.

- [ ] **Step 5: Commit**

```bash
git add wave_delivery/lint.py tests/test_plan_quality.py
git commit -m "feat(lint): flag enumerated file lists and coarse conflict domains"
```

---

### Task 4: Lint — brief checks, `wddctl plan lint`, auto-run on apply

**Files:**
- Modify: `wave_delivery/lint.py`
- Modify: `wave_delivery/cli.py` (new `plan lint` subcommand; plan-apply handler gains lint output + `--strict`)
- Test: `tests/test_plan_quality.py`

**Interfaces:**
- Produces: code `missing_brief` (per task): when `wdd_dir` is provided, a task whose `specPath` (resolved relative to `wdd_dir`) does not exist, or contains fewer than 3 non-blank lines, gets a warning carrying `"task"`. When `wdd_dir` is None the check is skipped.
- CLI: `wddctl plan lint --plan plan.json [--strict]` — validates the plan file (`read_plan`), applies config overlays when `.wdd/config.json` exists (`apply_config_defaults` with the raw scope + `apply_risk_rules`, same as apply, so lint sees what apply would see), prints `{"findings": [...], "strict": bool}`; exit 0 normally, exit 2 with `--strict` when findings exist (raise `ValidationError` listing the codes — the existing handler maps it).
- `plan apply` (both dry-run and real): the result JSON gains `"lint": [...]` computed from the same overlaid plan; apply gains `--strict` which refuses (same `ValidationError` path) BEFORE mutating state or creating branches when findings exist.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_plan_quality.py`:

```python
from wave_delivery.cli import main


def _git_repo(tmp: str) -> Path:
    root = Path(tmp) / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    (root / "seed").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", "commit", "-qm", "seed"],
        cwd=root, check=True,
    )
    return root


def _cli(state: str, *argv: str) -> tuple[int, str]:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = main(["--state", state, *argv])
    return code, stdout.getvalue()


class LintBriefTest(unittest.TestCase):
    def test_missing_and_empty_briefs_warn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            (wdd / "tasks").mkdir(parents=True)
            (wdd / "tasks" / "T1.md").write_text("# T1\n\nreal brief content here\n", encoding="utf-8")
            (wdd / "tasks" / "T2.md").write_text("\n\n", encoding="utf-8")
            plan = _plan([
                _task("T1"), _task("T2"), _task("T3"),
            ])
            for entry in plan["tasks"]:
                entry["specPath"] = f"tasks/{entry['id']}.md"
            findings = [f for f in lint_plan(plan, wdd) if f["code"] == "missing_brief"]
            self.assertEqual({f["task"] for f in findings}, {"T2", "T3"})

    def test_no_wdd_dir_skips_brief_check(self) -> None:
        plan = _plan([_task("T1")])
        self.assertNotIn("missing_brief", _codes(lint_plan(plan)))


class LintCliTest(unittest.TestCase):
    def test_plan_lint_reports_findings_exit_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            plan = _plan([
                _task("T1"), _task("T2", depends_on=["T1"]), _task("T3", depends_on=["T2"]),
            ])
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            code, out = _cli(str(root / ".wdd" / "state.json"), "plan", "lint", "--plan", str(plan_file))
            self.assertEqual(code, 0)
            self.assertIn("serialized_plan", out)

    def test_plan_lint_strict_fails_on_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            plan = _plan([
                _task("T1"), _task("T2", depends_on=["T1"]), _task("T3", depends_on=["T2"]),
            ])
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code, _ = _cli(str(root / ".wdd" / "state.json"), "plan", "lint", "--plan", str(plan_file), "--strict")
            self.assertNotEqual(code, 0)

    def test_apply_result_carries_lint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            plan = _plan([
                _task("T1"), _task("T2", depends_on=["T1"]), _task("T3", depends_on=["T2"]),
            ])
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            code, out = _cli(
                str(root / ".wdd" / "state.json"),
                "plan", "apply", "--plan", str(plan_file), "--repo", str(root), "--dry-run",
            )
            self.assertEqual(code, 0)
            self.assertIn("serialized_plan", out)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_plan_quality.LintBriefTest tests.test_plan_quality.LintCliTest -v`
Expected: FAIL — `missing_brief` absent; `plan lint` unknown subcommand (argparse error exit 2 — the test asserting exit 0 fails)

- [ ] **Step 3: Implement**

In `lint.py`:

```python
def _check_briefs(plan_dict: dict[str, Any], wdd_dir: Path | str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for entry in plan_dict["tasks"]:
        brief = Path(wdd_dir) / entry["specPath"]
        content_lines = 0
        if brief.is_file():
            content_lines = sum(
                1 for line in brief.read_text(encoding="utf-8").splitlines() if line.strip()
            )
        if content_lines < 3:
            reason = "does not exist" if not brief.is_file() else "is effectively empty"
            findings.append(
                {
                    "code": "missing_brief",
                    "severity": "warning",
                    "task": entry["id"],
                    "message": f"{entry['id']}: brief {entry['specPath']} {reason} — a worker dispatched on it will improvise.",
                }
            )
    return findings
```

Wire into `lint_plan` guarded by `if wdd_dir is not None:`.

In `cli.py`:

1. Parser — on the existing `plan` subparsers add:

```python
    plan_lint = plan_subparsers.add_parser("lint", help="report plan-quality warnings")
    plan_lint.add_argument("--plan", type=Path, required=True)
    plan_lint.add_argument("--strict", action="store_true")
```

and add `--strict` to the existing `plan apply` parser.

2. Extract the config-overlay steps the apply handler already performs (raw-scope read, `apply_config_defaults`, `apply_risk_rules`) into a small local helper so `lint` and `apply` share it verbatim, then:

```python
        if args.command == "plan" and args.plan_command == "lint":
            plan_dict, wdd_dir = _overlaid_plan(args, store)  # the shared helper
            findings = lint_plan(plan_dict, wdd_dir if wdd_dir.exists() else None)
            if args.strict and findings:
                raise ValidationError(
                    "plan lint --strict: " + ", ".join(sorted({f["code"] for f in findings}))
                )
            _print_json({"findings": findings, "strict": args.strict})
            return 0
```

and in the apply handler, after the overlay and before calling `apply_plan`:

```python
            findings = lint_plan(plan_dict, wdd_dir if wdd_dir.exists() else None)
            if args.strict and findings:
                raise ValidationError(
                    "plan apply --strict: " + ", ".join(sorted({f["code"] for f in findings}))
                )
```

and merge `"lint": findings` into the printed apply result. (`wdd_dir = store.path.parent`; the brief check needs it only when `.wdd/` exists.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_plan_quality -v` — PASS.
Run: `python3 -m unittest discover -s tests -q` — PASS (existing plan-apply tests must be unaffected: lint only ADDS a key to the result).

- [ ] **Step 5: Commit**

```bash
git add wave_delivery/lint.py wave_delivery/cli.py tests/test_plan_quality.py
git commit -m "feat(lint): brief checks, plan lint verb, and lint-on-apply"
```

---

### Task 5: Deferral — governance hardening (event gate + migrate lock)

Phase-1 final-review deferrals: `event apply` bypasses the drift gate; `migrate_governance` writes state without the lock or a revision bump.

**Files:**
- Modify: `wave_delivery/cli.py` (`GOVERNED_VERBS`)
- Modify: `wave_delivery/setup.py` (`migrate_governance` state mutation)
- Modify: `docs/wddctl.md` (exemption paragraph: remove `event` from the exempt list, state it is governed)
- Test: `tests/test_plan_quality.py`

**Interfaces:**
- `("event", "apply")` joins `GOVERNED_VERBS` — a drifted repo refuses raw transitions too (the escape hatch escapes the state machine's transitions, not governance).
- `migrate_governance`'s ratification invalidation goes through `engine.apply_mutation` with `event_type="governance.migrated"`, `task_id=None`, `data={}` and a mutator that sets `constitution = {"status": "draft", "ratification": None}` on a `copied_state` — giving it the lock, a revision bump, and an audit event. The function's return shape is unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_plan_quality.py`:

```python
from wave_delivery.config import governance_fingerprint, load_config, save_config, set_value
from wave_delivery.setup import init_repository, migrate_governance
from wave_delivery.store import StateStore
from wave_delivery.schema import new_state


class EventApplyGovernanceTest(unittest.TestCase):
    def test_event_apply_refuses_on_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            init_repository(wdd, root)
            config = load_config(wdd)
            config = set_value(config, "merge.surface", "local")
            if any(q["path"] == "verification.commands" for q in config["openQuestions"]):
                config = set_value(config, "verification.commands", ["true"])
            save_config(wdd, config)
            store = StateStore(wdd / "state.json")
            state = store.read()
            state["constitution"] = {
                "status": "ratified",
                "ratification": {"by": "t", "decisionFingerprint": governance_fingerprint(wdd)},
            }
            store.write(state)
            save_config(wdd, set_value(load_config(wdd), "concurrency.maxConcurrent", 9))
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code, _ = _cli(
                    str(wdd / "state.json"),
                    "event", "apply", "--event", "note.added", "--data", '{"note": "x"}',
                )
            self.assertNotEqual(code, 0)
            self.assertIn("drift", stderr.getvalue())


class MigrateGovernanceLockTest(unittest.TestCase):
    def test_invalidation_bumps_revision_and_records_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            wdd.mkdir()
            (wdd / "constitution.md").write_text("# Old\n", encoding="utf-8")
            state = new_state("SCOPE-legacy", base_ref="wdd/legacy")
            state["constitution"] = {
                "status": "ratified",
                "ratification": {"by": "t", "decisionFingerprint": "sha256:old"},
            }
            StateStore(wdd / "state.json").write(state)
            before = StateStore(wdd / "state.json").read()["revision"]
            result = migrate_governance(wdd)
            self.assertTrue(result["ratificationInvalidated"])
            after = StateStore(wdd / "state.json").read()
            self.assertEqual(after["revision"], before + 1)
            self.assertEqual(after["events"][-1]["type"], "governance.migrated")
```

Note for the implementer: check the exact `event apply` CLI flag names in
`build_parser()` (`--event`, `--data` are the plan's best guess — adapt the
test's argv to the real ones, assertions unchanged).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_plan_quality.EventApplyGovernanceTest tests.test_plan_quality.MigrateGovernanceLockTest -v`
Expected: EventApplyGovernanceTest fails (exit 0 — gate absent); MigrateGovernanceLockTest fails (revision unchanged / no event)

- [ ] **Step 3: Implement**

1. `cli.py`: add `("event", "apply")` to `GOVERNED_VERBS` with a one-line comment ("the escape hatch bypasses transitions, not governance"). Verify the `_subcommand` helper resolves `event_command`.
2. `setup.py` `migrate_governance`: replace the direct read/modify/`store.write` block with `apply_mutation` (import from `.engine`):

```python
    invalidated = False
    if store.exists():
        if store.read()["constitution"]["status"] == "ratified":
            def _invalidate(state: dict[str, Any]) -> dict[str, Any]:
                state = copied_state(state)
                state["constitution"] = {"status": "draft", "ratification": None}
                return state

            apply_mutation(
                store,
                event_type="governance.migrated",
                task_id=None,
                data={},
                idempotency_key=None,
                expected_revision=None,
                mutator=_invalidate,
            )
            invalidated = True
```

(`copied_state` from `.schema`. The pre-check read is outside the lock; that is fine — the mutator re-runs on locked state, and invalidating an already-draft constitution is a no-op worth guarding inside the mutator too: `if state["constitution"]["status"] != "ratified": return state` — but then the event still records; keep the outer check as the gate and accept the tiny race, mirroring the codebase's read-then-mutate patterns.)
3. `docs/wddctl.md`: move `event` from the exempt list to the governed list in the exemption paragraph, with the one-line rationale.

- [ ] **Step 4: Run tests, full suite, commit**

Run: `python3 -m unittest tests.test_plan_quality -v` — PASS.
Run: `python3 -m unittest discover -s tests -q` — PASS (a pre-existing test using `event apply` on a legacy no-config scope stays green because drift is a legacy no-op).

```bash
git add wave_delivery/cli.py wave_delivery/setup.py docs/wddctl.md tests/test_plan_quality.py
git commit -m "fix(governance): gate event apply and lock migrate_governance state write"
```

---

### Task 6: Deferral — repair hint + overlay tests

Phase-1 deferrals: circular `init`↔`next` hint when `config.json` was deleted; missing tests for the `reconcileEveryNMerges`/`maxConcurrent` config overlays.

**Files:**
- Modify: `wave_delivery/setup.py` (`setup_next_actions`)
- Test: `tests/test_plan_quality.py`

**Interfaces:**
- `setup_next_actions` — before calling `load_config`, check `config_path(wdd_dir).exists()`. When missing, return the standard shape with one action:

```python
        {
            "task": "-",
            "action": "repair_config",
            "judgment": (
                "state.json exists but config.json is missing. Restore "
                ".wdd/config.json from version control if it was committed; "
                "otherwise delete .wdd/state.json and re-run 'wddctl init' "
                "to regenerate both."
            ),
        }
```

  (No `command` key — both recovery paths destroy or restore user data; that decision is judgment.) This replaces the dead-end `ValidationError` for the scope-None case; the scope-present case still routes to the execute path in `cli.py` (unchanged from phase 1).
- Overlay tests: end-to-end through `main([...])` — init, resolve questions, ratify, then apply a plan whose raw scope omits `reconcileEveryNMerges` and `maxConcurrent` after `config set merge.reconcileEveryNMerges 5` and `config set concurrency.maxConcurrent 2`; assert the adopted state carries 5 and 2. A second plan with explicit values keeps them.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_plan_quality.py`:

```python
from wave_delivery.setup import setup_next_actions


class RepairConfigHintTest(unittest.TestCase):
    def test_missing_config_yields_repair_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            init_repository(wdd, root)
            (wdd / "config.json").unlink()
            state = StateStore(wdd / "state.json").read()
            result = setup_next_actions(state, wdd)
            self.assertEqual(result["actions"][0]["action"], "repair_config")


class ConfigOverlayEndToEndTest(unittest.TestCase):
    def test_omitted_scope_fields_default_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            state = str(wdd / "state.json")
            self.assertEqual(_cli(state, "init", "--repo", str(root))[0], 0)
            self.assertEqual(_cli(state, "config", "set", "merge.surface", "local")[0], 0)
            config = load_config(wdd)
            if any(q["path"] == "verification.commands" for q in config["openQuestions"]):
                self.assertEqual(
                    _cli(state, "config", "set", "verification.commands", '["true"]')[0], 0
                )
            self.assertEqual(_cli(state, "config", "set", "merge.reconcileEveryNMerges", "5")[0], 0)
            self.assertEqual(_cli(state, "config", "set", "concurrency.maxConcurrent", "2")[0], 0)
            self.assertEqual(_cli(state, "constitution", "ratify", "--by", "t")[0], 0)
            plan = _plan([_task("T1")])
            del plan["scope"]["reconcileEveryNMerges"]
            del plan["scope"]["maxConcurrent"]
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            self.assertEqual(
                _cli(state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root))[0], 0
            )
            adopted = StateStore(wdd / "state.json").read()
            self.assertEqual(adopted["reconcile"]["everyNMerges"], 5)
            self.assertEqual(adopted["scope"]["maxConcurrent"], 2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_plan_quality.RepairConfigHintTest tests.test_plan_quality.ConfigOverlayEndToEndTest -v`
Expected: RepairConfigHintTest fails with `ValidationError` (config file does not exist); the overlay test should PASS already (it pins behavior shipped in phase 1's fix wave) — if it fails, that is a real regression to investigate.

- [ ] **Step 3: Implement the repair hint**

In `setup_next_actions`, before `config = load_config(wdd_dir)`:

```python
    if not config_path(wdd_dir).exists():
        return {
            "scope": None,
            "revision": state["revision"],
            "phase": "setup",
            "actions": [<the repair_config action from the interface block>],
            "blockers": [],
        }
```

(`config_path` is already imported in `setup.py` via the config import — verify.)

- [ ] **Step 4: Run tests, full suite, commit**

Run: `python3 -m unittest tests.test_plan_quality -v` — PASS.
Run: `python3 -m unittest discover -s tests -q` — PASS.

```bash
git add wave_delivery/setup.py tests/test_plan_quality.py
git commit -m "fix(setup): repair hint for missing config; pin config-overlay behavior"
```

---

### Task 7: Documentation and skill update

**Files:**
- Modify: `docs/wddctl.md` (new `plan lint` section; riskRules derivation note in the plan/config sections)
- Modify: `skills/wdd-plan/SKILL.md` (risk section: risk is now derived from config riskRules, override upward only; new closing step: run `wddctl plan lint` and address warnings before `plan apply`)
- Test: none (docs/skill prose), but transcripts must be real

**Interfaces:**
- Consumes: the exact CLI behavior of Tasks 1–6. Repo convention: paste REAL transcripts from a scratch run (generate a deliberately serialized plan to show `serialized_plan` output, and a riskRules match showing derived high risk in the applied state).

- [ ] **Step 1: Generate transcripts** in a scratch dir via `python3 scripts/wddctl.py` (init → resolve → ratify → `plan lint` on a bad plan → apply showing `"lint"` in the result and derived risk in `status`/state).

- [ ] **Step 2: Update `docs/wddctl.md`** — `plan lint` section (codes table: serialized_plan, uniform_risk, enumerated_domains, coarse_domain, missing_brief; --strict semantics; auto-run on apply), riskRules derivation paragraph in the config section, and the governed-verbs list update from Task 5 verified present.

- [ ] **Step 3: Update `skills/wdd-plan/SKILL.md`** — rewrite the "Risk and review" section: agents no longer hand-assign risk when riskRules exist (state that plan risk is an upward-only override), and add to "Sanity-check before applying": run `wddctl plan lint --plan plan.json`, address every warning or explain in the plan-approval message why it stands.

- [ ] **Step 4: Run the full suite once (unchanged code, sanity), commit**

```bash
git add docs/wddctl.md skills/wdd-plan/SKILL.md
git commit -m "docs: document plan lint and derived risk; update wdd-plan skill"
```

---

## Self-review checklist (run after Task 7)

- Spec §4 derived risk: Task 1. Spec §4 lint checks (serialization, risk distribution, domain granularity both directions, briefs): Tasks 2–4. Auto-run on apply + --strict: Task 4.
- Phase-1 deferrals: event-apply gate + migrate lock (Task 5), repair hint + overlay tests (Task 6). NOT addressed here (still deferred): workflow.md rewrite and constitution-template wording (phase 3), TOCTOU double config load (cosmetic), v2-flavored migration notes (cosmetic).
- Lint is advisory-by-default everywhere; nothing in this phase changes admission or merge semantics.
