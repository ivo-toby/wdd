# Setup & Config Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make WDD setup deterministic: `wddctl init` scaffolds `.wdd/` (config.json, prose constitution, state.json with no scope), and `wddctl next` drives the agent from init through ratification to plan apply — phase 1 of the 2026-07-28 onboarding/workflow redesign spec.

**Architecture:** A new `wave_delivery/config.py` owns the machine-config file (`.wdd/config.json`): defaults, validation, dotted get/set, open questions, and the governance fingerprint that covers config.json + constitution.md together. A new `wave_delivery/setup.py` owns `wddctl init` (probe → scaffold) and the setup-phase action queue that `next` shows before a scope exists. Schema bumps to v4 to allow `scope: null` (state exists before any plan). `plan apply` adopts the scope into an init-created state. Execution verbs refuse when governance files drifted from the ratified fingerprint.

**Tech Stack:** Python 3 stdlib only (repo has zero runtime dependencies — keep it that way). Tests: `unittest`, run via `python3 -m unittest`.

## Global Constraints

- No new runtime dependencies; stdlib only (this is why config validation is hand-rolled like `wave_delivery/schema.py`, not `jsonschema`).
- All state writes go through `StateStore` / `atomic_write_text` (`wave_delivery/store.py`).
- Errors raise `ValidationError` or `IllegalTransition` from `wave_delivery/errors.py`; the CLI already maps them to stderr + exit codes.
- Conventional commits (`feat(scope): ...`); never skip hooks; do not push.
- New tests live in `tests/test_setup_config.py` (discovery: `python3 -m unittest discover -s tests` picks up any `test_*.py`). Follow the existing `unittest` style; invoke the CLI as `main([...])` from `wave_delivery.cli`.
- Spec: `docs/superpowers/specs/2026-07-28-onboarding-and-workflow-redesign-design.md`. Sections referenced per task.
- Full suite must pass at the end of every task: `python3 -m unittest discover -s tests -q`.

---

### Task 1: Config module — defaults, validation, load/save

Spec §2. The config file is `.wdd/config.json`.

**Files:**
- Create: `wave_delivery/config.py`
- Test: `tests/test_setup_config.py`

**Interfaces:**
- Produces: `DEFAULT_CONFIG: dict`, `default_config() -> dict` (deep copy), `validate_config(config: dict) -> None` (raises `ValidationError`), `config_path(wdd_dir: Path) -> Path`, `load_config(wdd_dir: Path) -> dict`, `save_config(wdd_dir: Path, config: dict) -> None`. `wdd_dir` is always the `.wdd` directory (callers derive it as `store.path.parent`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_setup_config.py`:

```python
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wave_delivery.config import (
    default_config,
    load_config,
    save_config,
    validate_config,
)
from wave_delivery.errors import ValidationError


class ConfigValidationTest(unittest.TestCase):
    def test_default_config_validates(self) -> None:
        validate_config(default_config())

    def test_default_config_is_a_fresh_copy(self) -> None:
        first = default_config()
        first["merge"]["surface"] = "local"
        self.assertEqual(default_config()["merge"]["surface"], "pr")

    def test_rejects_unknown_merge_surface(self) -> None:
        config = default_config()
        config["merge"]["surface"] = "carrier-pigeon"
        with self.assertRaises(ValidationError):
            validate_config(config)

    def test_rejects_bad_risk_rule(self) -> None:
        config = default_config()
        config["riskRules"] = [{"pattern": "src/**", "risk": "extreme"}]
        with self.assertRaises(ValidationError):
            validate_config(config)

    def test_rejects_non_local_task_provider(self) -> None:
        config = default_config()
        config["taskProvider"]["type"] = "jira"
        with self.assertRaises(ValidationError):
            validate_config(config)

    def test_rejects_malformed_open_question(self) -> None:
        config = default_config()
        config["openQuestions"] = [{"question": "no path key"}]
        with self.assertRaises(ValidationError):
            validate_config(config)


class ConfigStorageTest(unittest.TestCase):
    def test_save_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            config = default_config()
            config["verification"]["commands"] = ["pytest -q"]
            save_config(wdd, config)
            self.assertEqual(load_config(wdd), config)

    def test_load_missing_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValidationError):
                load_config(Path(tmp) / ".wdd")

    def test_load_invalid_json_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            wdd.mkdir()
            (wdd / "config.json").write_text("{broken", encoding="utf-8")
            with self.assertRaises(ValidationError):
                load_config(wdd)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_setup_config -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wave_delivery.config'`

- [ ] **Step 3: Write the implementation**

Create `wave_delivery/config.py`:

```python
"""Machine-consumed configuration for a WDD repository (.wdd/config.json).

The constitution stays prose; every knob wddctl or a dispatching agent
mechanically consumes lives here. Validation is hand-rolled like schema.py:
this package has no runtime dependencies and keeps it that way.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .schema import REVIEW_POLICIES, RISK_LEVELS
from .store import atomic_write_text


CONFIG_SCHEMA_VERSION = 1
MERGE_SURFACES = {"pr", "local"}
MERGE_MODES = {"controller", "human"}
TASK_PROVIDERS = {"local"}

DEFAULT_CONFIG: dict[str, Any] = {
    "schemaVersion": CONFIG_SCHEMA_VERSION,
    "kind": "wdd_config",
    "branching": {
        "targetBranch": "main",
        "basePattern": "wdd/{scope-slug}",
        "taskPattern": "task/{task-id}",
    },
    "verification": {"commands": [], "unavailableJustification": None},
    "review": {"policy": "risk_based", "blockingSeverities": ["P1", "P2"]},
    "merge": {"surface": "pr", "mode": "controller", "reconcileEveryNMerges": 3},
    "concurrency": {"maxConcurrent": 3},
    "models": {
        "planning": None,
        "implementation": {"default": None, "highRisk": None},
        "review": None,
    },
    "riskRules": [],
    "taskProvider": {"type": "local"},
    "openQuestions": [],
}


def default_config() -> dict[str, Any]:
    return deepcopy(DEFAULT_CONFIG)


def config_path(wdd_dir: Path | str) -> Path:
    return Path(wdd_dir) / "config.json"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(f"config: {message}")


def _require_string_list(value: Any, name: str) -> None:
    _require(
        isinstance(value, list) and all(isinstance(item, str) and item for item in value),
        f"{name} must be a list of non-empty strings",
    )


def validate_config(config: dict[str, Any]) -> None:
    _require(isinstance(config, dict), "must be an object")
    _require(config.get("schemaVersion") == CONFIG_SCHEMA_VERSION,
             f"schemaVersion must be {CONFIG_SCHEMA_VERSION}")
    _require(config.get("kind") == "wdd_config", "kind must be 'wdd_config'")

    branching = config.get("branching")
    _require(isinstance(branching, dict), "branching must be an object")
    for key in ("targetBranch", "basePattern", "taskPattern"):
        _require(isinstance(branching.get(key), str) and branching[key],
                 f"branching.{key} must be a non-empty string")

    verification = config.get("verification")
    _require(isinstance(verification, dict), "verification must be an object")
    _require_string_list(verification.get("commands"), "verification.commands")
    justification = verification.get("unavailableJustification")
    _require(justification is None or (isinstance(justification, str) and justification),
             "verification.unavailableJustification must be null or a non-empty string")

    review = config.get("review")
    _require(isinstance(review, dict), "review must be an object")
    _require(review.get("policy") in REVIEW_POLICIES,
             f"review.policy must be one of {sorted(REVIEW_POLICIES)}")
    _require_string_list(review.get("blockingSeverities"), "review.blockingSeverities")
    _require(all(item in {"P1", "P2", "P3"} for item in review["blockingSeverities"]),
             "review.blockingSeverities entries must be P1, P2, or P3")

    merge = config.get("merge")
    _require(isinstance(merge, dict), "merge must be an object")
    _require(merge.get("surface") in MERGE_SURFACES,
             f"merge.surface must be one of {sorted(MERGE_SURFACES)}")
    _require(merge.get("mode") in MERGE_MODES,
             f"merge.mode must be one of {sorted(MERGE_MODES)}")
    every = merge.get("reconcileEveryNMerges")
    _require(every is None or (isinstance(every, int) and every >= 1),
             "merge.reconcileEveryNMerges must be a positive integer or null")

    concurrency = config.get("concurrency")
    _require(isinstance(concurrency, dict), "concurrency must be an object")
    limit = concurrency.get("maxConcurrent")
    _require(limit is None or (isinstance(limit, int) and limit >= 1),
             "concurrency.maxConcurrent must be a positive integer or null")

    models = config.get("models")
    _require(isinstance(models, dict), "models must be an object")
    for key in ("planning", "review"):
        value = models.get(key)
        _require(value is None or (isinstance(value, str) and value),
                 f"models.{key} must be null or a non-empty string")
    implementation = models.get("implementation")
    _require(isinstance(implementation, dict), "models.implementation must be an object")
    for key in ("default", "highRisk"):
        value = implementation.get(key)
        _require(value is None or (isinstance(value, str) and value),
                 f"models.implementation.{key} must be null or a non-empty string")

    rules = config.get("riskRules")
    _require(isinstance(rules, list), "riskRules must be a list")
    for index, rule in enumerate(rules):
        _require(isinstance(rule, dict), f"riskRules[{index}] must be an object")
        _require(isinstance(rule.get("pattern"), str) and rule["pattern"],
                 f"riskRules[{index}].pattern must be a non-empty string")
        _require(rule.get("risk") in RISK_LEVELS,
                 f"riskRules[{index}].risk must be one of {sorted(RISK_LEVELS)}")

    provider = config.get("taskProvider")
    _require(isinstance(provider, dict), "taskProvider must be an object")
    _require(provider.get("type") in TASK_PROVIDERS,
             f"taskProvider.type must be one of {sorted(TASK_PROVIDERS)} (jira lands in phase 2)")

    questions = config.get("openQuestions")
    _require(isinstance(questions, list), "openQuestions must be a list")
    for index, question in enumerate(questions):
        _require(isinstance(question, dict), f"openQuestions[{index}] must be an object")
        _require(isinstance(question.get("path"), str) and question["path"],
                 f"openQuestions[{index}].path must be a non-empty string")
        _require(isinstance(question.get("question"), str) and question["question"],
                 f"openQuestions[{index}].question must be a non-empty string")
        options = question.get("options")
        if options is not None:
            _require_string_list(options, f"openQuestions[{index}].options")


def load_config(wdd_dir: Path | str) -> dict[str, Any]:
    path = config_path(wdd_dir)
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValidationError(f"config file does not exist: {path}; run 'wddctl init'") from error
    except json.JSONDecodeError as error:
        raise ValidationError(f"config file is not valid JSON: {path}: {error}") from error
    validate_config(config)
    return config


def save_config(wdd_dir: Path | str, config: dict[str, Any]) -> None:
    validate_config(config)
    atomic_write_text(config_path(wdd_dir), json.dumps(config, indent=2, sort_keys=True) + "\n")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_setup_config -v`
