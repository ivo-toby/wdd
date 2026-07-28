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
