"""Machine-consumed configuration for a WDD repository (.wdd/config.json).

The constitution stays prose; every knob wddctl or a dispatching agent
mechanically consumes lives here. Validation is hand-rolled like schema.py:
this package has no runtime dependencies and keeps it that way.
"""

from __future__ import annotations

import hashlib
import json
import math
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
    "verification": {"commands": [], "unavailableJustification": None, "timeoutSeconds": 600},
    "review": {"policy": "risk_based", "blockingSeverities": ["P1", "P2"]},
    "merge": {"surface": "pr", "mode": "controller", "reconcileEveryNMerges": 3},
    "concurrency": {"maxConcurrent": 3},
    "models": {
        "planning": None,
        "implementation": {"default": None, "highRisk": None},
        "review": None,
        "specReview": None,
    },
    "riskRules": [],
    "taskProvider": {"type": "local"},
    "runners": {},
    "worktrees": {"root": ".worktrees"},
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


def _parse_strict_json(text: str, *, context: str) -> Any:
    """The one JSON parse used for every WDD-authored config file (global
    `config.json` AND an epic overlay's `config.json`): duplicate object
    keys and non-finite number literals (NaN/Infinity/-Infinity) are
    rejected rather than silently accepted by Python's `json` module --
    the same byte-precision `effective_config_digest` demands of what it
    hashes. `context` names what is being parsed (typically the file path)
    so the raised `ValidationError` can name both the file and the
    offending key/literal. Still raises the stdlib's own
    `json.JSONDecodeError` for ordinary malformed JSON; callers translate
    that into a `ValidationError` themselves (they know the right wording
    for "not valid JSON" in their context).
    """

    def _reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: dict[str, Any] = {}
        for key, value in pairs:
            if key in seen:
                raise ValidationError(f"{context}: duplicate JSON key {key!r}")
            seen[key] = value
        return seen

    def _reject_constant(name: str) -> Any:
        raise ValidationError(f"{context}: non-finite number literal {name!r} is not allowed")

    return json.loads(text, object_pairs_hook=_reject_duplicate, parse_constant=_reject_constant)


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
    # Per-command timeout for `--run` (spec Sec1, machine-verification epic).
    # Absent is tolerated like `runners`/`worktrees` above -- a real
    # pre-existing config.json written before this key existed has no
    # legitimate way to supply it, and refusing it outright would brick
    # every one of them with no in-tool remedy (fix-round: bug found against
    # this repo's own live config). A PRESENT value is still held strictly:
    # only an absent key falls back to the DEFAULT_CONFIG value (600) for
    # this check; `_hydrate_optional_sections` is what actually writes 600
    # into `effective` so downstream readers never see the key missing.
    timeout_seconds = verification.get("timeoutSeconds", DEFAULT_CONFIG["verification"]["timeoutSeconds"])
    _require(
        isinstance(timeout_seconds, int) and not isinstance(timeout_seconds, bool)
        and 1 <= timeout_seconds <= 86400,
        "verification.timeoutSeconds must be an integer between 1 and 86400",
    )

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

    # models.specReview (spec Sec1 "`models.specReview` -- routing the
    # reviewer"): a default reviewer CANDIDATE for spec/design/plan review
    # rounds, mirroring models.planning's shape exactly -- string or null,
    # no tier object. Unlike models.review, spec review is explicitly never
    # risk-tiered (there is no per-task risk at spec-review time), so no
    # object form is accepted here.
    spec_review = models.get("specReview")
    _require(spec_review is None or (isinstance(spec_review, str) and spec_review),
             "models.specReview must be null or a non-empty string")

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

    # Optional (spec Sec6): absent entirely on configs that predate the
    # runner registry -- backward compatible, unlike every required section
    # above. name -> {"command": [str, ...]} non-empty argv; placeholders
    # ({worktree}/{prompt}/{logfile}) are a runner.py concern, not validated
    # here (any non-empty string is a legal argv element).
    runners = config.get("runners")
    if runners is not None:
        _require(isinstance(runners, dict), "runners must be an object")
        for name, entry in runners.items():
            _require(isinstance(name, str) and name, "runners keys must be non-empty strings")
            _require(isinstance(entry, dict), f"runners.{name} must be an object")
            command = entry.get("command")
            _require(
                isinstance(command, list) and command
                and all(isinstance(item, str) and item for item in command),
                f"runners.{name}.command must be a non-empty list of non-empty strings",
            )

    # Optional, like runners above: absent entirely on configs that predate
    # this key -- backward compatible with a config.json written before
    # worktrees.root existed. When present, root must be a non-empty string;
    # git.py resolves it (relative against the repo root, absolute as-is).
    worktrees = config.get("worktrees")
    if worktrees is not None:
        _require(isinstance(worktrees, dict), "worktrees must be an object")
        _require(isinstance(worktrees.get("root"), str) and worktrees["root"],
                 "worktrees.root must be a non-empty string")

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
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ValidationError(f"config file does not exist: {path}; run 'wddctl init'") from error
    try:
        config = _parse_strict_json(text, context=f"config file ({path})")
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


