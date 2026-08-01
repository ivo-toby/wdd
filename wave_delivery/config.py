"""Machine-consumed configuration for a WDD repository (.wdd/config.json).

The constitution stays prose; every knob wddctl or a dispatching agent
mechanically consumes lives here. Validation is hand-rolled like schema.py:
this package has no runtime dependencies and keeps it that way.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .errors import IllegalTransition, ValidationError
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
    value = models.get("planning")
    _require(value is None or (isinstance(value, str) and value),
             "models.planning must be null or a non-empty string")
    implementation = models.get("implementation")
    _require(isinstance(implementation, dict), "models.implementation must be an object")
    for key in ("default", "highRisk"):
        value = implementation.get(key)
        _require(value is None or (isinstance(value, str) and value),
                 f"models.implementation.{key} must be null or a non-empty string")
    # review stays tierable like implementation: a plain string means both
    # tiers (regression-pinned -- spec Sec3), an object form tiers by task
    # risk the same way implementation does. Object shape validation matches
    # implementation's strictness: default/highRisk must each be null or a
    # non-empty string; unknown extra keys are not rejected (implementation
    # doesn't reject them either).
    review = models.get("review")
    if isinstance(review, dict):
        for key in ("default", "highRisk"):
            value = review.get(key)
            _require(value is None or (isinstance(value, str) and value),
                     f"models.review.{key} must be null or a non-empty string")
    else:
        _require(review is None or (isinstance(review, str) and review),
                 "models.review must be null, a non-empty string, or an object")

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


def get_value(config: dict[str, Any], dotted: str) -> Any:
    node: Any = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ValidationError(f"config: unknown path '{dotted}'")
        node = node[part]
    return node


def set_value(config: dict[str, Any], dotted: str, value: Any) -> dict[str, Any]:
    if dotted == "openQuestions" or dotted.startswith("openQuestions."):
        raise ValidationError(
            "config: openQuestions cannot be set directly (it would silently drop the "
            "ratify gate); resolve each question by setting the path it names instead"
        )
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


def governance_drift(state: dict[str, Any], wdd_dir: Path | str) -> dict[str, Any] | None:
    """Detect config/constitution edits made after ratification without an amend.

    None covers two no-op cases: unratified (nothing signed yet), and legacy
    scopes that predate the config split (no config.json to fingerprint —
    migration, not drift detection, is their path forward; see Task 9). Once
    config.json exists, a missing constitution.md is NOT a legacy no-op: it
    is drift (someone deleted the ratified prose), reported with a synthetic
    "missing:constitution.md" actual fingerprint so the gate still fires.
    """
    ratification = state["constitution"].get("ratification")
    if state["constitution"]["status"] != "ratified" or not isinstance(ratification, dict):
        return None
    if not config_path(wdd_dir).exists():
        return None
    ratified = ratification.get("decisionFingerprint")
    if not constitution_path(wdd_dir).exists():
        return {"ratified": ratified, "actual": "missing:constitution.md"}
    actual = governance_fingerprint(wdd_dir)
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


def merge_settings(state: dict[str, Any] | None, config: dict[str, Any] | None) -> dict[str, str]:
    """Effective merge surface/mode for a scope: scope override wins, else config.

    config=None models a legacy repo predating config.json; it defaults to
    {"surface": "local", "mode": "controller"} to preserve legacy behavior.
    A scope-level override (state["scope"]["mergeSurface"/"mergeMode"]) still
    applies on top of either source when present.
    """
    if config is None:
        surface, mode = "local", "controller"
    else:
        surface = config["merge"]["surface"]
        mode = config["merge"]["mode"]
    scope = (state or {}).get("scope") or {}
    surface = scope.get("mergeSurface", surface)
    mode = scope.get("mergeMode", mode)
    return {"surface": surface, "mode": mode}


def check_ratifiable(wdd_dir: Path | str) -> None:
    config = load_config(wdd_dir)
    open_questions = config["openQuestions"]
    if open_questions:
        paths = ", ".join(question["path"] for question in open_questions)
        raise ValidationError(
            f"cannot ratify: {len(open_questions)} open config question(s) remain ({paths}); "
            "resolve them with 'wddctl config set' first"
        )