Expected: all PASS

- [ ] **Step 5: Run the full suite, then commit**

Run: `python3 -m unittest discover -s tests -q` — expected: all pass.

```bash
git add wave_delivery/config.py tests/test_setup_config.py
git commit -m "feat(config): add machine config module with validation"
```

---

### Task 2: Dotted get/set, open-question resolution, and `wddctl config` CLI

Spec §2: "`wddctl config set <dotted.path> <value>` — agents write answers mechanically", "resolving a question removes it from `openQuestions`".

**Files:**
- Modify: `wave_delivery/config.py`
- Modify: `wave_delivery/cli.py` (add `config` subparser in `build_parser()` around line 242, and handlers in `main()` before the `constitution` handlers)
- Test: `tests/test_setup_config.py`

**Interfaces:**
- Produces: `get_value(config: dict, dotted: str) -> Any` (raises `ValidationError` on unknown path), `set_value(config: dict, dotted: str, value: Any) -> dict` (returns a validated copy with the value set and any matching open question removed). CLI: `wddctl config get <path>`, `wddctl config set <path> <value>`, `wddctl config show`.
- Consumes: Task 1's `load_config` / `save_config` / `validate_config`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_setup_config.py`:

```python
import contextlib
import io
import os

from wave_delivery.cli import main
from wave_delivery.config import get_value, set_value


class ConfigPathAccessTest(unittest.TestCase):
    def test_get_value_walks_dotted_path(self) -> None:
        self.assertEqual(get_value(default_config(), "merge.surface"), "pr")

    def test_get_value_unknown_path_raises(self) -> None:
        with self.assertRaises(ValidationError):
            get_value(default_config(), "merge.velocity")

    def test_set_value_returns_validated_copy(self) -> None:
        config = default_config()
        updated = set_value(config, "merge.surface", "local")
        self.assertEqual(updated["merge"]["surface"], "local")
        self.assertEqual(config["merge"]["surface"], "pr")

    def test_set_value_rejects_invalid_value(self) -> None:
        with self.assertRaises(ValidationError):
            set_value(default_config(), "merge.surface", "carrier-pigeon")

    def test_set_value_resolves_matching_open_question(self) -> None:
        config = default_config()
        config["openQuestions"] = [
            {"path": "merge.surface", "question": "pr or local?", "options": ["pr", "local"]}
        ]
        updated = set_value(config, "merge.surface", "local")
        self.assertEqual(updated["openQuestions"], [])


class ConfigCliTest(unittest.TestCase):
    def _run(self, tmp: str, *argv: str) -> tuple[int, str]:
        state = str(Path(tmp) / ".wdd" / "state.json")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(["--state", state, *argv])
        return code, stdout.getvalue()

    def test_config_set_then_get_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            save_config(wdd, default_config())
            code, _ = self._run(tmp, "config", "set", "verification.commands", '["pytest -q"]')
            self.assertEqual(code, 0)
            code, out = self._run(tmp, "config", "get", "verification.commands")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out), ["pytest -q"])

    def test_config_set_bare_string_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            save_config(wdd, default_config())
            code, _ = self._run(tmp, "config", "set", "merge.surface", "local")
            self.assertEqual(code, 0)
            self.assertEqual(load_config(wdd)["merge"]["surface"], "local")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_setup_config -v`
Expected: FAIL — `ImportError: cannot import name 'get_value'`

- [ ] **Step 3: Implement dotted access in `wave_delivery/config.py`**

Append:

```python
def get_value(config: dict[str, Any], dotted: str) -> Any:
    node: Any = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ValidationError(f"config: unknown path '{dotted}'")
        node = node[part]
    return node


def set_value(config: dict[str, Any], dotted: str, value: Any) -> dict[str, Any]:
    updated = deepcopy(config)
    parts = dotted.split(".")
    node: Any = updated
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            raise ValidationError(f"config: unknown path '{dotted}'")
        node = node[part]
    if not isinstance(node, dict) or parts[-1] not in node:
        raise ValidationError(f"config: unknown path '{dotted}'")
    node[parts[-1]] = value
    updated["openQuestions"] = [
        question for question in updated["openQuestions"] if question.get("path") != dotted
    ]
    validate_config(updated)
    return updated
```

Existing keys only: setting an unknown path is an error, which is what keeps
`config set` from silently inventing knobs that nothing reads.

- [ ] **Step 4: Wire the CLI**

In `build_parser()` (after the `constitution` block, before `return parser`):

```python
    config_cmd = subparsers.add_parser("config", help="read or write .wdd/config.json")
    config_subparsers = config_cmd.add_subparsers(dest="config_command", required=True)
    config_get = config_subparsers.add_parser("get", help="print one value as JSON")
    config_get.add_argument("path", help="dotted path, e.g. merge.surface")
    config_set = config_subparsers.add_parser(
        "set", help="set one value (JSON literal, or bare string fallback)"
    )
    config_set.add_argument("path", help="dotted path, e.g. merge.surface")
    config_set.add_argument("value", help='e.g. local or \'["pytest -q"]\'')
    config_subparsers.add_parser("show", help="print the whole config")
```

In `main()` (before the `constitution` handlers), with imports added at the top
(`from .config import get_value, load_config, save_config, set_value`):

```python
        if args.command == "config":
            wdd_dir = store.path.parent
            config = load_config(wdd_dir)
            if args.config_command == "get":
                _print_json(get_value(config, args.path))
                return 0
            if args.config_command == "show":
                _print_json(config)
                return 0
            if args.config_command == "set":
                try:
                    value = json.loads(args.value)
                except json.JSONDecodeError:
                    value = args.value
                updated = set_value(config, args.path, value)
                save_config(wdd_dir, updated)
                _print_json(
                    {
                        "path": args.path,
                        "value": get_value(updated, args.path),
                        "openQuestions": len(updated["openQuestions"]),
                    }
                )
                return 0
```

- [ ] **Step 5: Run tests, full suite, commit**

Run: `python3 -m unittest tests.test_setup_config -v` — PASS.
Run: `python3 -m unittest discover -s tests -q` — PASS.

```bash
git add wave_delivery/config.py wave_delivery/cli.py tests/test_setup_config.py
git commit -m "feat(config): dotted get/set with open-question resolution and CLI verbs"
```

---

### Task 3: Schema v4 — state before a scope exists

Spec §1: "`state.json` with `scope: null`"; phase is derived, not stored.