# ---------------------------------------------------------------------------
# Epic configuration overlay (epic-scoped-state plan, Task 2, spec Sec2).
#
# `epics/<slug>/config.json` is a sparse overlay: only overridden dotted
# LEAVES are present. The allowlist below is the exact granularity of
# override -- e.g. `models.implementation` is one leaf (the whole
# {default, highRisk} object), not two ("models.implementation.default"
# and "models.implementation.highRisk" are NOT independently overridable).
# Overriding a leaf replaces it atomically; it is not a deep-merge below
# the leaf boundary. Any key outside this set is rejected BY NAME at every
# entry point: overlay load, `config set --epic`, and (Task 5) `intake
# configure` approval -- this module enforces the first two; Task 5 wires
# the third through `derive_effective`, already exposed here.
# ---------------------------------------------------------------------------

OVERLAY_ALLOWED_LEAVES: tuple[str, ...] = (
    "models.planning",
    "models.implementation",
    "models.review",
    "models.specReview",
    "verification.commands",
    "verification.unavailableJustification",
    "verification.timeoutSeconds",
    "merge.surface",
    "riskRules",
    "review.policy",
)

_ALLOWED_LEAVES = frozenset(OVERLAY_ALLOWED_LEAVES)
_ALLOWED_LEAF_PARTS: tuple[tuple[str, ...], ...] = tuple(
    tuple(leaf.split(".")) for leaf in OVERLAY_ALLOWED_LEAVES
)
_ALLOWED_PREFIXES = frozenset(
    ".".join(parts[:index])
    for parts in _ALLOWED_LEAF_PARTS
    for index in range(1, len(parts))
)

# Purposes `project()` accepts (spec Sec2's named projections).
PROJECTION_PURPOSES: tuple[str, ...] = (
    "plan",
    "taskReview",
    "finalReview",
    "taskVerification",
    "finalVerification",
)


def _collect_leaf_paths(node: Any, prefix: str) -> list[str]:
    """All dotted leaf paths under `node`, for naming a rejected key by its
    most specific offending path(s) (e.g. 'worktrees.root', not just
    'worktrees')."""
    if isinstance(node, dict) and node:
        paths: list[str] = []
        for key in sorted(node):
            dotted = f"{prefix}.{key}" if prefix else key
            paths.extend(_collect_leaf_paths(node[key], dotted))
        return paths
    return [prefix]


def _check_overlay_allowlist(overlay: dict[str, Any], prefix: str = "") -> None:
    for key in sorted(overlay):
        value = overlay[key]
        dotted = f"{prefix}.{key}" if prefix else key
        if dotted in _ALLOWED_LEAVES:
            # Reaching an allowed leaf stops the walk here -- keys INSIDE an
            # object-shaped leaf (e.g. an unknown key nested in
            # `models.review`) are not further checked by name. This
            # mirrors `validate_config`'s own documented tolerance for
            # `models.review`'s object form (see its comment: "unknown
            # extra keys are not rejected"); deliberate, not an oversight
            # (pinned by OverlayAllowlistLaxnessInsideObjectLeafTest in
            # tests/test_epics.py so a future tightening of either
            # validator is a conscious, visible change).
            continue
        if dotted in _ALLOWED_PREFIXES and isinstance(value, dict):
            _check_overlay_allowlist(value, dotted)
            continue
        offending = ", ".join(_collect_leaf_paths(value, dotted))
        raise ValidationError(
            f"epic config overlay: key(s) not in the allowed overlay set "
            f"({', '.join(OVERLAY_ALLOWED_LEAVES)}): {offending}"
        )