**Files:**
- Modify: `wave_delivery/schema.py` (SCHEMA_VERSION, `new_state`, `validate_state`)
- Modify: `wave_delivery/migration.py` (accept v3 sources)
- Test: `tests/test_setup_config.py`

**Interfaces:**
- Produces: `new_setup_state() -> dict` (schema v4, `scope: None`, no tasks), `derived_phase(state: dict) -> str` returning `"setup" | "execute"` (finalize/delivered land in the phase-5 plan), and `validate_state` accepting `scope: None` **only** when `tasks` is empty.
- Consumes: nothing new.

**Compatibility note for the implementer:** bumping `SCHEMA_VERSION` to 4 makes every existing v3 `state.json` unreadable until migrated — that is deliberate honesty (old code must not misread new states), and the v3→v4 conversion is a pure version bump because every valid v3 state is a valid v4 state. `tests/test_wave_delivery.py` builds states via `new_state(...)`, which now emits v4, so the existing suite keeps passing without edits; if any test hardcodes `"schemaVersion": 3`, update that literal to 4.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_setup_config.py`:

```python
from wave_delivery.schema import (
    SCHEMA_VERSION,
    derived_phase,
    new_setup_state,
    new_state,
    validate_state,
)


class SetupStateTest(unittest.TestCase):
    def test_schema_version_is_4(self) -> None:
        self.assertEqual(SCHEMA_VERSION, 4)

    def test_new_setup_state_validates_with_null_scope(self) -> None:
        state = new_setup_state()
        validate_state(state)
        self.assertIsNone(state["scope"])
        self.assertEqual(state["tasks"], {})

    def test_null_scope_with_tasks_is_rejected(self) -> None:
        state = new_setup_state()
        state["tasks"]["TASK-001"] = {"id": "TASK-001"}
        with self.assertRaises(ValidationError):
            validate_state(state)

    def test_derived_phase(self) -> None:
        setup = new_setup_state()
        self.assertEqual(derived_phase(setup), "setup")
        ratified = new_setup_state()
        ratified["constitution"] = {
            "status": "ratified",
            "ratification": {"by": "ivo", "decisionFingerprint": "sha256:abc"},
        }
        self.assertEqual(derived_phase(ratified), "setup")  # still no scope
        executing = new_state("SCOPE-x", base_ref="wdd/x")
        executing["constitution"] = ratified["constitution"]
        self.assertEqual(derived_phase(executing), "execute")


class MigrationV3Test(unittest.TestCase):
    def test_v3_state_migrates_to_v4(self) -> None:
        from wave_delivery.migration import plan_migration

        with tempfile.TemporaryDirectory() as tmp:
            state = new_state("SCOPE-x", base_ref="wdd/x")
            state["schemaVersion"] = 3
            path = Path(tmp) / "state.json"
            path.write_text(json.dumps(state), encoding="utf-8")
            result = plan_migration(path)
            self.assertEqual(result["migrated"]["schemaVersion"], 4)
```

Note: read `wave_delivery/migration.py` for `plan_migration`'s exact return
shape before finalizing the last assertion — if the dry-run result nests the
converted state under a different key, assert on that key instead.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_setup_config -v`
Expected: FAIL — `ImportError: cannot import name 'derived_phase'` (and SCHEMA_VERSION is 3)

- [ ] **Step 3: Implement schema changes**

In `wave_delivery/schema.py`:

1. `SCHEMA_VERSION = 4`.
2. Add after `new_state`:

```python
def new_setup_state() -> dict[str, Any]:
    """State for an initialized repository that has no scope yet.

    Created by `wddctl init` so `next` can drive setup; `plan apply` adopts
    the scope into this state later.
    """
    state = new_state("__setup__")
    state["scope"] = None
    return state


def derived_phase(state: dict[str, Any]) -> str:
    """Phase is computed, never stored: setup until a scope exists."""
    if state.get("scope") is None or state["constitution"]["status"] != "ratified":
        return "setup"
    return "execute"
```

3. In `validate_state`, replace the unconditional scope block with:

```python
    scope = state.get("scope")
    if scope is None:
        if state.get("tasks"):
            raise ValidationError("scope must exist before tasks do; run 'wddctl plan apply'")
    else:
        scope = _require_mapping(scope, "scope")
        _require_string(scope.get("id"), "scope.id")
        _require_string(scope.get("baseRef"), "scope.baseRef", nullable=True)
        if scope.get("reviewPolicy") not in REVIEW_POLICIES:
            raise ValidationError(f"scope.reviewPolicy must be one of {sorted(REVIEW_POLICIES)}")
        max_concurrent = scope.get("maxConcurrent")
        if max_concurrent is not None and (
            not isinstance(max_concurrent, int) or max_concurrent < 1
        ):
            raise ValidationError("scope.maxConcurrent must be a positive integer or null")
```

4. Update the `schemaVersion` error hint so both 2 and 3 point at migrate:

```python
        hint = (
            " run 'wddctl --state <path> migrate --dry-run' to convert it"
            if found in {2, 3}
            else ""
        )
```

In `wave_delivery/migration.py`:

1. `SUPPORTED_SOURCE_VERSIONS = {2, 3}`.
2. In `convert`, branch on the source version: a v3 source only needs
   `state = deepcopy(state); state["schemaVersion"] = SCHEMA_VERSION` and a
   `validate_state` check; the existing v2 body remains for v2 sources.
   Read the function before editing — keep its dry-run/backup contract intact.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_setup_config -v` — PASS.
Run: `python3 -m unittest discover -s tests -q` — the pre-existing suite must also pass; if a test hardcoded schema version 3, update the literal.

- [ ] **Step 5: Commit**

```bash
git add wave_delivery/schema.py wave_delivery/migration.py tests/test_setup_config.py
git commit -m "feat(schema): allow pre-scope state (v4) with derived phase"
```

---

### Task 4: Governance fingerprint over config.json + constitution.md

Spec §2: "The ratification fingerprint covers `config.json` and `constitution.md` together"; "Ratification is refused while `openQuestions` is non-empty."

**Files:**
- Modify: `wave_delivery/config.py`
- Modify: `wave_delivery/cli.py` (the `constitution ratify`/`amend` handler, currently around line 547)
- Test: `tests/test_setup_config.py`

**Interfaces:**
- Produces: `governance_fingerprint(wdd_dir: Path) -> str` (`"sha256:..."` over canonical config JSON + `"\x00"` + constitution text; raises `ValidationError` if either file is missing), `check_ratifiable(wdd_dir: Path) -> None` (raises while `openQuestions` non-empty).
- Consumes: Task 1's `load_config`.
- CLI behavior change: `constitution ratify --by NAME` and `amend --by NAME` no longer require `--proposal`/`--decision-fingerprint`; they compute the governance fingerprint from `.wdd/` themselves. If `--decision-fingerprint` IS passed, it must equal the computed one (this preserves "never ratify text the user hasn't seen" for callers that pin what was shown). `--proposal` keeps working for one deprecation cycle by being ignored with a warning field in the JSON output.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_setup_config.py`:

```python
from wave_delivery.config import check_ratifiable, governance_fingerprint


def _write_governance(wdd: Path, *, questions: list | None = None) -> None:
    config = default_config()
    config["openQuestions"] = questions or []
    save_config(wdd, config)
    (wdd / "constitution.md").write_text("# Constitution\n\nProse only.\n", encoding="utf-8")


class GovernanceFingerprintTest(unittest.TestCase):
    def test_fingerprint_is_stable_and_prefixed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            _write_governance(wdd)
            first = governance_fingerprint(wdd)
            self.assertTrue(first.startswith("sha256:"))
            self.assertEqual(first, governance_fingerprint(wdd))

    def test_fingerprint_changes_when_constitution_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            _write_governance(wdd)
            before = governance_fingerprint(wdd)
            (wdd / "constitution.md").write_text("# Changed\n", encoding="utf-8")
            self.assertNotEqual(before, governance_fingerprint(wdd))

    def test_fingerprint_changes_when_config_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            _write_governance(wdd)
            before = governance_fingerprint(wdd)
            save_config(wdd, set_value(load_config(wdd), "merge.surface", "local"))
            self.assertNotEqual(before, governance_fingerprint(wdd))

    def test_ratifiable_refused_with_open_questions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            _write_governance(
                wdd, questions=[{"path": "merge.surface", "question": "pr or local?"}]
            )
            with self.assertRaises(ValidationError):
                check_ratifiable(wdd)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_setup_config -v`
Expected: FAIL — `ImportError: cannot import name 'governance_fingerprint'`

- [ ] **Step 3: Implement in `wave_delivery/config.py`**

Append:

```python
import hashlib  # move to the top imports


def constitution_path(wdd_dir: Path | str) -> Path:
    return Path(wdd_dir) / "constitution.md"


def governance_fingerprint(wdd_dir: Path | str) -> str:
    """One fingerprint over both governance files.

    Ratifying signs the exact config values AND the exact constitution prose;
    editing either afterwards is drift until an amend re-signs them.
    """
    config = load_config(wdd_dir)
    try:
        constitution = constitution_path(wdd_dir).read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ValidationError(
            f"constitution does not exist: {constitution_path(wdd_dir)}; run 'wddctl init'"
        ) from error
    encoded = (
        json.dumps(config, sort_keys=True, separators=(",", ":")) + "\x00" + constitution
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def check_ratifiable(wdd_dir: Path | str) -> None:
    config = load_config(wdd_dir)
    open_questions = config["openQuestions"]
    if open_questions:
        paths = ", ".join(question["path"] for question in open_questions)
        raise ValidationError(
            f"cannot ratify: {len(open_questions)} open config question(s) remain ({paths}); "
            "resolve them with 'wddctl config set' first"
        )
```

- [ ] **Step 4: Update the ratify/amend CLI handler**

Replace the body of the `constitution ratify|amend` branch in `main()` with:

```python
        if args.command == "constitution" and args.constitution_command in {"ratify", "amend"}:
            wdd_dir = store.path.parent
            check_ratifiable(wdd_dir)
            fingerprint = governance_fingerprint(wdd_dir)
            if args.decision_fingerprint and args.decision_fingerprint != fingerprint:
                raise ValidationError(
                    "provided --decision-fingerprint does not match the current "
                    "config.json + constitution.md; re-read the files before ratifying"
                )
            state, duplicate = apply_event(
                store,
                event_type=f"constitution.{'ratified' if args.constitution_command == 'ratify' else 'amended'}",
                task_id=None,
                data={"by": args.by, "decisionFingerprint": fingerprint},
                **_concurrency(args),
            )
            result = {
                "revision": state["revision"],
                "duplicate": duplicate,
                "decisionFingerprint": fingerprint,
            }
            if args.proposal:
                result["warning"] = "--proposal is deprecated; the fingerprint now covers .wdd/config.json + constitution.md"
            _print_json(result)
            return 0
```

Also relax the parser so `--proposal`/`--decision-fingerprint` are optional on
these subcommands (check the loop near `cli.py:250` that builds them — remove
any `required=True` on that mutually-exclusive group), and add the imports:
`from .config import check_ratifiable, governance_fingerprint`.

- [ ] **Step 5: Run tests, full suite, commit**

Run: `python3 -m unittest tests.test_setup_config -v` — PASS.
Run: `python3 -m unittest discover -s tests -q` — existing constitution tests that pass `--proposal` must still pass (deprecated path). If one asserts ratify *requires* a proposal, update it to match the new contract.

```bash
git add wave_delivery/config.py wave_delivery/cli.py tests/test_setup_config.py
git commit -m "feat(constitution): fingerprint covers config.json and constitution.md"
```

---

### Task 5: `wddctl init` — deterministic scaffold

Spec §1 steps 1–5. New module so `config.py` stays file-format-only.

**Files:**
- Create: `wave_delivery/setup.py`
- Modify: `wave_delivery/cli.py` (add `init` subparser + handler)
- Test: `tests/test_setup_config.py`

**Interfaces:**
- Produces: `init_repository(wdd_dir: Path, repo: Path) -> dict` — creates `config.json` (probed defaults + open questions), `constitution.md` (from `CONSTITUTION_TEMPLATE`), `tasks/`, `shared-context/`, and `state.json` via `new_setup_state()`; returns `{"created": [...], "openQuestions": [...], "alreadyInitialized": bool}`. Idempotent: if `state.json` exists it creates nothing and reports.
- Consumes: `probe_repository` (`wave_delivery/constitution.py`), Task 1's config functions, Task 3's `new_setup_state`, `StateStore`.
- Open questions produced: `merge.surface` **always** (spec: "a deliberate decision, never silently defaulted"); `verification.commands` only when the probe found none.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_setup_config.py`:

```python
import subprocess

from wave_delivery.setup import CONSTITUTION_TEMPLATE, init_repository
from wave_delivery.store import StateStore


def _git_repo(tmp: str) -> Path:
    root = Path(tmp) / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    return root


class InitTest(unittest.TestCase):
    def test_init_scaffolds_everything(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            result = init_repository(wdd, root)
            self.assertFalse(result["alreadyInitialized"])
            self.assertTrue((wdd / "config.json").is_file())
            self.assertTrue((wdd / "constitution.md").is_file())
            self.assertTrue((wdd / "tasks").is_dir())
            self.assertTrue((wdd / "shared-context").is_dir())
            state = StateStore(wdd / "state.json").read()
            self.assertIsNone(state["scope"])

    def test_init_always_asks_about_merge_surface(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            init_repository(root / ".wdd", root)
            config = load_config(root / ".wdd")
            self.assertIn("merge.surface", [q["path"] for q in config["openQuestions"]])

    def test_init_asks_verification_only_when_undetected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            (root / "package.json").write_text("{}", encoding="utf-8")
            init_repository(root / ".wdd", root)
            config = load_config(root / ".wdd")
            self.assertEqual(config["verification"]["commands"], ["npm test"])
            self.assertNotIn(
                "verification.commands", [q["path"] for q in config["openQuestions"]]
            )

    def test_init_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            init_repository(root / ".wdd", root)
            marker = (root / ".wdd" / "constitution.md").read_text(encoding="utf-8")
            result = init_repository(root / ".wdd", root)
            self.assertTrue(result["alreadyInitialized"])
            self.assertEqual(
                (root / ".wdd" / "constitution.md").read_text(encoding="utf-8"), marker
            )

    def test_template_has_no_placeholders_or_json(self) -> None:
        for banned in ("```json", "TBD", "TODO", "<", "state which"):
            self.assertNotIn(banned, CONSTITUTION_TEMPLATE)


class InitCliTest(unittest.TestCase):
    def test_cli_init_prints_next_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            state = str(root / ".wdd" / "state.json")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--state", state, "init", "--repo", str(root)])
            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertFalse(payload["alreadyInitialized"])
            self.assertTrue(payload["openQuestions"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_setup_config -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wave_delivery.setup'`

- [ ] **Step 3: Create `wave_delivery/setup.py`**

```python
"""wddctl init: deterministic scaffolding of a fresh .wdd directory.

Setup used to be prose-choreographed — the agent improvised from a template
and no state existed until `plan apply`. Init moves that choreography into
the controller: everything mechanical is created here, and `next` drives the
rest (resolve open questions -> ratify -> plan).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import config_path, constitution_path, default_config, save_config
from .constitution import probe_repository
from .schema import new_setup_state
from .store import StateStore, atomic_write_text


CONSTITUTION_TEMPLATE = """\
---
id: WDD-CONSTITUTION
kind: constitution
version: 2.0.0
---

# Project Constitution

This document is prose for humans and agents exercising judgment. Every
machine-consumed setting lives in `config.json` next to this file and is
edited with `wddctl config set`.

## Intent

Describe in a few sentences what this project is and what a successful
change to it looks like. Until this section is written for the project,
reviewers fall back to the task briefs alone.

## What reviewers should weigh most

Reviewers block on correctness, security, and unmet objectives (P1), and on
real problems short of that (P2). Beyond those defaults, name what deserves
extra scrutiny in this repository and why — the areas where a bad merge is
expensive to unwind.

## Workflow norms

Work is decomposed into tasks with explicit dependencies and conflict
domains. Workers implement test-first and stay inside their declared
domains. The controller merges only tasks whose review and verification
evidence is current. The final merge to the target branch is made by a
human, never by the controller.
"""


def _open_questions(probed_commands: list[str]) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = [
        {
            "path": "merge.surface",
            "question": (
                "Review/merge surface: 'pr' pushes task branches and mirrors review "
                "findings to pull-request comments; 'local' keeps the whole loop "
                "offline in state.json. Which should this repository use?"
            ),
            "options": ["pr", "local"],
        }
    ]
    if not probed_commands:
        questions.append(
            {
                "path": "verification.commands",
                "question": (
                    "No verification command could be detected. What command(s) prove "
                    'a change works here? (JSON list, e.g. ["pytest -q"])'
                ),
            }
        )
    return questions


def init_repository(wdd_dir: Path | str, repo: Path | str) -> dict[str, Any]:
    wdd_dir = Path(wdd_dir)
    store = StateStore(wdd_dir / "state.json")
    if store.exists():
        return {
            "alreadyInitialized": True,
            "created": [],
            "openQuestions": [],
            "hint": "state exists; run 'wddctl next' for what to do",
        }

    proposal = probe_repository(repo)
    probed_commands = proposal["evidence"]["verificationCommands"]

    config = default_config()
    config["branching"]["targetBranch"] = proposal["decisions"]["targetBranch"]
    config["verification"]["commands"] = probed_commands
    config["openQuestions"] = _open_questions(probed_commands)

    created: list[str] = []
    save_config(wdd_dir, config)
    created.append(str(config_path(wdd_dir)))
    if not constitution_path(wdd_dir).exists():
        atomic_write_text(constitution_path(wdd_dir), CONSTITUTION_TEMPLATE)
        created.append(str(constitution_path(wdd_dir)))
    for directory in ("tasks", "shared-context"):
        (wdd_dir / directory).mkdir(parents=True, exist_ok=True)
        created.append(str(wdd_dir / directory))
    store.write(new_setup_state())
    created.append(str(store.path))
    return {
        "alreadyInitialized": False,
        "created": created,
        "openQuestions": config["openQuestions"],
        "hint": "run 'wddctl next' and follow it",
    }
```

- [ ] **Step 4: Wire the CLI**

In `build_parser()`:

```python
    init = subparsers.add_parser(
        "init", help="scaffold .wdd/: config, constitution draft, and pre-scope state"
    )
    init.add_argument("--repo", type=Path, default=Path("."))
```

In `main()` (put this first among the store-using handlers, since every other
verb assumes state exists), with `from .setup import init_repository` at the top:

```python
        if args.command == "init":
            _print_json(init_repository(store.path.parent, args.repo))
            return 0
```

- [ ] **Step 5: Run tests, full suite, commit**

Run: `python3 -m unittest tests.test_setup_config -v` — PASS.
Run: `python3 -m unittest discover -s tests -q` — PASS.

```bash
git add wave_delivery/setup.py wave_delivery/cli.py tests/test_setup_config.py
git commit -m "feat(init): deterministic .wdd scaffold with probed config and open questions"
```

---

### Task 6: Setup-phase actions in `next` and `status`

Spec §1: `next` gains `resolve_config` → `ratify` → `plan`, "with the same shape as execution actions (literal command included)."

**Files:**
- Modify: `wave_delivery/setup.py`
- Modify: `wave_delivery/cli.py` (`next` and `status` handlers)
- Test: `tests/test_setup_config.py`

**Interfaces:**
- Produces: `setup_next_actions(state: dict, wdd_dir: Path, *, state_path: str | None = None) -> dict` with the `{"scope", "revision", "actions", "blockers"}` shape `next` already prints. Actions in priority order, at most one at a time (setup is sequential by nature):
  - open questions → `{"task": "-", "action": "resolve_config", "questions": [...], "command": "wddctl config set <path> <value>  # once per answered question"}`
  - resolved, unratified → `{"task": "-", "action": "ratify", "command": "wddctl constitution ratify --by NAME"}`
  - ratified, `scope` is None → `{"task": "-", "action": "plan", "command": "wddctl plan apply --plan plan.json --repo ."}` (judgment: decompose per the wdd-plan skill first)
- Consumes: Task 1 `load_config`, Task 3 `derived_phase`.
- CLI: `next` calls `setup_next_actions` instead of `bounded_next_actions` whenever `derived_phase(state) == "setup"`; `status` prints a compact setup summary instead of `status_summary` (which assumes a scope) in that phase.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_setup_config.py`:

```python
from wave_delivery.setup import setup_next_actions


class SetupNextTest(unittest.TestCase):
    def _initialized(self, tmp: str) -> tuple[Path, Path]:
        root = _git_repo(tmp)
        wdd = root / ".wdd"
        init_repository(wdd, root)
        return root, wdd

    def test_first_action_is_resolve_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, wdd = self._initialized(tmp)
            state = StateStore(wdd / "state.json").read()
            result = setup_next_actions(state, wdd)
            self.assertEqual(result["actions"][0]["action"], "resolve_config")
            self.assertTrue(result["actions"][0]["questions"])

    def test_after_resolution_action_is_ratify(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, wdd = self._initialized(tmp)
            config = load_config(wdd)
            config = set_value(config, "merge.surface", "local")
            config = set_value(config, "verification.commands", ["true"])
            save_config(wdd, config)
            state = StateStore(wdd / "state.json").read()
            result = setup_next_actions(state, wdd)
            self.assertEqual(result["actions"][0]["action"], "ratify")

    def test_cli_next_routes_to_setup_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, wdd = self._initialized(tmp)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--state", str(wdd / "state.json"), "next"])
            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["actions"][0]["action"], "resolve_config")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_setup_config -v`
Expected: FAIL — `ImportError: cannot import name 'setup_next_actions'`

- [ ] **Step 3: Implement `setup_next_actions` in `wave_delivery/setup.py`**

```python
import shlex  # top of file

from .config import load_config  # extend the existing config import


def setup_next_actions(
    state: dict[str, Any], wdd_dir: Path | str, *, state_path: str | None = None
) -> dict[str, Any]:
    """The setup-phase counterpart of engine.next_actions.

    Same output shape, one action at a time: setup is sequential, and a
    single unambiguous action is what keeps an agent from improvising.
    """
    prefix = "wddctl" + (f" --state {shlex.quote(state_path)}" if state_path else "")
    actions: list[dict[str, Any]] = []
    config = load_config(wdd_dir)
    if config["openQuestions"]:
        actions.append(
            {
                "task": "-",
                "action": "resolve_config",
                "questions": config["openQuestions"],
                "command": f"{prefix} config set <path> <value>",
                "judgment": "ask the user every listed question in ONE round, then record each answer",
            }
        )
    elif state["constitution"]["status"] != "ratified":
        actions.append(
            {
                "task": "-",
                "action": "ratify",
                "command": f"{prefix} constitution ratify --by NAME",
                "judgment": "show the user config.json and constitution.md; ratify only after explicit sign-off",
            }
        )
    elif state.get("scope") is None:
        actions.append(
            {
                "task": "-",
                "action": "plan",
                "command": f"{prefix} plan apply --plan plan.json --repo .",
                "judgment": "decompose the work per the wdd-plan skill, write task briefs, then apply",
            }
        )
    return {
        "scope": (state.get("scope") or {}).get("id") if state.get("scope") else None,
        "revision": state["revision"],
        "phase": "setup",
        "actions": actions,
        "blockers": [],
    }
```

- [ ] **Step 4: Route `next` and `status` in `main()`**

Add imports: `from .schema import derived_phase` and extend the setup import
with `setup_next_actions`. Change the two handlers:

```python
        if args.command == "next":
            state = store.read()
            if derived_phase(state) == "setup":
                _print_json(
                    setup_next_actions(
                        state, store.path.parent, state_path=_state_option(args)
                    )
                )
                return 0
            _print_json(
                bounded_next_actions(
                    state,
                    max_bytes=args.max_bytes,
                    state_path=_state_option(args),
                    repo=str(args.repo),
                )
            )
            return 0

        if args.command == "status":
            state = store.read()
            if derived_phase(state) == "setup":
                config = load_config(store.path.parent)
                _print_json(
                    {
                        "phase": "setup",
                        "openQuestions": len(config["openQuestions"]),
                        "constitution": state["constitution"]["status"],
                        "scope": None,
                    }
                )
                return 0
            summary = status_summary(state)
            _print_json(summary) if args.json else print(_brief(summary))
            return 0
```

- [ ] **Step 5: Run tests, full suite, commit**

Run: `python3 -m unittest tests.test_setup_config -v` — PASS.
Run: `python3 -m unittest discover -s tests -q` — PASS.

```bash
git add wave_delivery/setup.py wave_delivery/cli.py tests/test_setup_config.py
git commit -m "feat(next): drive the setup phase from wddctl next"
```

---

### Task 7: `plan apply` adopts the scope into an init-created state

Spec §1: "`plan apply` updates the init-created state instead of creating it."

**Files:**
- Modify: `wave_delivery/plan.py` (`_diff_plan` ~line 150, `_apply_plan_to_state` ~line 177)
- Test: `tests/test_setup_config.py`

**Interfaces:**
- Consumes: Task 3's nullable scope.
- Produces: applying a plan to a `scope: null` state adopts the plan's scope (id, baseRef, maxConcurrent, reviewPolicy, reconcileEveryNMerges) and adds its tasks, while preserving `constitution` (ratification survives), `events`, and `revision` history. The `store.exists()` creation path in `apply_plan` is unchanged — it still covers repos that never ran `init`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_setup_config.py`:

```python
from wave_delivery.plan import apply_plan


def _minimal_plan() -> dict:
    return {
        "schemaVersion": 1,
        "kind": "wdd_plan",
        "scope": {
            "id": "SCOPE-demo",
            "baseRef": None,
            "maxConcurrent": 2,
            "reviewPolicy": "risk_based",
            "reconcileEveryNMerges": 3,
        },
        "tasks": [
            {
                "id": "TASK-001-first",
                "title": "First task",
                "specPath": "tasks/TASK-001-first.md",
                "risk": "normal",
                "dependsOn": [],
                "conflictDomains": ["src/**"],
            }
        ],
    }


class PlanAdoptionTest(unittest.TestCase):
    def test_apply_onto_setup_state_adopts_scope_and_keeps_ratification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            init_repository(wdd, root)
            store = StateStore(wdd / "state.json")
            state = store.read()
            state["constitution"] = {
                "status": "ratified",
                "ratification": {"by": "ivo", "decisionFingerprint": "sha256:abc"},
            }
            store.write(state)
            result = apply_plan(store, _minimal_plan())
            self.assertFalse(result["created"])
            adopted = store.read()
            self.assertEqual(adopted["scope"]["id"], "SCOPE-demo")
            self.assertIn("TASK-001-first", adopted["tasks"])
            self.assertEqual(adopted["constitution"]["status"], "ratified")
```

Note: `read_plan` validates plan files from disk; here we call `apply_plan`
with an already-shaped dict, which follows the existing test suite's pattern.
If `apply_plan` insists on validated input, wrap the dict with
`wave_delivery.plan.validate_plan(_minimal_plan())` in the test.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_setup_config.PlanAdoptionTest -v`
Expected: FAIL — inside `_apply_plan_to_state` / `_diff_plan`, `state["scope"]["id"]` raises `TypeError: 'NoneType' object is not subscriptable`

- [ ] **Step 3: Implement adoption**

In `_diff_plan`, guard the scope comparison at the top:

```python
    if state["scope"] is None:
        scope_changes = dict(plan["scope"])
        return {
            "added": sorted(entry["id"] for entry in plan["tasks"]),
            "removed": [],
            "updated": [],
            "scope": scope_changes,
        }
```

In `_apply_plan_to_state`, before the scope-id mismatch check:

```python
    if state["scope"] is None:
        # An init-created state has no scope yet; the first plan apply adopts
        # it while keeping ratification and event history.
        state["scope"] = {
            "id": plan["scope"]["id"],
            "baseRef": plan["scope"]["baseRef"],
            "maxConcurrent": plan["scope"]["maxConcurrent"],
            "reviewPolicy": plan["scope"]["reviewPolicy"],
        }
        state["reconcile"]["everyNMerges"] = plan["scope"]["reconcileEveryNMerges"]
```

(The task-merging loop below it then adds the tasks; the trailing scope
assignments re-set the same values harmlessly.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_setup_config -v` — PASS.
Run: `python3 -m unittest discover -s tests -q` — PASS (the no-state creation path is untouched).

- [ ] **Step 5: Commit**

```bash
git add wave_delivery/plan.py tests/test_setup_config.py
git commit -m "feat(plan): first apply adopts scope into init-created state"
```

---

### Task 8: Governance drift blocks execution verbs

Spec §2: "Post-ratification config edits without an amend are drift: `doctor` and `next` report it and execution-affecting verbs refuse."

**Files:**
- Modify: `wave_delivery/config.py`
- Modify: `wave_delivery/cli.py`
- Test: `tests/test_setup_config.py`

**Interfaces:**
- Produces: `governance_drift(state: dict, wdd_dir: Path) -> dict | None` — `None` when unratified (nothing signed yet) or fingerprints match; otherwise `{"ratified": "sha256:...", "actual": "sha256:..."}`. Also `require_fresh_governance(state, wdd_dir) -> None` raising `IllegalTransition` on drift. If the governance files are missing entirely (legacy pre-config scope), drift is `None` — legacy scopes keep working until migrated (Task 9).
- CLI: `require_fresh_governance` is called before dispatching these verbs: `start`, `submit`, `review record`, `review collect`, `verify record`, `verify collect`, `refresh`, `merge`, `reconcile done`. Read-only verbs (`status`, `next`, `render`, `freshness check`, `doctor`, `monitor`) never refuse — `next` instead adds a `governance_drift` blocker so the agent sees why nothing will run.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_setup_config.py`:

```python
from wave_delivery.config import governance_drift
from wave_delivery.errors import IllegalTransition


class GovernanceDriftTest(unittest.TestCase):
    def _ratified_repo(self, tmp: str) -> tuple[Path, Path]:
        root = _git_repo(tmp)
        wdd = root / ".wdd"
        init_repository(wdd, root)
        config = load_config(wdd)
        config = set_value(config, "merge.surface", "local")
        config = set_value(config, "verification.commands", ["true"])
        save_config(wdd, config)
        store = StateStore(wdd / "state.json")
        state = store.read()
        state["constitution"] = {
            "status": "ratified",
            "ratification": {
                "by": "ivo",
                "decisionFingerprint": governance_fingerprint(wdd),
            },
        }
        store.write(state)
        return root, wdd

    def test_no_drift_after_ratification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, wdd = self._ratified_repo(tmp)
            state = StateStore(wdd / "state.json").read()
            self.assertIsNone(governance_drift(state, wdd))

    def test_editing_config_after_ratification_is_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, wdd = self._ratified_repo(tmp)
            save_config(wdd, set_value(load_config(wdd), "concurrency.maxConcurrent", 5))
            state = StateStore(wdd / "state.json").read()
            drift = governance_drift(state, wdd)
            self.assertIsNotNone(drift)
            self.assertNotEqual(drift["ratified"], drift["actual"])

    def test_cli_start_refuses_on_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, wdd = self._ratified_repo(tmp)
            store = StateStore(wdd / "state.json")
            apply_plan(store, _minimal_plan())
            save_config(wdd, set_value(load_config(wdd), "concurrency.maxConcurrent", 5))
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main(
                    [
                        "--state", str(wdd / "state.json"),
                        "start", "--task", "TASK-001-first", "--repo", str(root),
                    ]
                )
            self.assertNotEqual(code, 0)
            self.assertIn("drift", stderr.getvalue())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_setup_config.GovernanceDriftTest -v`
Expected: FAIL — `ImportError: cannot import name 'governance_drift'`

- [ ] **Step 3: Implement in `wave_delivery/config.py`**

Append (import `IllegalTransition` from `.errors` at the top):

```python
def governance_drift(state: dict[str, Any], wdd_dir: Path | str) -> dict[str, Any] | None:
    ratification = state["constitution"].get("ratification")
    if state["constitution"]["status"] != "ratified" or not isinstance(ratification, dict):
        return None
    if not config_path(wdd_dir).exists() or not constitution_path(wdd_dir).exists():
        # Legacy scope ratified before the config split existed; migration
        # (wddctl migrate) is the path to the new governance files.
        return None
    actual = governance_fingerprint(wdd_dir)
    ratified = ratification.get("decisionFingerprint")
    if ratified == actual:
        return None
    return {"ratified": ratified, "actual": actual}


def require_fresh_governance(state: dict[str, Any], wdd_dir: Path | str) -> None:
    drift = governance_drift(state, wdd_dir)
    if drift is not None:
        raise IllegalTransition(
            "governance drift: config.json or constitution.md changed since ratification "
            f"(ratified {drift['ratified']}, current {drift['actual']}); "
            "run 'wddctl constitution amend --by NAME' after the user re-approves"
        )
```

- [ ] **Step 4: Gate the CLI verbs**

In `main()`, right after `store = StateStore(args.state)` and the `try:` line,
add a guard set and check (import `require_fresh_governance` from `.config`):

```python
    GOVERNED = {
        ("start", None), ("submit", None), ("refresh", None), ("merge", None),
        ("review", "record"), ("review", "collect"),
        ("verify", "record"), ("verify", "collect"),
        ("reconcile", "done"),
    }
    subcommand = getattr(
        args,
        f"{args.command}_command" if hasattr(args, f"{args.command}_command") else "",
        None,
    )
    if (args.command, subcommand) in GOVERNED and store.exists():
        require_fresh_governance(store.read(), store.path.parent)
```

(Adapt the `subcommand` lookup to the actual attribute names — `review` uses
`args.review_command`, `verify` uses `args.verify_command`, etc.; plain verbs
have none. A small helper keeps it readable.)

In `setup.py`'s `setup_next_actions` no change; in the execute-phase `next`
handler, add the blocker before printing:

```python
            result = bounded_next_actions(...)
            drift = governance_drift(state, store.path.parent)
            if drift is not None:
                result["actions"] = []
                result["blockers"].insert(
                    0,
                    {
                        "code": "governance_drift",
                        "message": "config/constitution changed since ratification; amend before executing",
                        **drift,
                    },
                )
            _print_json(result)
```

- [ ] **Step 5: Run tests, full suite, commit**

Run: `python3 -m unittest tests.test_setup_config -v` — PASS.
Run: `python3 -m unittest discover -s tests -q` — PASS. Existing tests never
have governance files next to their state, so the legacy `None` path keeps
them green — if one fails on the new gate, it is a real integration bug to
fix, not a test to silence.

```bash
git add wave_delivery/config.py wave_delivery/cli.py tests/test_setup_config.py
git commit -m "feat(governance): refuse execution verbs when config or constitution drifted"
```

---

### Task 9: Migrate a legacy `.wdd` to the config split

Spec §9: extract machine values from a JSON-block constitution into `config.json`, rewrite the constitution prose-only, invalidate ratification.

**Files:**
- Modify: `wave_delivery/setup.py`
- Modify: `wave_delivery/cli.py` (extend the `migrate` handler)
- Test: `tests/test_setup_config.py`

**Interfaces:**
- Produces: `migrate_governance(wdd_dir: Path) -> dict`. Behavior:
  - No-op (`{"migrated": False, ...}`) when `config.json` already exists.
  - Otherwise: create `config.json` from defaults; if the old `constitution.md` contains a ` ```json ` fenced block that parses and has a `models` object, copy `models.planning` and `models.review` verbatim and put the old flat `models.implementation` string into `models.implementation.default`; add the standard open questions (`merge.surface` always — this is a new decision the old constitution never made); back up the old constitution to `constitution.md.pre-config` and write the new prose template; set `constitution.status` back to `"draft"` with `ratification: None` in state (via `StateStore`), because the fingerprint's meaning changed and the user must re-approve once.
  - CLI: `wddctl migrate --governance --apply|--dry-run` (dry-run reports what would change without writing).
- Consumes: Task 1 config functions, Task 5's `CONSTITUTION_TEMPLATE`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_setup_config.py`:

```python
from wave_delivery.setup import migrate_governance

LEGACY_CONSTITUTION = """---
id: WDD-CONSTITUTION
kind: constitution
---

# Project Constitution

## Model aliases

```json
{"models": {"planning": "model-a", "implementation": "model-b", "review": "model-c"}}
```

## Merge policy

- Merge mode: controller-merges-automatically
"""


class GovernanceMigrationTest(unittest.TestCase):
    def test_extracts_models_and_invalidates_ratification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            wdd.mkdir()
            (wdd / "constitution.md").write_text(LEGACY_CONSTITUTION, encoding="utf-8")
            state = new_state("SCOPE-legacy", base_ref="wdd/legacy")
            state["constitution"] = {
                "status": "ratified",
                "ratification": {"by": "ivo", "decisionFingerprint": "sha256:old"},
            }
            StateStore(wdd / "state.json").write(state)
            result = migrate_governance(wdd)
            self.assertTrue(result["migrated"])
            config = load_config(wdd)
            self.assertEqual(config["models"]["planning"], "model-a")
            self.assertEqual(config["models"]["implementation"]["default"], "model-b")
            self.assertEqual(config["models"]["review"], "model-c")
            self.assertTrue((wdd / "constitution.md.pre-config").is_file())
            migrated_state = StateStore(wdd / "state.json").read()
            self.assertEqual(migrated_state["constitution"]["status"], "draft")

    def test_noop_when_config_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            init_repository(wdd, root)
            result = migrate_governance(wdd)
            self.assertFalse(result["migrated"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_setup_config.GovernanceMigrationTest -v`
Expected: FAIL — `ImportError: cannot import name 'migrate_governance'`

- [ ] **Step 3: Implement in `wave_delivery/setup.py`**

```python
import json  # top of file
import re


_JSON_BLOCK = re.compile(r"```json\s*\n(.*?)\n\s*```", re.DOTALL)


def _legacy_models(constitution_text: str) -> dict[str, Any] | None:
    for match in _JSON_BLOCK.finditer(constitution_text):
        try:
            parsed = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        models = parsed.get("models") if isinstance(parsed, dict) else None
        if isinstance(models, dict):
            return models
    return None


def migrate_governance(wdd_dir: Path | str) -> dict[str, Any]:
    """One-time conversion of a pre-split .wdd to config.json + prose constitution.

    Ratification is deliberately invalidated: the fingerprint's meaning
    changed (it now signs config.json too), so the user must see and approve
    the new split once.
    """
    wdd_dir = Path(wdd_dir)
    if config_path(wdd_dir).exists():
        return {"migrated": False, "reason": "config.json already exists"}
    constitution_file = constitution_path(wdd_dir)
    old_text = (
        constitution_file.read_text(encoding="utf-8") if constitution_file.exists() else ""
    )

    config = default_config()
    legacy = _legacy_models(old_text)
    if legacy:
        for key in ("planning", "review"):
            value = legacy.get(key)
            if isinstance(value, str) and value:
                config["models"][key] = value
        implementation = legacy.get("implementation")
        if isinstance(implementation, str) and implementation:
            config["models"]["implementation"]["default"] = implementation
    config["openQuestions"] = _open_questions(config["verification"]["commands"])
    save_config(wdd_dir, config)

    if old_text:
        atomic_write_text(wdd_dir / "constitution.md.pre-config", old_text)
    atomic_write_text(constitution_file, CONSTITUTION_TEMPLATE)

    store = StateStore(wdd_dir / "state.json")
    invalidated = False
    if store.exists():
        state = store.read()
        if state["constitution"]["status"] == "ratified":
            state["constitution"] = {"status": "draft", "ratification": None}
            store.write(state)
            invalidated = True
    return {
        "migrated": True,
        "ratificationInvalidated": invalidated,
        "modelsExtracted": bool(legacy),
        "backup": str(wdd_dir / "constitution.md.pre-config") if old_text else None,
    }
```

- [ ] **Step 4: Wire the CLI**

In `build_parser()`, on the existing `migrate` parser add:

```python
    migrate.add_argument(
        "--governance",
        action="store_true",
        help="split a legacy constitution into config.json + prose (invalidates ratification)",
    )
```

In the `migrate` handler in `main()`, before the schema-migration dispatch:

```python
        if args.command == "migrate" and args.governance:
            if args.apply == args.dry_run:
                parser.error("choose exactly one of --dry-run or --apply")
            wdd_dir = store.path.parent
            if args.dry_run:
                from .config import config_path as _config_path
                _print_json(
                    {
                        "wouldMigrate": not _config_path(wdd_dir).exists(),
                        "wddDir": str(wdd_dir),
                    }
                )
                return 0
            _print_json(migrate_governance(wdd_dir))
            return 0
```

(Import `migrate_governance` from `.setup` at the top.)

- [ ] **Step 5: Run tests, full suite, commit**

Run: `python3 -m unittest tests.test_setup_config -v` — PASS.
Run: `python3 -m unittest discover -s tests -q` — PASS.

```bash
git add wave_delivery/setup.py wave_delivery/cli.py tests/test_setup_config.py
git commit -m "feat(migrate): split legacy constitutions into config.json plus prose"
```

---

### Task 10: End-to-end test — init to first admitted task

Spec §8: "End-to-end test for `init` on a scratch repo: init → `config set` → ratify → `plan apply` → `next` reaches `start_task`."

**Files:**
- Test: `tests/test_setup_config.py`

**Interfaces:**
- Consumes: everything above, exclusively through `main([...])` — this test is the executable form of the onboarding flow the skills describe.

- [ ] **Step 1: Write the test (it should pass immediately if Tasks 1–9 are correct — treat a failure as a real integration bug)**

Append to `tests/test_setup_config.py`:

```python
class EndToEndSetupTest(unittest.TestCase):
    def _cli(self, state: str, *argv: str) -> dict:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(["--state", state, *argv])
        self.assertEqual(code, 0, f"wddctl {' '.join(argv)} failed")
        return json.loads(stdout.getvalue())

    def test_full_setup_reaches_start_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            (root / "README.md").write_text("seed\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed"],
                cwd=root, check=True,
            )
            wdd = root / ".wdd"
            state = str(wdd / "state.json")

            payload = self._cli(state, "init", "--repo", str(root))
            self.assertFalse(payload["alreadyInitialized"])

            payload = self._cli(state, "next")
            self.assertEqual(payload["actions"][0]["action"], "resolve_config")

            self._cli(state, "config", "set", "merge.surface", "local")
            self._cli(state, "config", "set", "verification.commands", '["true"]')

            payload = self._cli(state, "next")
            self.assertEqual(payload["actions"][0]["action"], "ratify")

            self._cli(state, "constitution", "ratify", "--by", "test")

            payload = self._cli(state, "next")
            self.assertEqual(payload["actions"][0]["action"], "plan")

            plan_file = root / "plan.json"
            plan = _minimal_plan()
            plan["scope"]["baseRef"] = "wdd/demo"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            (wdd / "tasks").mkdir(exist_ok=True)
            (wdd / "tasks" / "TASK-001-first.md").write_text("# Brief\n", encoding="utf-8")
            self._cli(state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root))

            payload = self._cli(state, "next", "--repo", str(root))
            actions = [action["action"] for action in payload["actions"]]
            self.assertIn("start_task", actions)
```

Note: check the actual `plan apply` / `next` flag names in `build_parser()`
(`--repo` placement) and adjust; the assertions are the contract, the flag
spelling follows the CLI as built.

- [ ] **Step 2: Run the test**

Run: `python3 -m unittest tests.test_setup_config.EndToEndSetupTest -v`
Expected: PASS. If it fails, debug the integration (most likely candidates: ratify's fingerprint check when constitution template changed, or `plan apply` flag names) — do not weaken the assertions.

- [ ] **Step 3: Run the full suite, commit**

Run: `python3 -m unittest discover -s tests -q` — PASS.

```bash
git add tests/test_setup_config.py
git commit -m "test(setup): end-to-end onboarding from init to first admitted task"
```

---

### Task 11: Skills — `wdd-setup` replaces `wdd-constitution`, router points at init

Spec §1 ("router skill's first line"), §7 partially (full skill-pack rewrite is the phase-3 plan; this task ships only what the new CLI flow requires so the skills and the binary never disagree).

**Files:**
- Create: `skills/wdd-setup/SKILL.md`
- Delete: `skills/wdd-constitution/` (directory, including `templates/constitution.md` — the template now lives in `wave_delivery/setup.py`)
- Modify: `skills/wave-driven-development/SKILL.md`

**Interfaces:**
- Consumes: the CLI behavior of Tasks 5–8 — every command named in the skill must exist and be spelled exactly as the CLI accepts it.

- [ ] **Step 1: Write `skills/wdd-setup/SKILL.md`**

```markdown
---
name: wdd-setup
description: Initialize WDD in a repository — run wddctl init, resolve the open config questions with the user in one round, and ratify the constitution + config. Replaces the old wdd-constitution skill. Use when .wdd/state.json is missing, when open config questions remain, or when governance needs amending.
---

# WDD Setup

You run every `wddctl` command in this skill yourself. Presenting a command
to the user instead of executing it is a protocol violation.

## Fresh repository

1. Run `wddctl init --repo .`. It scaffolds `.wdd/` deterministically:
   `config.json` with probed defaults plus an `openQuestions` list,
   a prose `constitution.md` draft, `tasks/`, `shared-context/`, and
   `state.json`. Re-running it is a safe no-op.
2. Run `wddctl next` and do what it says. During setup it emits exactly one
   action at a time:
   - `resolve_config` — ask the user every listed question in ONE compact
     round (not one message per question), then record each answer with
     `wddctl config set <path> <value>`. Values are JSON when structured
     (`'["pytest -q"]'`), bare strings otherwise (`local`).
   - `ratify` — show the user the current `config.json` values and the
     `constitution.md` text (summarize, link the files), get explicit
     sign-off, then run `wddctl constitution ratify --by <name>`. Never
     ratify content the user has not seen.
   - `plan` — setup is done; switch to the `wdd-plan` skill.
3. While resolving questions, also fill the constitution's prose sections
   (Intent, reviewer focus) from what you know of the project — it is prose
   for judgment, never configuration. All machine knobs go in `config.json`.

## Amending later

Config and constitution are fingerprint-bound to ratification. After any
edit, execution verbs refuse with a governance-drift error until you get the
user's explicit re-approval of the change and run
`wddctl constitution amend --by <name>`.

## Legacy repositories

A `.wdd/` from before the config split (constitution containing a JSON
models block, no `config.json`) is converted with
`wddctl migrate --governance --apply`. This backs up the old constitution,
extracts the model aliases into `config.json`, and deliberately invalidates
ratification — walk the user through re-approval as for a fresh setup.

## Done when

- `wddctl next` no longer emits `resolve_config` or `ratify`.
- The user has explicitly approved what was ratified.
```

- [ ] **Step 2: Update the router skill**

In `skills/wave-driven-development/SKILL.md`:

1. In "The loop" / judgment list, replace the line
   `- No \`.wdd/constitution.md\`, or it isn't ratified: use \`wdd-constitution\`.`
   with:
   `- No \`.wdd/state.json\`: run \`wddctl init --repo .\`, then follow \`wdd-setup\`.`
   `- Open config questions or an unratified constitution: use \`wdd-setup\`.`
2. In the artifact-layout block, add `config.json     # machine config; edit via wddctl config set` under `constitution.md`, and change the `constitution.md` comment to `# human-authored prose governance`.
3. Anywhere the text says the agent might show commands to the user, tighten to: the agent executes `wddctl` itself.

- [ ] **Step 3: Delete the old skill**

```bash
git rm -r skills/wdd-constitution
```

Then grep for stragglers: `grep -rn "wdd-constitution" skills/ docs/ README.md` — update every hit to `wdd-setup` (README's install/quickstart lists skills by name).

- [ ] **Step 4: Verify and commit**

Run: `python3 -m unittest discover -s tests -q` — PASS (skills are prose; the suite proves the commands the skill names still exist — spot-check by rereading the skill against `wddctl --help` output).

```bash
git add skills/wdd-setup/SKILL.md skills/wave-driven-development/SKILL.md README.md docs/
git commit -m "feat(skills): replace wdd-constitution with init-driven wdd-setup"
```

---

### Task 12: Documentation — init and config in wddctl.md and README

**Files:**
- Modify: `docs/wddctl.md` (add `init` and `config` verb sections, update `constitution` section for the new fingerprint contract)
- Modify: `README.md` (Quickstart: replace steps 1–3 with the init flow)

**Interfaces:**
- Consumes: the exact CLI output shapes from Tasks 5, 6, 2, 4 — paste real output from a scratch run, per the repo's documented convention ("nothing here is paraphrased output").

- [ ] **Step 1: Generate real transcripts**

In a scratch directory, run the full flow from Task 10 by hand (`init`, `next`, `config set`, `next`, `constitution ratify`, `next`) and capture the literal JSON outputs.

- [ ] **Step 2: Update `docs/wddctl.md`**

Add sections for `init` (what it creates, idempotency, the open-questions contract) and `config` (`get`/`set`/`show`, dotted paths, JSON-or-bare-string values, open-question resolution), and rewrite the `constitution` section: fingerprint now covers `config.json` + `constitution.md`, `ratify` needs only `--by`, `--proposal` is deprecated, drift refuses execution verbs until `amend`. Paste the captured transcripts.

- [ ] **Step 3: Update `README.md` Quickstart**

Replace the current steps 1–3 (hand-write plan → apply → probe/ratify) with:

```markdown
1. Initialize. This scaffolds `.wdd/` — machine config with probed defaults,
   a prose constitution draft, and controller state:

   ```sh
   wddctl init --repo .
   ```

2. Follow the controller. `wddctl next` names each remaining setup step —
   resolve the open config questions (`wddctl config set merge.surface pr`),
   then ratify:

   ```sh
   wddctl next
   wddctl constitution ratify --by "your-name"
   ```

3. Plan. Write `plan.json` and the task briefs (see `skills/wdd-plan`), then:

   ```sh
   wddctl plan apply --plan plan.json --repo .
   ```

4. Run the loop:
```

(keeping the existing step-4 loop text).

- [ ] **Step 4: Commit**

```bash
git add docs/wddctl.md README.md
git commit -m "docs: document init, config, and the new ratification contract"
```

---

## Self-review checklist (run after Task 12)

- Spec §1 (init, setup phase, next-driven onboarding): Tasks 3, 5, 6, 7.
- Spec §2 (config.json, get/set, joint fingerprint, drift, ratify gate): Tasks 1, 2, 4, 8.
- Spec §9 (legacy migration): Task 9 (+ Task 3 for schema v3→v4).
- Spec §8 (E2E test): Task 10. Stub-`gh` testing belongs to the phase-4 plan (PR surface) — not here.
- Spec §7 (skills): only the setup-critical slice (Task 11); the full agent-first rewrite is the phase-3 plan.
- Deliberately NOT in this plan (later phases): riskRules consumption + plan lint (phase 2), intake flow + `--approved-by` (phase 3), model dispatch + mergeSurface behavior + human-merge observation (phase 4), finalize phase + `delivered` (phase 5). `config.json` already carries their knobs so those phases add no config migrations.