def _leaf_present(node: dict[str, Any], parts: tuple[str, ...]) -> bool:
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def _leaf_get(node: dict[str, Any], parts: tuple[str, ...]) -> Any:
    for part in parts:
        node = node[part]
    return node


def _leaf_set(node: dict[str, Any], parts: tuple[str, ...], value: Any) -> None:
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _apply_leaves(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Leaf-atomic overlay application: every allowed leaf present in
    `overlay` replaces that whole leaf's value in a copy of `base`; a leaf
    absent from `overlay` retains `base`'s value untouched. This is what
    makes override removal derivable (spec Sec2) -- dropping a leaf from
    the overlay reveals the retained `base` value at the next call.
    """
    result = deepcopy(base)
    for parts in _ALLOWED_LEAF_PARTS:
        if _leaf_present(overlay, parts):
            _leaf_set(result, parts, deepcopy(_leaf_get(overlay, parts)))
    return result


# Top-level sections `validate_config` treats as optional for backward
# compatibility with a config.json written before they existed (see its
# `runners`/`worktrees` comments) -- never overlay-allowed themselves.
_OPTIONAL_GLOBAL_SECTIONS: tuple[str, ...] = ("runners", "worktrees")


def _hydrate_optional_sections(config: dict[str, Any]) -> dict[str, Any]:
    """Copy of `config` with any of `_OPTIONAL_GLOBAL_SECTIONS` missing from
    it filled in from `default_config()`. A legacy config predating one of
    these sections is a valid `global` layer (`validate_config` allows the
    omission) -- but `effective` must resolve to a concrete value for every
    known path, not just carry the name for `resolve_config_source`'s
    'default' tier to report while crashing on the value lookup (fix-round
    F1). `global` itself is deliberately left un-hydrated by every caller
    of this function -- hydrating it too would make `resolve_config_source`
    find these keys in `global` and report source='global' instead of the
    correct 'default'.

    Also hydrates `verification.timeoutSeconds` when the key itself is
    missing, one leaf deep inside the otherwise-required `verification`
    object rather than a whole top-level section: `validate_config` accepts
    its absence for the identical backward-compat reason `runners`/
    `worktrees` are accepted absent, and `effective` needs the same
    concrete default (600) here for the same reason (fix-round: a real
    pre-existing config.json lacking this key must still resolve, not
    crash). A present-but-invalid value is never touched here --
    `validate_config` still refuses it by name.
    """
    hydrated = deepcopy(config)
    defaults = DEFAULT_CONFIG
    for section in _OPTIONAL_GLOBAL_SECTIONS:
        if section not in hydrated:
            hydrated[section] = deepcopy(defaults[section])
    verification = hydrated.get("verification")
    if isinstance(verification, dict) and "timeoutSeconds" not in verification:
        verification["timeoutSeconds"] = defaults["verification"]["timeoutSeconds"]
    return hydrated


def _effective_view(global_config: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """The ONE place `effective` is computed from a `global` layer and an
    overlay -- used by both `load_layers` and `derive_effective` so the two
    can never drift apart. Hydrates missing optional top-level sections
    (see `_hydrate_optional_sections`) onto `global_config` first, then
    applies the overlay's leaves on top (fix-round F1).
    """
    return _apply_leaves(_hydrate_optional_sections(global_config), overlay)


def validate_overlay(overlay: Any) -> None:
    """The loader's validators for an epic overlay (spec Sec2): allowlist
    membership by name, then value-shape validity via the same
    `validate_config` every global config value is held to (hydrated onto
    the built-in defaults so only the touched leaves are exercised).
    `derive_effective` runs this same function on every patch, so no
    overlay can reach approval that loading would reject.
    """
    _require(isinstance(overlay, dict), "epic overlay must be an object")
    _check_overlay_allowlist(overlay)
    hydrated = _apply_leaves(default_config(), overlay)
    validate_config(hydrated)


def epic_overlay_path(wdd_dir: Path | str, epic: str) -> Path:
    return Path(wdd_dir) / "epics" / epic / "config.json"


def load_overlay(wdd_dir: Path | str, epic: str | None) -> dict[str, Any]:
    """The sparse overlay for `epic`, or `{}` for no active epic or a
    missing overlay file (spec Sec2: "Missing overlay file = empty
    overlay"). Parsed via `_parse_strict_json` -- duplicate JSON object
    keys and non-finite number literals are rejected at parse, the same
    byte-precision the digest function demands (spec Sec2); `load_config`
    parses the global layer through the same helper (fix-round F3).
    """
    if epic is None:
        return {}
    path = epic_overlay_path(wdd_dir, epic)
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValidationError(f"epic overlay could not be read: {path}: {error}") from error
    try:
        overlay = _parse_strict_json(text, context=f"epic overlay ({path})")
    except json.JSONDecodeError as error:
        raise ValidationError(f"epic overlay is not valid JSON: {path}: {error}") from error
    validate_overlay(overlay)
    return overlay


def save_overlay(wdd_dir: Path | str, epic: str, overlay: dict[str, Any]) -> None:
    validate_overlay(overlay)
    atomic_write_text(
        epic_overlay_path(wdd_dir, epic),
        json.dumps(overlay, indent=2, sort_keys=True) + "\n",
    )


def set_overlay_value(
    overlay: dict[str, Any],
    dotted: str,
    value: Any,
    *,
    effective: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a new sparse overlay with `dotted` set to `value`, used by
    `config set --epic`. Unlike `set_value` (which requires the path to
    already exist in a fully-hydrated config), the overlay is sparse:
    intermediate objects along `dotted` are created as needed. Allowlist
    and value-shape validation are NOT done here -- every overlay mutation
    goes through `derive_effective`, which validates the resulting overlay
    before it is treated as approved.

    Sub-leaf seeding (fix-round F2): when `dotted` targets a path STRICTLY
    BELOW one of the allowlisted leaves (e.g. `models.implementation.default`,
    below the `models.implementation` leaf), the overlay's leaf-atomic apply
    (`_apply_leaves`) will later replace that WHOLE leaf with whatever this
    function builds -- so building a bare `{"default": value}` here would
    silently null out sibling keys (e.g. `highRisk`) the caller never
    touched. Instead, if the leaf isn't already present in `overlay` (a
    prior sub-leaf set on the same leaf already seeded it -- don't reseed
    over it), the new leaf is seeded from a deep copy of `effective`'s
    CURRENT value at that leaf before the sub-key is set, so untouched
    siblings are preserved from wherever they were effective (epic, global,
    or default). `effective` is optional and only consulted for below-leaf
    paths; callers that only ever set exact-leaf paths (the CLI's normal
    case for e.g. `merge.surface`) never need it.
    """
    updated = deepcopy(overlay)
    parts = tuple(dotted.split("."))
    for leaf_parts in _ALLOWED_LEAF_PARTS:
        depth = len(leaf_parts)
        if len(parts) > depth and parts[:depth] == leaf_parts and not _leaf_present(
            updated, leaf_parts
        ):
            seed = deepcopy(_leaf_get(effective, leaf_parts)) if effective is not None else {}
            _leaf_set(updated, leaf_parts, seed)
            break
    node = updated
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value
    return updated


def load_layers(wdd_dir: Path | str, epic: str | None) -> dict[str, Any]:
    """The admission snapshot (spec Sec2): `{defaults, global, overlay,
    effective}`, each layer validated at capture. Resolution per key path
    is `epic overlay -> global config.json -> built-in default`; a legacy
    `global` missing an optional section (`runners`, `worktrees` -- neither
    is overlay-allowed) is hydrated onto `effective` from the built-in
    default by `_effective_view` (fix-round F1), so `resolve_config_source`'s
    'default' tier has a concrete value to return, not just a name. `global`
    itself is returned un-hydrated -- that's what lets the 'default' tier
    be distinguished from 'global' in the first place.
    """
    wdd_dir = Path(wdd_dir)
    defaults = default_config()
    global_config = load_config(wdd_dir)
    overlay = load_overlay(wdd_dir, epic)
    effective = _effective_view(global_config, overlay)
    validate_config(effective)
    return {
        "defaults": defaults,
        "global": global_config,
        "overlay": overlay,
        "effective": effective,
    }


def derive_effective(layers: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Pure: `patch` is the new overlay layer in full (a replacement, not a
    merge-patch -- `--use-defaults` is `derive_effective(snapshot, {})`,
    spec Sec2). Revalidates with `validate_overlay` (the loader's exact
    validators) and recomputes `effective` from the retained
    `global`/`defaults` layers -- dropping a previously-overridden leaf
    from `patch` reveals the retained `global` value (the masking /
    removal-reveals-global pin). Never touches disk.
    """
    validate_overlay(patch)
    effective = _effective_view(layers["global"], patch)
    validate_config(effective)
    return {
        "defaults": layers["defaults"],
        "global": layers["global"],
        "overlay": deepcopy(patch),
        "effective": effective,
    }


def resolve_config_source(layers: dict[str, Any], dotted: str) -> tuple[Any, str]:
    """The per-key `source` marker for `config get --epic` (spec Sec2):
    'epic' when `dotted` is covered by an overlaid leaf (the leaf itself,
    or a path below it), else 'global' when it resolves in the global
    config, else 'default'. Source markers are a presentation concern
    only -- they never enter any digest.
    """
    parts = tuple(dotted.split("."))
    overridden = any(
        _leaf_present(layers["overlay"], leaf_parts) and parts[: len(leaf_parts)] == leaf_parts
        for leaf_parts in _ALLOWED_LEAF_PARTS
    )
    if overridden:
        source = "epic"
    else:
        try:
            get_value(layers["global"], dotted)
            source = "global"
        except ValidationError:
            get_value(layers["defaults"], dotted)  # raises if unknown everywhere
            source = "default"
    return get_value(layers["effective"], dotted), source


def _reject_non_finite(node: Any, path: str = "$") -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            _reject_non_finite(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _reject_non_finite(value, f"{path}[{index}]")
    elif isinstance(node, float) and not math.isfinite(node):
        raise ValidationError(f"config: non-finite number at {path}: {node!r}")


def effective_config_digest(view: dict[str, Any]) -> str:
    """The ONE fingerprint function for config views (spec Sec2), mirroring
    `governance_fingerprint`'s idiom: recursively sorted object keys (json
    handles this natively with `sort_keys=True`), array order preserved,
    UTF-8, fixed separators, non-finite numbers rejected. `view` must
    already be default-hydrated with source markers stripped -- callers
    pass a `layers[...]` value or a `project(...)` result, never the
    CLI's `{value, source}` presentation wrapper. Settings here are
    frozen: changing any of them is a breaking change to every recorded
    approval and requires a migration.
    """
    _reject_non_finite(view)
    encoded = json.dumps(
        view, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def epic_config_drift(state: dict[str, Any], wdd_dir: Path | str) -> dict[str, Any] | None:
    """Detect an epic overlay (or the global config it layers over) edited
    since `intake.configure` was approved (epic-scoped-state plan Task 5,
    spec Sec2: "Editing the overlay mid-epic surfaces an epic_config_drift
    blocker"). Mirrors `governance_drift`'s shape and no-op cases: None when
    there is nothing to drift from -- a scope that has not recorded
    `configure` at all yet (nothing approved to compare against; this
    happens during setup, before any governed verb -- the only caller of
    `require_fresh_epic_config` -- could possibly run), or (defensive; should
    not occur post-migration, see migration.py's `convert_v5_to_v6`) a
    `configure` record with no `sha256` at all.

    A wholesale-legacy scope (`intake.legacy`) is NOT exempt here: migration
    stamps its `configure` with the identical `{"legacy": true, "sha256":
    ...}` shape a migrated non-legacy scope gets (spec Sec4: "the exemption
    covers only the missing human attribution -- drift is still guarded
    ordinarily"), so its overlay/global edits are gated exactly like any
    other scope's. Likewise a migration-stamped `configure` (`legacy: true`)
    on a non-legacy scope is compared, not skipped -- the exemption only
    means no human approved the CURRENT digest; a mismatch here still
    demands a real, attributed `intake configure --approved-by` (which
    replaces the exemption with a real record).

    The digest recomputed here covers the FULL effective view (matching what
    `intake configure` itself signs), not a purpose projection: this is a
    governance-style approval of "everything", the same doctrine as
    `governance_fingerprint`, not evidence-binding's narrower per-purpose
    comparison.
    """
    intake = state.get("intake") or {}
    configure = intake.get("configure")
    if not isinstance(configure, dict):
        return None
    recorded = configure.get("sha256")
    if recorded is None:
        return None
    layers = load_layers(wdd_dir, state.get("epic"))
    actual = effective_config_digest(layers["effective"])
    if recorded == actual:
        return None
    return {"recorded": recorded, "actual": actual}


def require_fresh_epic_config(state: dict[str, Any], wdd_dir: Path | str) -> None:
    """Chokepoint gate (epic-scoped-state plan Task 5): refuse every governed
    verb once the epic config drifted from what `intake configure` approved.
    Sits between `require_fresh_governance` and `require_fresh_intake` at the
    CLI chokepoint (spec Sec2's precedence: governance -> epic config ->
    intake artifacts -> plan composite).
    """
    drift = epic_config_drift(state, wdd_dir)
    if drift is not None:
        raise IllegalTransition(
            "epic config drift: the epic overlay (or the global config it layers "
            f"over) changed since intake.configure was approved (recorded "
            f"{drift['recorded']}, current {drift['actual']}); run 'wddctl intake "
            "configure --approved-by NAME' (or --use-defaults --by NAME) to re-approve"
        )


def project(view: dict[str, Any], purpose: str) -> dict[str, Any]:
    """The named key-subset projections (spec Sec2). Feed the result to
    `effective_config_digest` for a purpose-projected digest -- the same
    function, never a second implementation. Gates compare like with
    like: a `models.planning` edit changes no projection at all; a
    `verification.commands` edit changes exactly `taskVerification` and
    `finalVerification`.

    Spec-alignment note (flagged in the Task 2 report): spec Sec2 says
    `finalVerification` is "verification.*, plus the deliverable command
    for final". The deliverable command lives in
    `state.intake.design.deliverableCommand`, not in the config `view`
    this function receives -- `project()` has no access to state, so it
    cannot include it. `taskVerification` and `finalVerification` are
    therefore identical projections here; combining this projection with
    the deliverable command's bytes is Task 5's concern (finalize.py's
    evidence recording), not this function's.
    """
    if purpose == "plan":
        # models.specReview is excluded here too, not just from the
        # taskReview/finalReview projections below: spec review is
        # conversation-gated, never machine-gated (spec Sec1), so it must
        # appear in NO evidence projection at all -- "plan" is the one
        # projection that otherwise copies the whole `models` object
        # wholesale, so it is the one place that copy must be pruned.
        models = deepcopy(view["models"])
        models.pop("specReview", None)
        return {
            "models": models,
            "riskRules": deepcopy(view["riskRules"]),
            "review": {"policy": view["review"]["policy"]},
        }
    if purpose == "taskReview":
        return {
            "models": {"review": deepcopy(view["models"]["review"])},
            "review": {
                "policy": view["review"]["policy"],
                "blockingSeverities": deepcopy(view["review"]["blockingSeverities"]),
            },
        }
    if purpose == "finalReview":
        return {
            "models": {"review": deepcopy(view["models"]["review"])},
            "review": {"blockingSeverities": deepcopy(view["review"]["blockingSeverities"])},
        }
    if purpose in ("taskVerification", "finalVerification"):
        return {"verification": deepcopy(view["verification"])}
    raise ValidationError(
        f"config: unknown projection purpose {purpose!r}; expected one of {PROJECTION_PURPOSES}"
    )
