"""wddctl: the deterministic half of Wave-Driven Development.

The agent loop is: `wddctl next` -> do the one thing that needs judgment ->
`wddctl <verb>` -> repeat. Every mechanical action is a verb here, so no
mechanical step depends on prompt interpretation.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
import time
from pathlib import Path
from typing import Any

from .config import (
    _hydrate_optional_sections,
    check_ratifiable,
    config_path,
    derive_effective,
    epic_config_drift,
    get_value,
    governance_drift,
    governance_fingerprint,
    load_config,
    load_layers,
    merge_settings,
    require_fresh_epic_config,
    require_fresh_governance,
    resolve_config_source,
    save_config,
    save_overlay,
    set_overlay_value,
    set_value,
)
from .constitution import probe_repository, ratification_status, read_proposal, write_proposal
from .setup import (
    _intake_ladder_action,
    create_epic,
    init_repository,
    migrate_governance,
    park_epic,
    resume_epic,
    setup_next_actions,
)
from .doctor import inspect_capabilities
from .version import wddctl_version
from .engine import (
    ACTIVE_STATUSES,
    admission_schedule,
    apply_event,
    apply_mutation,
    bounded_next_actions,
    human_reference,
    render_to_path,
    status_summary,
)
from .errors import IllegalTransition, ValidationError, WaveDeliveryError
from .schema import derived_phase, intake_complete
from .finalize import (
    _base_ref as _finalize_base_ref,
    _finalize_integration_worktree,
    _is_legacy_intake,
    _remove_finalize_integration_worktree,
    _require_finalize_phase,
    _require_not_delivered,
    _required_verification_commands,
    archive_scope,
    finalize_next_actions,
    finalize_status,
    prepare_handoff,
    record_delivered,
    record_final_review,
    record_final_verification,
    record_final_verification_run,
)
from .freshness import check_freshness, record_freshness
from .git import require_repository, resolve_ref, run_git, worktree_for
from .github import comment_pr, create_pr, push_branch
from .handover import (
    inputs_status,
    materialize_attempt,
    record_attempt,
    rebind_attempt,
    sanitize_task_id_for_filename,
)
from .intake import (
    intake_status,
    record_configure,
    record_design,
    record_research,
    record_spec,
)
from .leases import release_task, start_task, submit_task
from .lint import lint_plan
from .merge import merge_task, observe_merge, refresh_task
from .migration import apply_migration, plan_migration
from .monitor import monitor_once
from .runner import dispatch_task, probe_command, record_probe, runner_command_digest
from .plan import (
    apply_config_defaults,
    apply_plan,
    apply_risk_rules,
    brief_skeleton,
    intake_gate_status,
    plan_skeleton,
    read_plan,
    require_fresh_intake,
    state_from_plan,
)
from .review import record_review, record_verification, record_verification_run, validate_findings
from .store import StateStore
from .verify_run import execute as verify_run_execute
from .verify_run import reserve_numbered_path


DEFAULT_STATE = Path(".wdd/state.json")

# Verbs that mutate execution state (or the record of it) and therefore must
# refuse when config.json/constitution.md drifted from what was ratified.
# Read-only verbs (status, next, render, freshness check, doctor, monitor),
# init/config/plan/migrate, block/unblock/cancel/note, and constitution
# itself are deliberately absent: they either don't act on ratified
# governance or are how governance gets re-signed in the first place.
GOVERNED_VERBS = {
    ("start", None),
    ("submit", None),
    ("refresh", None),
    ("merge", None),
    ("review", "record"),
    ("review", "collect"),
    ("verify", "record"),
    ("verify", "collect"),
    ("reconcile", "done"),
    ("finalize", "review"),
    ("finalize", "verify"),
    ("finalize", "handoff"),
    ("finalize", "delivered"),
    # rebind is governed (config/constitution + intake/plan drift must still
    # be fresh) but deliberately NOT in TASK_INPUT_GATED_VERBS below: it is
    # the remedy input-version binding demands, and must stay legal exactly
    # when a task's own inputs_status is non-None.
    ("rebind", None),
    # Governed by default -- both `--probe NAME` (re-verifying an already-
    # ratified runner) and `--task ID --role ...` execute config-loaded
    # commands. The one exception, `--probe-command` (an explicit candidate
    # the user just typed, never yet config), is special-cased to skip this
    # set entirely in main()'s chokepoint -- spec Sec6's "deliberately
    # ungoverned" path, never a second entry in this set.
    ("dispatch", None),
    # The escape hatch bypasses transitions, not governance.
    ("event", "apply"),
    # `epic new` (Task 4, spec Sec1) mutates state.epic and creates
    # epics/<slug>/ -- governed like plan/intake's OWN mutating verbs are
    # NOT (they are how governance/ladder progress gets re-signed), but
    # unlike them epic new has no remedy role of its own to protect, so it
    # stays governed like start/submit/merge.
    ("epic", "new"),
    # `epic park`/`epic resume` (epic park/resume spec, "Verbs"): both
    # mutate state.epic and the scope-carrying sections, same governed
    # footing as `epic new`. `resume`'s OWN admission question is answered
    # by `require_fresh_governance` alone against the pre-swap state (spec
    # "Resume and the chokepoint, pinned") -- it stays in this set (not a
    # separate one) because the epic-level gates below are structural
    # no-ops on that pre-swap state by construction, not because resume is
    # exempted from the chokepoint.
    ("epic", "park"),
    ("epic", "resume"),
}

# Task-targeted governed verbs additionally gated by input-version binding
# (spec Sec3, Task 3): refuse via IllegalTransition when the TARGETED task's
# own `inputs_status` is non-None -- its recorded attempt's brief/context
# digests no longer match the current bytes, so review/verification/merge
# evidence for it is no longer trustworthy. `start` is deliberately absent:
# a fresh `start` re-materializes and re-records, which IS the re-dispatch
# remedy, not something the gate should block. Scope-level verbs (finalize,
# reconcile) and other tasks are unaffected -- this is a per-task gate, not
# a scope-wide one (that's `require_fresh_intake`'s plan_drift, above).
TASK_INPUT_GATED_VERBS = {
    ("submit", None),
    ("review", "record"),
    ("review", "collect"),
    ("verify", "record"),
    ("verify", "collect"),
    ("refresh", None),
    ("merge", None),
    # `--task ID --role ...` is task-targeted the same way; `--probe-command`/
    # `--probe NAME` pass no --task, so the chokepoint's own gated_task_id
    # lookup (getattr(args, "task", None)) is None for them and this is a
    # no-op there -- only the --task dispatch path is actually gated.
    ("dispatch", None),
}


def _subcommand(args: argparse.Namespace) -> str | None:
    """The dispatched subcommand for commands that have one, else None.

    Every subparser's dest follows "{command}_command" (review_command,
    verify_command, reconcile_command, ...); plain verbs like "start" have
    no such attribute.
    """
    return getattr(args, f"{args.command}_command", None)


def _json_argument(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError(f"invalid JSON: {error.msg}") from error


def _json_object(value: str) -> dict[str, Any]:
    parsed = _json_argument(value)
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("JSON data must be an object")
    return parsed


def _json_list(value: str) -> list[str]:
    parsed = _json_argument(value)
    if not isinstance(parsed, list) or not all(isinstance(item, str) and item for item in parsed):
        raise argparse.ArgumentTypeError("JSON command must be a non-empty string array")
    return parsed


def _add_concurrency_flags(parser: argparse.ArgumentParser) -> None:
    """Optional optimistic-concurrency controls.

    Omit them and the current revision is used under the same lock that guards
    the write, with an idempotency key derived from the event payload. Pass them
    when several controllers share one scope and you want a hard conflict.
    """
    parser.add_argument("--expected-revision", type=int, default=None)
    parser.add_argument("--idempotency-key", default=None)


def _short_sha() -> str | None:
    """Best-effort short commit SHA for the checkout `wave_delivery` lives in.

    Absent (not a git checkout, git missing, or any failure) rather than
    fatal: version reporting must never refuse. A short timeout keeps a
    missing/hung git from stalling `--version`.
    """
    import subprocess

    repo_root = Path(__file__).resolve().parents[1]
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def _version_string() -> str:
    version = wddctl_version()
    sha = _short_sha()
    return f"wddctl {version} ({sha})" if sha else f"wddctl {version}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wddctl", description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--version", action="version", version=_version_string())
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="create or update a scope from a plan file")
    plan_subparsers = plan.add_subparsers(dest="plan_command", required=True)
    plan_apply = plan_subparsers.add_parser("apply", help="apply plan.json to the scope")
    plan_apply.add_argument("--plan", required=True, type=Path)
    plan_apply.add_argument("--repo", type=Path, default=Path("."))
    plan_apply.add_argument("--from-ref", default=None, help="start point for a new base branch")
    plan_apply.add_argument("--dry-run", action="store_true")
    plan_apply.add_argument("--strict", action="store_true")
    plan_apply.add_argument("--approved-by", dest="approved_by", default=None)
    _add_concurrency_flags(plan_apply)
    plan_preview = plan_subparsers.add_parser(
        "preview", help="project the admission order (a view, not a gate)"
    )
    plan_preview.add_argument("--plan", type=Path)
    plan_lint = plan_subparsers.add_parser("lint", help="report plan-quality warnings")
    plan_lint.add_argument("--plan", type=Path, required=True)
    plan_lint.add_argument("--strict", action="store_true")
    plan_template = plan_subparsers.add_parser(
        "template",
        help=(
            "print a skeleton plan.json (or --brief for a task brief) to fill "
            "in — the starting point for wdd-plan decomposition"
        ),
    )
    plan_template.add_argument("--brief", action="store_true")

    subparsers.add_parser("doctor", help="report optional controller capabilities").add_argument(
        "--json", action="store_true"
    )

    init = subparsers.add_parser(
        "init", help="scaffold .wdd/: config, constitution draft, and pre-scope state"
    )
    init.add_argument("--repo", type=Path, default=Path("."))

    status = subparsers.add_parser("status", help="show a concise state summary")
    status.add_argument("--json", action="store_true")

    next_command = subparsers.add_parser("next", help="show executable next actions and blockers")
    next_command.add_argument("--max-bytes", type=int, default=4096)
    next_command.add_argument("--repo", type=Path, default=Path("."))

    render = subparsers.add_parser("render", help="render a Markdown state projection")
    render.add_argument("--output", required=True, type=Path)
    render.add_argument("--repo", type=Path, default=Path("."))

    start = subparsers.add_parser(
        "start", help="admit a task, create its isolated worktree, and mark it in progress"
    )
    start.add_argument("--task", required=True)
    start.add_argument("--repo", type=Path, default=Path("."))
    start.add_argument("--branch")
    start.add_argument("--worktree", type=Path)
    _add_concurrency_flags(start)

    submit = subparsers.add_parser("submit", help="record a task deliverable and its head SHA")
    submit.add_argument("--task", required=True)
    submit.add_argument("--repo", type=Path, default=Path("."))
    submit.add_argument("--pr", help="PR URL; defaults to a branch reference")
    _add_concurrency_flags(submit)

    review = subparsers.add_parser("review", help="record review findings")
    review_subparsers = review.add_subparsers(dest="review_command", required=True)
    review_record = review_subparsers.add_parser(
        "record", help="record findings inline; base and head SHAs are supplied by the controller"
    )
    review_record.add_argument("--task", required=True)
    review_record.add_argument("--reviewer", required=True)
    review_record.add_argument(
        "--findings",
        type=_json_argument,
        default=[],
        help='JSON array, e.g. \'[{"severity":"P1","summary":"...","file":"a.py","line":3}]\'; '
        "omit or pass [] for a clean review",
    )
    review_record.add_argument("--repo", type=Path, default=Path("."))
    _add_concurrency_flags(review_record)
    review_run = review_subparsers.add_parser("run", help="run a configured reviewer command")
    review_run.add_argument("--task", required=True)
    review_run.add_argument("--repo", type=Path, default=Path("."))
    review_run.add_argument("--command-json", required=True, type=_json_list)
    review_run.add_argument("--output", required=True, type=Path)
    review_collect = review_subparsers.add_parser(
        "collect", help="aggregate external reviewer result files"
    )
    review_collect.add_argument("--task", required=True)
    review_collect.add_argument("--result", required=True, type=Path, action="append")
    review_collect.add_argument("--repo", type=Path, default=Path("."))
    _add_concurrency_flags(review_collect)

    verify = subparsers.add_parser("verify", help="record verification evidence")
    verify_subparsers = verify.add_subparsers(dest="verify_command", required=True)
    verify_record = verify_subparsers.add_parser("record", help="record a verification outcome")
    verify_record.add_argument("--task", required=True)
    # Not argparse-required: --status is the agent-reported contract, --run is
    # the machine-executed one, and they are mutually exclusive (spec AC-3).
    # Neither argparse's own mutually-exclusive-group nor a required= here can
    # express "one of these two, post-parse, with a named message" -- the
    # refusal is raised by hand below, same pattern finalize_verify_record's
    # legacy-vs-v5 contract already uses for --status/--command vs --results.
    verify_record.add_argument(
        "--status",
        choices=("passed", "failed", "unavailable"),
        help="agent-reported outcome; mutually exclusive with --run",
    )
    # dest must not be "command": that is the top-level subparser destination.
    verify_record.add_argument(
        "--command",
        dest="verify_command_text",
        help="the command that produced this result; mutually exclusive with --run",
    )
    verify_record.add_argument(
        "--run",
        action="store_true",
        help="execute the effective verification commands in the task's worktree and "
        "record what wddctl observed, instead of trusting --status/--command",
    )
    verify_record.add_argument("--repo", type=Path, default=Path("."))
    _add_concurrency_flags(verify_record)
    verify_collect = verify_subparsers.add_parser("collect", help="read an external result file")
    verify_collect.add_argument("--task", required=True)
    verify_collect.add_argument("--result", required=True, type=Path)
    verify_collect.add_argument("--repo", type=Path, default=Path("."))
    _add_concurrency_flags(verify_collect)

    finalize = subparsers.add_parser(
        "finalize", help="scope-level finalize verbs: review, verify, handoff, delivered"
    )
    finalize_subparsers = finalize.add_subparsers(dest="finalize_command", required=True)

    finalize_review = finalize_subparsers.add_parser(
        "review", help="record the whole-epic-branch final review"
    )
    finalize_review_subparsers = finalize_review.add_subparsers(
        dest="finalize_review_command", required=True
    )
    finalize_review_record = finalize_review_subparsers.add_parser(
        "record", help="record final review findings; evidence is pinned to the current base head"
    )
    finalize_review_record.add_argument("--reviewer", required=True)
    finalize_review_record.add_argument(
        "--findings",
        type=_json_argument,
        default=[],
        help='JSON array, e.g. \'[{"severity":"P1","summary":"...","file":"a.py","line":3}]\'; '
        "omit or pass [] for a clean review",
    )
    finalize_review_record.add_argument("--repo", type=Path, default=Path("."))
    _add_concurrency_flags(finalize_review_record)

    finalize_verify = finalize_subparsers.add_parser(
        "verify", help="record the whole-epic-branch final verification"
    )
    finalize_verify_subparsers = finalize_verify.add_subparsers(
        dest="finalize_verify_command", required=True
    )
    finalize_verify_record = finalize_verify_subparsers.add_parser(
        "record", help="record a final verification outcome"
    )
    # Legacy scopes use --status/--command(/--justification); v5 non-legacy
    # scopes use --results (a JSON array covering every required command in
    # one atomic call). Neither trio nor --results is argparse-required:
    # record_final_verification itself refuses the wrong contract for the
    # scope's own legacy-ness with a named message, not argparse's generic
    # SystemExit(2).
    finalize_verify_record.add_argument(
        "--status",
        choices=("passed", "failed", "unavailable"),
        help="legacy scopes only: the single verification outcome",
    )
    # dest must not be "command": that is the top-level subparser destination.
    finalize_verify_record.add_argument(
        "--command",
        dest="finalize_verify_command_text",
        help="legacy scopes only: the command that produced this result",
    )
    finalize_verify_record.add_argument(
        "--justification",
        help="legacy scopes only: required when --status unavailable and no config "
        "verification.unavailableJustification exists",
    )
    finalize_verify_record.add_argument(
        "--results",
        type=_json_argument,
        default=None,
        help="v5 non-legacy scopes only: JSON array '[{\"command\":..., \"status\":...}, ...]' "
        "naming, in order, every entry of the ratified global verification.commands then the "
        "scope's deliverable command",
    )
    finalize_verify_record.add_argument(
        "--run",
        action="store_true",
        help="execute the ratified global verification commands then the scope's "
        "deliverable command, in order, in an integration worktree at the resolved "
        "epic head, and record what wddctl observed; mutually exclusive with "
        "--results and unavailable on legacy scopes",
    )
    finalize_verify_record.add_argument("--repo", type=Path, default=Path("."))
    _add_concurrency_flags(finalize_verify_record)

    finalize_handoff = finalize_subparsers.add_parser(
        "handoff",
        help="push the epic branch and open (pr surface) or instruct (local surface) the handoff",
    )
    finalize_handoff.add_argument("--repo", type=Path, default=Path("."))
    _add_concurrency_flags(finalize_handoff)

    finalize_delivered = finalize_subparsers.add_parser(
        "delivered", help="record the observed human merge of the epic branch into the target"
    )
    finalize_delivered.add_argument("--by", required=True)
    finalize_delivered.add_argument("--repo", type=Path, default=Path("."))
    _add_concurrency_flags(finalize_delivered)

    finalize_subparsers.add_parser("status", help="show the finalize section and phase")

    epic = subparsers.add_parser("epic", help="epic lifecycle: create and adopt")
    epic_subparsers = epic.add_subparsers(dest="epic_command", required=True)
    epic_new = epic_subparsers.add_parser(
        "new",
        help=(
            "create a new epic directory and set it active -- the first action of "
            "every epic (spec Sec1). Slugs are immutable: there is no rename verb; "
            "retiring one means archiving it."
        ),
    )
    epic_new.add_argument(
        "--slug", required=True, help="epic slug: [a-z0-9][a-z0-9-]{1,63}, unique, immutable"
    )
    epic_new.add_argument(
        "--title", default=None, help="optional display title, recorded on the epic.created event"
    )
    _add_concurrency_flags(epic_new)

    epic_park = epic_subparsers.add_parser(
        "park",
        help=(
            "suspend the active epic: release its worktrees (refusing on any dirty "
            "one), keep its branches, and move its state under state.parked -- "
            "'wddctl epic resume --slug SLUG' reactivates it later."
        ),
    )
    epic_park.add_argument("--repo", type=Path, default=Path("."))
    _add_concurrency_flags(epic_park)

    epic_resume = epic_subparsers.add_parser(
        "resume",
        help="reactivate a parked epic -- refuses if an epic is already active.",
    )
    epic_resume.add_argument("--slug", required=True, help="the parked epic's slug")
    _add_concurrency_flags(epic_resume)

    intake = subparsers.add_parser(
        "intake", help="the spec/research/design ladder before plan apply"
    )
    intake_subparsers = intake.add_subparsers(dest="intake_command", required=True)

    intake_spec = intake_subparsers.add_parser(
        "spec", help="record spec.md approval (numbered acceptance criteria required)"
    )
    intake_spec.add_argument("--approved-by", dest="approved_by", required=True)
    _add_concurrency_flags(intake_spec)

    intake_research = intake_subparsers.add_parser(
        "research", help="record research done (with hashed artifacts) or an attributed skip"
    )
    # Mode exclusivity (exactly one of --done/--skip) is enforced by
    # record_research itself, not argparse: a semantic ValidationError with a
    # clear message is more useful here than argparse's SystemExit(2).
    intake_research.add_argument("--done", action="store_true")
    intake_research.add_argument("--skip", action="store_true")
    intake_research.add_argument("--by", required=True)
    intake_research.add_argument(
        "--artifacts",
        nargs="+",
        default=None,
        help="one or more .wdd-relative paths, required with --done",
    )
    intake_research.add_argument("--reason", help="required with --skip")
    _add_concurrency_flags(intake_research)

    intake_design = intake_subparsers.add_parser(
        "design", help="record design.md approval and the epic deliverable command"
    )
    intake_design.add_argument("--approved-by", dest="approved_by", required=True)
    # Not argparse-required: omission must refuse with record_design's own
    # named message ("the epic deliverable's proof is not optional"), not
    # argparse's generic SystemExit(2).
    intake_design.add_argument(
        "--deliverable-command",
        dest="deliverable_command",
        default=None,
        help="the command that proves the epic deliverable; recorded and fingerprinted with design",
    )
    _add_concurrency_flags(intake_design)

    intake_configure = intake_subparsers.add_parser(
        "configure",
        help="approve the epic overlay (or explicitly inherit every default) -- "
        "the configure step, spec Sec2; required before 'intake spec'",
    )
    intake_configure.add_argument(
        "--approved-by", dest="approved_by", default=None,
        help="approve the epic overlay AS CURRENTLY WRITTEN (built up via 'config set --epic')",
    )
    intake_configure.add_argument(
        "--use-defaults", action="store_true",
        help="explicitly inherit every default; also resets the overlay to empty",
    )
    intake_configure.add_argument(
        "--by", default=None, help="required with --use-defaults",
    )
    _add_concurrency_flags(intake_configure)

    intake_subparsers.add_parser("status", help="show the intake section and the next rung")

    scope = subparsers.add_parser(
        "scope", help="scope-level lifecycle verbs beyond the finalize ladder"
    )
    scope_subparsers = scope.add_subparsers(dest="scope_command", required=True)
    scope_archive = scope_subparsers.add_parser(
        "archive",
        help="delivered-only: archive the scope's records and reset state for the next one",
    )
    scope_archive.add_argument("--repo", type=Path, default=Path("."))
    _add_concurrency_flags(scope_archive)

    freshness = subparsers.add_parser("freshness", help="classify a task branch against the base")
    freshness_subparsers = freshness.add_subparsers(dest="freshness_command", required=True)
    freshness_check = freshness_subparsers.add_parser("check", help="inspect without mutating state")
    freshness_check.add_argument("--repo", type=Path, default=Path("."))
    freshness_check.add_argument("--base", required=True)
    freshness_check.add_argument("--head", required=True)
    freshness_check.add_argument("--conflict-domain", action="append", default=[])
    freshness_record = freshness_subparsers.add_parser("record", help="inspect and record evidence")
    freshness_record.add_argument("--task", required=True)
    freshness_record.add_argument("--repo", type=Path, default=Path("."))
    _add_concurrency_flags(freshness_record)

    refresh = subparsers.add_parser(
        "refresh", help="merge the scope base into a task branch and re-record its head"
    )
    refresh.add_argument("--task", required=True)
    refresh.add_argument("--repo", type=Path, default=Path("."))
    _add_concurrency_flags(refresh)

    merge = subparsers.add_parser("merge", help="merge a task into the scope base and record it")
    merge.add_argument("--task", required=True)
    merge.add_argument("--repo", type=Path, default=Path("."))
    merge.add_argument(
        "--observed",
        action="store_true",
        help="record a merge a human performed out-of-band; never mutates Git, "
        "requires live-Git ancestry proof",
    )
    _add_concurrency_flags(merge)

    rebind = subparsers.add_parser(
        "rebind",
        help="record that a task's changed inputs are accepted; re-records digests vs current bytes",
    )
    rebind.add_argument("--task", required=True)
    rebind.add_argument("--by", required=True)
    rebind.add_argument("--repo", type=Path, default=Path("."))
    _add_concurrency_flags(rebind)

    dispatch = subparsers.add_parser(
        "dispatch",
        help="probe a runner command, or dispatch a task through a configured runner",
    )
    dispatch.add_argument(
        "--probe-command",
        dest="probe_command",
        type=_json_list,
        default=None,
        help="ungoverned: probe an explicit candidate runner argv (JSON string array)",
    )
    dispatch.add_argument(
        "--probe", default=None, help="governed: re-verify an already-configured runner by name"
    )
    dispatch.add_argument("--task", help="governed + input-binding-gated: dispatch this task")
    dispatch.add_argument("--role", choices=("worker", "reviewer"))
    dispatch.add_argument("--repo", type=Path, default=Path("."))
    dispatch.add_argument("--timeout", type=int, default=None)
    _add_concurrency_flags(dispatch)

    release = subparsers.add_parser("release", help="remove a finished task's worktree")
    release.add_argument("--task", required=True)
    release.add_argument("--repo", type=Path, default=Path("."))
    release.add_argument("--keep-worktree", action="store_true")
    _add_concurrency_flags(release)

    block = subparsers.add_parser("block", help="mark a task blocked with a reason")
    block.add_argument("--task", required=True)
    block.add_argument("--reason", required=True)
    _add_concurrency_flags(block)

    unblock = subparsers.add_parser("unblock", help="return a blocked task to the queue")
    unblock.add_argument("--task", required=True)
    _add_concurrency_flags(unblock)

    cancel = subparsers.add_parser("cancel", help="cancel a task")
    cancel.add_argument("--task", required=True)
    _add_concurrency_flags(cancel)

    note = subparsers.add_parser("note", help="queue a durable discovery for reconciliation")
    note.add_argument("--task")
    note.add_argument("--note", required=True)
    _add_concurrency_flags(note)

    reconcile = subparsers.add_parser("reconcile", help="reconciliation checkpoint")
    reconcile_subparsers = reconcile.add_subparsers(dest="reconcile_command", required=True)
    reconcile_subparsers.add_parser("status", help="show whether a checkpoint is due")
    reconcile_done = reconcile_subparsers.add_parser("done", help="clear the checkpoint")
    _add_concurrency_flags(reconcile_done)

    migrate = subparsers.add_parser("migrate", help="convert schema-v2 state to the current schema")
    migrate.add_argument("--dry-run", action="store_true")
    migrate.add_argument("--apply", action="store_true")
    migrate.add_argument(
        "--governance",
        action="store_true",
        help="split a legacy constitution into config.json + prose (invalidates ratification)",
    )
    migrate.add_argument(
        "--review-policy",
        choices=("always", "risk_based", "none"),
        default="always",
        help="schema v2 required review for every task; the default preserves that",
    )

    monitor = subparsers.add_parser("monitor", help="perform one cheap Git observation tick")
    monitor.add_argument("--once", action="store_true", required=True)
    monitor.add_argument("--repo", type=Path, default=Path("."))
    monitor.add_argument("--dry-run", action="store_true")

    event = subparsers.add_parser("event", help="escape hatch: apply one raw transition")
    event_subparsers = event.add_subparsers(dest="event_command", required=True)
    event_apply = event_subparsers.add_parser("apply", help="apply one event atomically")
    event_apply.add_argument("--event", required=True)
    event_apply.add_argument("--task")
    event_apply.add_argument("--data", type=_json_object, default={})
    _add_concurrency_flags(event_apply)

    constitution = subparsers.add_parser("constitution", help="manage constitution ratification")
    constitution_subparsers = constitution.add_subparsers(
        dest="constitution_command", required=True
    )
    for name, help_text in (
        ("ratify", "record the initial explicit ratification"),
        ("amend", "re-ratify after the constitution changed"),
    ):
        command = constitution_subparsers.add_parser(name, help=help_text)
        command.add_argument("--by", required=True)
        # Optional since the fingerprint is now computed from .wdd/config.json +
        # constitution.md; --decision-fingerprint / --proposal are legacy pins,
        # kept for callers that want to assert what they were shown still matches.
        fingerprint_source = command.add_mutually_exclusive_group(required=False)
        fingerprint_source.add_argument("--decision-fingerprint")
        fingerprint_source.add_argument("--proposal", type=Path)
        _add_concurrency_flags(command)
    probe = constitution_subparsers.add_parser("probe", help="gather repository evidence")
    probe.add_argument("--root", type=Path, default=Path("."))
    probe.add_argument("--output", type=Path)
    constitution_status = constitution_subparsers.add_parser(
        "status", help="check ratification and proposal drift"
    )
    constitution_status.add_argument("--proposal", type=Path)

    config_cmd = subparsers.add_parser("config", help="read or write .wdd/config.json")
    config_subparsers = config_cmd.add_subparsers(dest="config_command", required=True)
    config_get = config_subparsers.add_parser("get", help="print one value as JSON")
    config_get.add_argument("path", help="dotted path, e.g. merge.surface")
    config_get.add_argument(
        "--epic",
        action="store_true",
        help="resolve through the active epic's overlay; prints {path, value, source}",
    )
    config_set = config_subparsers.add_parser(
        "set", help="set one value (JSON literal, or bare string fallback)"
    )
    config_set.add_argument("path", help="dotted path, e.g. merge.surface")
    config_set.add_argument("value", help='e.g. local or \'["pytest -q"]\'')
    config_set.add_argument(
        "--epic",
        action="store_true",
        help="write to the active epic's overlay instead of the global config "
        "(allowlist-checked; requires an active epic)",
    )
    config_subparsers.add_parser("show", help="print the whole config")
    return parser


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _review_comment_body(findings: list[dict[str, Any]], reviewer: str) -> str:
    """The markdown mirrored to a PR when review record runs on the pr surface."""
    if not findings:
        return f"wddctl review by {reviewer}: clean review, no findings."
    lines = [
        f"wddctl review by {reviewer}:",
        "",
        "| Severity | Summary | File | Line |",
        "| --- | --- | --- | --- |",
    ]
    for finding in findings:
        summary = str(finding.get("summary", "") or "").replace("|", "\\|")
        file_ = finding.get("file") or ""
        line = finding.get("line")
        lines.append(
            f"| {finding.get('severity', '')} | {summary} | {file_} | {line if line is not None else ''} |"
        )
    return "\n".join(lines)


def _brief(summary: dict[str, Any]) -> str:
    counts = ", ".join(f"{status}={count}" for status, count in summary["taskCounts"].items())
    lines = [
        f"{summary['scope']['id']} revision {summary['revision']}",
        f"constitution: {summary['constitution']}",
        f"tasks: {counts}",
        f"active: {len(summary['activeTasks'])}",
    ]
    if summary["reconcile"]["due"]:
        lines.append(f"reconciliation due: {summary['reconcile']['due']['code']}")
    return "\n".join(lines)


def _state_option(args: argparse.Namespace) -> str | None:
    """Echo --state back into emitted commands only when it is not the default."""
    state = str(args.state)
    return None if state == str(DEFAULT_STATE) else state


def _concurrency(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "expected_revision": getattr(args, "expected_revision", None),
        "idempotency_key": getattr(args, "idempotency_key", None),
    }


def _worktrees_root(config: dict[str, Any] | None) -> str | None:
    """The `worktrees.root` config value, or None with no config (legacy repo).

    None is also legal for a config that predates this key (config.py's
    backward-compatible optional validation) -- either way, git.py's
    `resolve_worktrees_root` falls back to the historical out-of-repo default.
    """
    if not config:
        return None
    return (config.get("worktrees") or {}).get("root")


def _governed_config(admission_layers: dict[str, Any] | None) -> dict[str, Any] | None:
    """The config view a GOVERNED_VERBS handler must use once `main()`'s
    chokepoint has already resolved an admission snapshot for this
    invocation (spec Sec2 resolve-once, extended by fix-round F2): the
    epic-overlaid `effective` view, not a second bare `load_config` read
    that would silently miss an active epic's override (`merge.surface`,
    `models.*`, ...) -- exactly the bug this closes for merge_settings and
    dispatch's model resolution. `admission_layers` is None precisely when
    the chokepoint's own `config_path(...).exists()` check was None too (a
    legacy scope with no config.json), so this is a drop-in replacement for
    the old `load_config(...) if config_path(...).exists() else None`
    pattern at every governed call site, never a behavior change for that
    no-config case.
    """
    return admission_layers["effective"] if admission_layers is not None else None


# The same legal-status set engine.transition's "verification.recorded"
# branch enforces (`_require_status(task, {"in_progress"}, ...)`). Duplicated
# here, deliberately, so `_run_task_verification` can refuse BEFORE
# `verify_run.execute` ever runs a command (see its docstring) -- the
# transition's own check stays in engine.py as defense in depth for state
# that changes between this read and the locked write, not as the only
# gate.
_VERIFICATION_RUN_LEGAL_STATUSES = {"in_progress"}


def _run_task_verification(
    store: StateStore, *, args: argparse.Namespace, admission_layers: dict[str, Any] | None
) -> tuple[dict[str, Any], bool]:
    """`wddctl verify record --task T --run` (spec AC-1/AC-2/AC-4/AC-9):
    resolve the effective verification commands from the SAME admission
    snapshot the chokepoint already read (`_governed_config`, never a second
    `load_layers`), refuse before executing anything on an illegal task
    status, an unparseable/missing headSha, a worktree HEAD that has moved
    past the recorded headSha, a dirty or absent worktree, an empty command
    list, or a replayed idempotency key, then hand the commands to
    `verify_run.execute` and record what it observed via
    `review.record_verification_run`.

    Every refusal below happens before `verify_run.execute` is ever called,
    so AC-2's "no command executes on refusal" holds by construction --
    there is no partially-executed state to unwind. This includes checks
    (status legality, headSha) that `engine.transition` ALSO enforces: that
    enforcement only fires from inside `apply_event`, which this function
    calls AFTER execution, so relying on it alone would mean a bad status or
    stale head gets discovered only after commands already ran and a log
    was already written (review finding P1-2). Hoisting them here, ahead of
    every side effect, is what actually satisfies AC-2 for those cases;
    engine.transition's copy remains authoritative defense in depth.
    """
    repo_path = require_repository(args.repo)
    state = store.read()
    try:
        task = state["tasks"][args.task]
    except KeyError as error:
        raise ValidationError(f"unknown task: {args.task}") from error
    # P3: an already-applied idempotency key must be inert BEFORE any other
    # precondition, execution included. apply_mutation's own dedupe (inside
    # record_verification_run's eventual apply_event call) checks the key
    # ahead of every mutator-embedded validation too (a replay must not
    # newly fail on e.g. a status the FIRST call already advanced past) --
    # mirrored here so a replayed --run also skips execution and every
    # other preflight check, not just the state write. appliedIdempotencyKeys
    # only ever grows (apply_mutation appends, never removes), so this
    # preflight read is exactly as reliable as the locked check apply_mutation
    # performs afterwards; it just runs early enough to skip execution.
    idempotency_key = getattr(args, "idempotency_key", None)
    if idempotency_key and idempotency_key in state.get("appliedIdempotencyKeys", []):
        return apply_event(
            store,
            event_type="verification.recorded",
            task_id=args.task,
            data={},
            idempotency_key=idempotency_key,
            expected_revision=getattr(args, "expected_revision", None),
        )
    if task["status"] not in _VERIFICATION_RUN_LEGAL_STATUSES:
        allowed_text = ", ".join(sorted(_VERIFICATION_RUN_LEGAL_STATUSES))
        raise IllegalTransition(
            f"verify record --run is not legal for task {args.task} from status "
            f"{task['status']!r}; expected one of {allowed_text}"
        )
    head_sha = task.get("headSha")
    if not isinstance(head_sha, str) or not head_sha:
        raise ValidationError(
            f"verify record --run requires task {args.task} to have a recorded, "
            "parseable headSha (none found); run 'wddctl submit' to pin one"
        )
    config = _governed_config(admission_layers)
    commands = list(config["verification"]["commands"]) if config else []
    if not commands:
        raise ValidationError(
            "verify record --run requires a non-empty effective verification command "
            "list (verification.commands is empty): nothing observed is not a pass"
        )
    worktree_path = worktree_for(
        repo_path,
        state["scope"]["id"],
        args.task,
        task.get("worktree"),
        worktrees_root=_worktrees_root(config),
    )
    if not worktree_path.exists():
        raise IllegalTransition(
            f"task worktree is missing: {worktree_path}; "
            f"run 'wddctl start --task {args.task} --repo .' to re-attach it"
        )
    porcelain = run_git(worktree_path, "status", "--porcelain").stdout.strip()
    if porcelain:
        raise IllegalTransition(
            f"verify record --run refuses: task {args.task}'s worktree {worktree_path} "
            f"has uncommitted changes (evidence binds committed bytes only): {porcelain}"
        )
    # P1-1: a clean worktree can still sit on a DIFFERENT commit than the
    # one state believes is current -- e.g. a commit landed in the worktree
    # after 'submit' last pinned task["headSha"], or a rebase/reset moved
    # HEAD without going through 'submit'/'refresh'. Evidence bound to
    # task["headSha"] while the worktree's actual HEAD disagrees would
    # silently verify the wrong bytes, so rev-parse the real HEAD and
    # refuse on any mismatch rather than trusting the recorded value.
    actual_head = run_git(worktree_path, "rev-parse", "HEAD").stdout.strip()
    if actual_head != head_sha:
        raise IllegalTransition(
            f"verify record --run refuses: task {args.task}'s worktree HEAD "
            f"({actual_head}) does not match its recorded headSha ({head_sha}); run "
            f"'wddctl submit --task {args.task} --repo {args.repo}' to re-pin the new "
            f"head, or 'wddctl refresh --task {args.task} --repo {args.repo}' if the "
            "base moved"
        )
    timeout_seconds = config["verification"]["timeoutSeconds"]
    dispatch_dir = store.path.parent / "dispatch"
    sanitized = sanitize_task_id_for_filename(args.task)
    log_path = reserve_numbered_path(dispatch_dir, f"verify-{sanitized}-", ".log")
    started = time.monotonic()
    run_result = verify_run_execute(
        commands, cwd=worktree_path, timeout_seconds=timeout_seconds, log_path=log_path
    )
    duration_ms = int((time.monotonic() - started) * 1000)
    return record_verification_run(
        store,
        task_id=args.task,
        run_result=run_result,
        duration_ms=duration_ms,
        repo=args.repo,
        layers=admission_layers,
        **_concurrency(args),
    )


def _run_final_verification(
    store: StateStore, *, args: argparse.Namespace, admission_layers: dict[str, Any] | None
) -> tuple[dict[str, Any], bool]:
    """`wddctl finalize verify record --run` (spec AC-6): the scope-level
    counterpart of `_run_task_verification`, same preflight-before-
    execution discipline -- every refusal below (idempotency-key replay
    first, then legacy scope, then finalize phase/not-delivered, then an
    empty required command list) happens before `verify_run.execute` is
    ever called, so no command runs on refusal. The required, ordered
    command list (ratified global `verification.commands` then the
    scope's deliverable command) is executed in a dedicated integration
    worktree checked out at the resolved epic head -- never this
    invocation's own `--repo` checkout (`finalize._finalize_integration_
    worktree`, mirroring `merge.py`'s `_integration_dir` precedent but
    always by SHA, detached, never by reusing the operator's checkout).
    """
    repo_path = require_repository(args.repo)
    state = store.read()

    # Mirrors `_run_task_verification`'s identical early return: an
    # already-applied idempotency key must be inert before any other
    # precondition, execution included. `apply_mutation`'s own dedupe
    # (inside `record_final_verification_run`'s eventual call) checks the
    # key ahead of every mutator-embedded validation too, but only once
    # the mutator would otherwise run AFTER a command has already
    # executed -- this preflight read skips execution entirely on replay.
    idempotency_key = getattr(args, "idempotency_key", None)
    if idempotency_key and idempotency_key in state.get("appliedIdempotencyKeys", []):
        return apply_mutation(
            store,
            event_type="final.verification_recorded",
            task_id=None,
            data={},
            idempotency_key=idempotency_key,
            expected_revision=getattr(args, "expected_revision", None),
            mutator=lambda current: current,
        )

    if _is_legacy_intake(state):
        raise ValidationError(
            "finalize verify record --run is not available for a legacy scope: legacy "
            "scopes keep the reported-only single-command contract (wddctl finalize "
            "verify record --status ... --command ...)"
        )
    _require_finalize_phase(state)
    _require_not_delivered(state, what="verify")

    wdd_dir = store.path.parent
    config = _governed_config(admission_layers)
    if config is None:
        raise ValidationError(
            "finalize verify record --run requires a ratified config.json (the global "
            "verification commands and verification.timeoutSeconds are resolved from it)"
        )
    commands = _required_verification_commands(state, wdd_dir, config)
    if not commands:
        raise ValidationError(
            "finalize verify record --run requires a non-empty required command list "
            "(the ratified global verification.commands plus the scope's deliverable "
            "command): nothing observed is not a pass"
        )
    timeout_seconds = config["verification"]["timeoutSeconds"]

    epic_head_sha = resolve_ref(repo_path, _finalize_base_ref(state))
    worktree_path = _finalize_integration_worktree(repo_path, state, epic_head_sha)
    try:
        dispatch_dir = wdd_dir / "dispatch"
        log_path = reserve_numbered_path(dispatch_dir, "verify-final-", ".log")
        started = time.monotonic()
        run_result = verify_run_execute(
            commands, cwd=worktree_path, timeout_seconds=timeout_seconds, log_path=log_path
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        return record_final_verification_run(
            store,
            run_result=run_result,
            duration_ms=duration_ms,
            repo=args.repo,
            layers=admission_layers,
            **_concurrency(args),
        )
    finally:
        # Unconditional, success or failure: a detached worktree left behind
        # at `integration_worktree_path` squats on the exact path merge.py's
        # own `_integration_dir`/`ensure_worktree` uses, and `ensure_worktree`
        # cannot reconcile a detached HEAD (`branch: None`) against the
        # branch name it expects -- see `_remove_finalize_integration_
        # worktree`'s docstring for the wedge this closes.
        _remove_finalize_integration_worktree(repo_path, worktree_path)


def _overlaid_plan(
    args: argparse.Namespace, store: StateStore
) -> tuple[dict[str, Any], Path, dict[str, Any] | None]:
    """Read a plan file and apply the same config overlays 'plan apply' does.

    Shared by 'plan lint' and 'plan apply' so lint always sees exactly what
    apply would see: raw scope defaults, then risk-rule overrides. Also
    returns the current state (or None when no state.json exists yet) --
    lint's `missing_context` check (Task 6) needs to know whether intake
    artifacts are recorded, and this is the one place both callers already
    touch the store, so the state ride-alongs rather than each caller
    re-reading it.

    Uses the EPIC-MERGED effective view (`load_layers(...)["effective"]`),
    not the bare global config (epic-scoped-state plan Task 5 fix): `models`
    and `riskRules` are both epic-overlay-allowed leaves (config.py's
    `OVERLAY_ALLOWED_LEAVES`), so an epic's own overrides -- approved via
    `intake configure` -- must actually feed risk derivation and the
    scope-default fallbacks here, or the overlay would have no effect on
    either. `epic=None` (no active epic, or a legacy scope) degrades to the
    global-only view, matching prior behavior exactly.
    """
    plan = read_plan(args.plan)
    wdd_dir = store.path.parent
    state = store.read() if store.exists() else None
    if config_path(wdd_dir).exists():
        raw_plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        config = load_layers(wdd_dir, (state or {}).get("epic"))["effective"]
        plan = apply_config_defaults(plan, raw_plan["scope"], config)
        plan = apply_risk_rules(plan, config, state)
    return plan, wdd_dir, state


def _base_equals_target_branch(plan: dict[str, Any], wdd_dir: Path) -> str | None:
    """Return the configured targetBranch when plan.scope.baseRef equals it, else None.

    Shared by 'plan apply' (hard refusal) and 'plan lint' (warning, code
    base_is_target): an epic branch that IS the branch it delivers into makes
    finalize's human-merge ancestry checks vacuous -- a ref is trivially its
    own ancestor, so the ladder could self-certify to delivered without any
    human merge ever happening (finalize.py's prepare_handoff/record_delivered
    now also guard this, but refusing at plan time catches it before any task
    ever starts). Skipped for legacy scopes that predate config.json (no
    targetBranch to compare against), and when baseRef is omitted (an
    existing-scope diff apply that keeps its already-validated baseRef).
    """
    if not config_path(wdd_dir).exists():
        return None
    base_ref = plan["scope"].get("baseRef")
    if not base_ref:
        return None
    target_branch = load_config(wdd_dir)["branching"]["targetBranch"]
    return target_branch if base_ref == target_branch else None


def _simple_event(store: StateStore, args: argparse.Namespace, event: str, data: dict[str, Any]) -> int:
    state, duplicate = apply_event(
        store, event_type=event, task_id=getattr(args, "task", None), data=data, **_concurrency(args)
    )
    _print_json({"revision": state["revision"], "duplicate": duplicate})
    return 0


def _apply_execution_drift(
    result: dict[str, Any], state: dict[str, Any], wdd_dir: Path, *, state_path: str | None = None
) -> dict[str, Any]:
    """Empty a `next` result's actions and surface a drift blocker when
    config/constitution, an intake rung, or the plan-approval composite
    changed since it was last signed off.

    Shared by every `next` branch (execute, finalize, delivered) so none of
    them can emit an action whose recordWith/command then fails the drift
    gate on exit 5 -- a consumer following recordWith verbatim would
    otherwise be misled into believing the action is still runnable.
    Delivered-phase actions are already empty, but drift is surfaced there
    too (rather than only execute/finalize) so a caller polling `next` after
    delivery still sees *why* nothing is happening if governance drifted.

    One `plan_drift` case is not a dead end to surface as a blocker: a rung
    re-approval mid-execution (the remedy for `intake_drift`) cascades and
    clears every downstream rung, including `scope.approval` -- the ladder
    itself is then incomplete, so `intake_gate_status` reports `plan_drift`
    ("never composite-approved") even though the real next step is to walk
    the ladder back to complete, not to run `plan apply --approved-by`
    (which would itself refuse: apply_plan requires an intact ladder on a
    non-legacy scope). When that's the situation -- non-legacy, a scope
    exists, and the ladder isn't complete -- this emits the next ladder rung
    action instead (reusing setup.py's `_intake_ladder_action`, which reads
    `state["intake"]` the same way regardless of scope), so `next` stays
    runnable through the whole remedy walk. Once the ladder is complete
    again and the composite genuinely mismatches, the ordinary plan_drift
    blocker below still applies.

    The chokepoint in `main()` (`require_fresh_governance` then
    `require_fresh_epic_config` then `require_fresh_intake`) raises
    governance drift first, then epic config drift, then intake/plan drift,
    when more than one is present -- spec Sec2's precedence order (governance
    -> epic config -> intake artifacts -> plan composite). This surfaces the
    identical order by inserting at position 0 in the REVERSE of that
    precedence: intake/plan first, then epic config, then governance last --
    since each insert lands at position 0, the last one in (governance) ends
    up leading, matching the raise order, while any single-drift case is
    unaffected by the reordering.
    """
    gate = intake_gate_status(state, wdd_dir)
    if gate is not None:
        code, detail = gate
        if code == "plan_drift" and not intake_complete(state):
            prefix = "wddctl" + (f" --state {shlex.quote(state_path)}" if state_path else "")
            ladder_action = _intake_ladder_action(state, wdd_dir, prefix)
            if ladder_action is not None:
                result["actions"] = [ladder_action]
            else:
                # Defensive: intake_gate_status said the ladder isn't
                # complete, but _intake_ladder_action found no next rung.
                # Should not happen (both read the same state), but an
                # empty-actions blocker is a safer fallback than a stale
                # action list.
                result["actions"] = []
                result["blockers"].insert(
                    0,
                    {
                        "code": code,
                        "message": (
                            "the intake ladder is incomplete and no next rung could be "
                            "determined; run 'wddctl intake status' to inspect"
                        ),
                        **detail,
                    },
                )
        else:
            result["actions"] = []
            if code == "intake_drift":
                message = (
                    f"intake {detail['rung']} drifted since approval (recorded "
                    f"{detail['recorded']}, actual {detail['actual']}); re-run "
                    f"'wddctl intake {detail['rung']} ...' to re-approve -- re-approving a "
                    "rung this upstream cascades, clearing every rung recorded after it, so "
                    "walk those again in order too -- then 'wddctl plan apply --approved-by "
                    "NAME' to re-stamp"
                )
            else:
                message = (
                    "the applied plan's composite approval no longer matches its current "
                    "bytes (a brief or context file changed, or it was never composite-"
                    "approved); run 'wddctl plan apply --approved-by NAME' to re-stamp"
                )
            result["blockers"].insert(0, {"code": code, "message": message, **detail})

    epic_drift = epic_config_drift(state, wdd_dir)
    if epic_drift is not None:
        result["actions"] = []
        result["blockers"].insert(
            0,
            {
                "code": "epic_config_drift",
                "message": (
                    "the epic overlay (or the global config it layers over) changed "
                    "since intake.configure was approved; run 'wddctl intake configure "
                    "--approved-by NAME' (or --use-defaults --by NAME) to re-approve"
                ),
                **epic_drift,
            },
        )

    drift = governance_drift(state, wdd_dir)
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

    # `archiveBlocked` (spec Sec1's recovery matrix, row 5): a durable field
    # that survives the journal's own removal, precisely so this blocker
    # keeps showing up even after `recover_archive_transaction` has already
    # run (state is read via `load_recovered()` above, in `main()`, before
    # this function ever runs). Highest precedence of everything this
    # function inserts -- an archive stuck on an external collision is the
    # most actionable thing to surface, and (being resolved by re-running
    # `wddctl scope archive`, not by anything intake/config/governance-shaped)
    # never legitimately co-occurs with the other blockers above in a way
    # where a different one should win.
    archive_blocked = state.get("archiveBlocked")
    if archive_blocked is not None:
        result["actions"] = []
        result["blockers"].insert(
            0,
            {
                "code": "archive_blocked",
                "message": (
                    f"scope archive is blocked: {archive_blocked['collidingPath']} already "
                    f"exists, colliding with epic slug {archive_blocked['slug']!r}; move or "
                    "remove it, then re-run 'wddctl scope archive --repo .' to start a fresh "
                    "transaction"
                ),
                **archive_blocked,
            },
        )
    return result


def _apply_input_binding(
    result: dict[str, Any],
    state: dict[str, Any],
    wdd_dir: Path,
    *,
    state_path: str | None = None,
    repo: str = ".",
) -> dict[str, Any]:
    """Layer per-task `inputs_changed` actions onto a `next` result (spec Sec3
    input-version binding, Task 3).

    Mirrors `_apply_execution_drift`'s CLI-injection pattern for the same
    reason: `inputs_status` needs `.wdd`-relative file reads, so it cannot
    live in the file-free engine, and is computed and injected here instead.
    Runs AFTER `_apply_execution_drift` and unconditionally adds to whatever
    it left behind (including an already-emptied `actions` list from a
    scope-wide drift blocker): the two gates are orthogonal -- the composite
    gates the plan, per-task digests gate the task -- so a controller mid-
    remedy for one still needs to see the other. This is exactly the "a
    started task's context edit fires BOTH plan_drift and inputs_changed"
    interplay the plan calls out; the judgment below names re-stamping the
    plan first when both are present.

    Only ACTIVE_STATUSES tasks (in_progress/review/merge_ready) are checked:
    a `todo` task was never started, so it has no recorded `inputs` to have
    gone stale (`inputs_status` returns None for it regardless), and
    done/cancelled tasks are merged-evidence-is-history -- never gated,
    never surfaced, so they are skipped outright rather than relying on
    `inputs_status` to say None. For a mismatched task, its own gate action
    (if any) is removed from `result["actions"]` -- don't emit `run_review`
    for a task whose inputs are stale -- and replaced with one
    `inputs_changed` action; other tasks' actions are untouched.
    """
    prefix = "wddctl" + (f" --state {shlex.quote(state_path)}" if state_path else "")
    for task_id, task in sorted(state["tasks"].items()):
        if task["status"] not in ACTIVE_STATUSES:
            continue
        mismatch = inputs_status(state, wdd_dir, task_id)
        if mismatch is None:
            continue
        result["actions"] = [a for a in result["actions"] if a.get("task") != task_id]
        rebind_command = (
            f"{prefix} rebind --task {shlex.quote(task_id)} --by NAME "
            f"--repo {shlex.quote(repo)}"
        )
        result["actions"].append(
            {
                "task": task_id,
                "action": "inputs_changed",
                "path": mismatch["path"],
                "recorded": mismatch["recorded"],
                "actual": mismatch["actual"],
                "judgment": (
                    f"{mismatch['path']} changed since task {task_id}'s attempt was "
                    "dispatched: its recorded input digest no longer matches the current "
                    "bytes, so unmerged review/verification evidence for it is no longer "
                    "trustworthy. If a plan_drift blocker is also present above, re-stamp "
                    "the plan first ('wddctl plan apply --approved-by NAME'); either way, "
                    "then decide: rebind to accept the existing work as still valid, or "
                    "discard it and re-dispatch a fresh attempt (block, unblock, then start)."
                ),
                "recordWith": rebind_command,
            }
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = StateStore(args.state)
    try:
        verb = (args.command, _subcommand(args))
        governed = verb in GOVERNED_VERBS
        if args.command == "dispatch" and getattr(args, "probe_command", None) is not None:
            # spec Sec6's deliberately UNGOVERNED path: an explicit candidate
            # command the user just typed or approved in conversation, never
            # yet config -- `--probe NAME` and `--task ...` stay governed.
            governed = False
        # Resolve once per invocation (spec Sec2): the layered config
        # snapshot every evidence-recording/evidence-consuming handler below
        # needs (review/verify record+collect, finalize review/verify
        # record, finalize handoff, merge) is read from disk here, exactly
        # ONCE, and threaded down as `admission_layers` -- no handler below
        # re-reads config.json/the epic overlay itself. This is what closes
        # the check/use race: an overlay edited after this point cannot
        # influence evidence recorded during the same invocation, and the
        # digest recorded on evidence is the digest that was gate-checked
        # just above.
        admission_layers: dict[str, Any] | None = None
        if governed and store.exists():
            gated_state = store.read()
            require_fresh_governance(gated_state, store.path.parent)
            require_fresh_epic_config(gated_state, store.path.parent)
            require_fresh_intake(gated_state, store.path.parent)
            if config_path(store.path.parent).exists():
                admission_layers = load_layers(store.path.parent, gated_state.get("epic"))
            if verb in TASK_INPUT_GATED_VERBS:
                gated_task_id = getattr(args, "task", None)
                gated_task = (gated_state.get("tasks") or {}).get(gated_task_id) if gated_task_id else None
                # Merged evidence is history (spec Sec3): a done/cancelled
                # task is never gated here, matching `_apply_input_binding`'s
                # identical ACTIVE_STATUSES-only check for `next`'s
                # surfacing. Without this, a governed verb aimed at an
                # already-terminal task (a stale digest left over from
                # before it merged) would refuse with the wrong reason --
                # "inputs changed" instead of the transition's own "not
                # legal from done".
                if gated_task is not None and gated_task.get("status") in ACTIVE_STATUSES:
                    mismatch = inputs_status(gated_state, store.path.parent, gated_task_id)
                    if mismatch is not None:
                        raise IllegalTransition(
                            f"inputs changed for task {gated_task_id}: {mismatch['path']} "
                            f"recorded {mismatch['recorded']}, now {mismatch['actual']}; run "
                            f"'wddctl rebind --task {gated_task_id} --by NAME --repo .' to "
                            "accept the existing work as still valid, or re-dispatch a fresh "
                            "attempt (block, unblock, then start)"
                        )

        if args.command == "doctor":
            # Read-only (spec Sec1 locking layers): heals a crashed archive
            # transaction before reporting, rather than surfacing it only as
            # an orphan (`load_recovered` acquires its own lock -- this is
            # the ONLY state read in this command, so no nesting risk).
            state = store.load_recovered() if store.exists() else None
            _print_json(inspect_capabilities(store.path.parent, state))
            return 0

        if args.command == "init":
            _print_json(init_repository(store.path.parent, args.repo))
            return 0

        if args.command == "plan" and args.plan_command == "apply":
            if args.approved_by is not None and not args.approved_by.strip():
                raise ValidationError("--approved-by requires a non-empty name")
            plan, wdd_dir, lint_state = _overlaid_plan(args, store)
            target_branch = _base_equals_target_branch(plan, wdd_dir)
            if target_branch is not None:
                raise ValidationError(
                    f"scope.baseRef must differ from branching.targetBranch ({target_branch}): "
                    "the epic branch cannot be the branch it delivers into"
                )
            findings = lint_plan(plan, wdd_dir if wdd_dir.exists() else None, lint_state)
            if args.strict and findings:
                raise ValidationError(
                    "plan apply --strict: " + ", ".join(sorted({f["code"] for f in findings}))
                )
            result = apply_plan(
                store,
                plan,
                repo=args.repo,
                from_ref=args.from_ref,
                dry_run=args.dry_run,
                approved_by=args.approved_by,
                **_concurrency(args),
            )
            _print_json({**result, "lint": findings})
            return 0

        if args.command == "plan" and args.plan_command == "lint":
            plan_dict, wdd_dir, lint_state = _overlaid_plan(args, store)
            findings = lint_plan(plan_dict, wdd_dir if wdd_dir.exists() else None, lint_state)
            target_branch = _base_equals_target_branch(plan_dict, wdd_dir)
            if target_branch is not None:
                findings.append(
                    {
                        "code": "base_is_target",
                        "severity": "warning",
                        "message": (
                            f"scope.baseRef ({plan_dict['scope']['baseRef']}) equals "
                            f"branching.targetBranch ({target_branch}): the epic branch "
                            "cannot be the branch it delivers into -- finalize's human-merge "
                            "ancestry checks would be vacuous."
                        ),
                    }
                )
            if args.strict and findings:
                raise ValidationError(
                    "plan lint --strict: " + ", ".join(sorted({f["code"] for f in findings}))
                )
            _print_json({"findings": findings, "strict": args.strict})
            return 0

        if args.command == "plan" and args.plan_command == "template":
            # Pure emitter: no store.read()/write(), not in GOVERNED_VERBS,
            # so it runs mid-setup with no .wdd/ at all -- exactly like
            # --help. See wave_delivery.plan for the skeletons themselves.
            if args.brief:
                print(brief_skeleton(), end="")
            else:
                _print_json(plan_skeleton())
            return 0

        if args.command == "plan" and args.plan_command == "preview":
            state = state_from_plan(read_plan(args.plan)) if args.plan else store.read()
            if state["scope"] is None:
                raise ValidationError(
                    "no scope has been applied yet; preview a plan file with "
                    "--plan plan.json, or run plan apply first"
                )
            _print_json(
                {
                    "scope": state["scope"]["id"],
                    "maxConcurrent": state["scope"]["maxConcurrent"],
                    "note": "projected admission order; rounds are a view, not a gate",
                    "rounds": admission_schedule(state),
                }
            )
            return 0

        if args.command == "status":
            # Read-only: heals a crashed archive transaction first (spec
            # Sec1 locking layers), so a delivered scope stuck mid-archive
            # reports its true, post-recovery phase rather than a stale one.
            state = store.load_recovered()
            # Named, never actioned (epic park/resume spec: "status ... name
            # parked epics ... without emitting actions for them") -- an
            # operator asking "what's going on" should see a parked epic
            # exists even while a different one is active (or none is).
            parked_epics = sorted(state.get("parked") or {})
            if derived_phase(state) == "setup" and (state["scope"] is None or config_path(store.path.parent).exists()):
                config = load_config(store.path.parent)
                payload = {
                    "phase": "setup",
                    "openQuestions": len(config["openQuestions"]),
                    "constitution": state["constitution"]["status"],
                    "scope": (state.get("scope") or {}).get("id"),
                }
                if parked_epics:
                    payload["parkedEpics"] = parked_epics
                _print_json(payload)
                return 0
            if derived_phase(state) in {"finalize", "delivered"}:
                result = finalize_status(state)
                if parked_epics:
                    result["parkedEpics"] = parked_epics
                _print_json(result)
                return 0
            summary = status_summary(state)
            if parked_epics:
                summary["parkedEpics"] = parked_epics
            _print_json(summary) if args.json else print(_brief(summary))
            return 0

        if args.command == "next":
            # Read-only: same recovery-first doctrine as `status` above --
            # `next` is what a caller polls to discover the durable
            # `archive_blocked` resting state (_apply_execution_drift below),
            # so it must see a healed state, not a stale mid-transaction one.
            state = store.load_recovered()
            if derived_phase(state) == "setup" and (state["scope"] is None or config_path(store.path.parent).exists()):
                _print_json(
                    setup_next_actions(
                        state, store.path.parent, state_path=_state_option(args)
                    )
                )
                return 0
            if derived_phase(state) in {"finalize", "delivered"}:
                result = finalize_next_actions(
                    state, store.path.parent, str(args.repo), state_path=_state_option(args)
                )
                _apply_execution_drift(
                    result, state, store.path.parent, state_path=_state_option(args)
                )
                _print_json(result)
                return 0
            # `next` is read-only and outside GOVERNED_VERBS (no admission
            # chokepoint runs for it, so there is no admission_layers to
            # thread) -- but it must still reflect an epic override the same
            # as any governed verb would, so it resolves its own layered
            # view here rather than the bare global config (fix-round F2:
            # `next` used to report the un-overlaid mode/models, silently
            # hiding an active epic's `merge.surface`/`models` overrides).
            config = (
                load_layers(store.path.parent, state.get("epic"))["effective"]
                if config_path(store.path.parent).exists()
                else None
            )
            models = config["models"] if config else None
            mode = merge_settings(state, config)["mode"]
            result = bounded_next_actions(
                state,
                max_bytes=args.max_bytes,
                state_path=_state_option(args),
                repo=str(args.repo),
                models=models,
                mode=mode,
            )
            _apply_execution_drift(
                result, state, store.path.parent, state_path=_state_option(args)
            )
            _apply_input_binding(
                result, state, store.path.parent,
                state_path=_state_option(args), repo=str(args.repo),
            )
            _print_json(result)
            return 0

        if args.command == "render":
            state = store.read()
            render_to_path(
                state, args.output, state_path=_state_option(args), repo=str(args.repo)
            )
            _print_json({"rendered": str(args.output), "revision": state["revision"]})
            return 0

        if args.command == "start":
            config = _governed_config(admission_layers)
            result, duplicate = start_task(
                store,
                repo=args.repo,
                task_id=args.task,
                branch=args.branch,
                worktree=args.worktree,
                worktrees_root=_worktrees_root(config),
                **_concurrency(args),
            )
            # Materialize + record a fresh attempt snapshot on a genuine new
            # dispatch, OR whenever the task has no recorded snapshot yet --
            # self-healing for the crash window between task.started
            # committing and materialize_attempt/record_attempt succeeding.
            # Without the second condition, an idempotency-keyed retry after
            # that crash hits duplicate=True and would skip materialization
            # forever, leaving the task stuck in_progress with snapshot None.
            # A duplicate/reattach that already HAS a snapshot keeps the
            # original no-new-attempt behavior: a reattach (leases._reattach's
            # action is prefixed "reattach:") reconnects an in-progress task's
            # worktree without re-approving anything, and re-materializing
            # there would silently re-bind input digests to whatever the
            # source files currently are, defeating Task 3's rebind gate.
            is_reattach = str(result.get("action", "")).startswith("reattach:")
            pre_state = store.read()
            existing_task = pre_state["tasks"].get(args.task) or {}
            needs_attempt = (not duplicate and not is_reattach) or not existing_task.get(
                "snapshot"
            )
            if needs_attempt:
                # A ValidationError here (most commonly a legacy scope's brief
                # gone missing on disk) must not wedge the task: task.started
                # already committed above, so failing the command now would
                # leave it permanently in_progress -- a same-idempotency-key
                # retry would self-heal by re-running this exact block and
                # hit the identical failure forever. Legacy scopes record no
                # inputs anyway, so degrading to a snapshot-less success loses
                # no doctrine. A v5 scope with a genuinely missing/edited
                # source file is caught earlier, at the chokepoint's
                # require_fresh_intake/plan_drift check, so this except path
                # is reached in practice only by legacy scopes.
                try:
                    materialized = materialize_attempt(pre_state, store.path.parent, args.task)
                except ValidationError as error:
                    result = {**result, "snapshot": None, "snapshotError": str(error)}
                else:
                    state_after, _ = record_attempt(
                        store,
                        task_id=args.task,
                        snapshot=materialized["snapshot"],
                        inputs=materialized["inputs"],
                    )
                    recorded = state_after["tasks"][args.task]
                    result = {
                        **result,
                        "snapshot": recorded["snapshot"],
                        "inputsRecorded": len(recorded.get("inputs") or []),
                    }
            else:
                result = {
                    **result,
                    "snapshot": existing_task["snapshot"],
                    "inputsRecorded": len(existing_task.get("inputs") or []),
                }
            _print_json({**result, "duplicate": duplicate})
            return 0

        if args.command == "submit":
            warning = None
            pr = args.pr
            config = _governed_config(admission_layers)
            if pr is None:
                state = store.read()
                if merge_settings(state, config)["surface"] == "pr":
                    try:
                        task = state["tasks"][args.task]
                    except KeyError as error:
                        raise ValidationError(f"unknown task: {args.task}") from error
                    branch = task.get("branch")
                    if not branch:
                        raise IllegalTransition(
                            f"task {args.task} has no branch; run 'wddctl start' first"
                        )
                    base_ref = state["scope"].get("baseRef")
                    if not base_ref:
                        raise IllegalTransition("this scope has no configured base ref")
                    repo = require_repository(args.repo)
                    # Push before any PR attempt: a push failure must abort
                    # with no state change at all, so it happens outside the
                    # try/except that downgrades PR-creation failures to a
                    # warning. This runs on every submit, including
                    # resubmissions of a task that already has a real PR --
                    # the whole point of resubmitting is to publish a new head.
                    push_branch(repo, branch)
                    existing_pr = task.get("pr")
                    # Only call gh when there is no PR yet, or the recorded
                    # reference is still the branch:<sha> fallback left by an
                    # earlier 'gh pr create' failure (the upgrade path). A
                    # task that already has a real PR URL is being resubmitted
                    # to publish a new head, not to open a second PR: gh has
                    # no "replace this PR's URL" operation, so calling
                    # create_pr again would either fail or open a duplicate.
                    needs_pr_create = existing_pr is None or (
                        isinstance(existing_pr, str) and existing_pr.startswith("branch:")
                    )
                    if needs_pr_create:
                        head_sha = resolve_ref(repo, branch)
                        title = task.get("title") or args.task
                        body = f"wdd task {args.task}\nspec: {task.get('specPath')}\nhead: {head_sha}"
                        try:
                            pr = create_pr(repo, branch, base_ref, title, body)
                        except IllegalTransition as error:
                            # The branch is already pushed; losing the submission
                            # here would silently orphan real work. Record it the
                            # same way a local-surface submit would (a branch
                            # reference), and surface the failure as a warning
                            # instead of aborting.
                            warning = f"PR creation failed after push: {error}"
                    else:
                        pr = existing_pr
            result, duplicate = submit_task(
                store, repo=args.repo, task_id=args.task, pr=pr,
                worktrees_root=_worktrees_root(config), **_concurrency(args)
            )
            payload = {**result, "duplicate": duplicate}
            if warning:
                payload["warning"] = warning
            _print_json(payload)
            return 0

        if args.command == "review" and args.review_command == "record":
            findings = validate_findings(args.findings)
            state, duplicate = record_review(
                store,
                task_id=args.task,
                findings=findings,
                reviewer=args.reviewer,
                repo=args.repo,
                layers=admission_layers,
                **_concurrency(args),
            )
            task = state["tasks"][args.task]
            warning = None
            config = _governed_config(admission_layers)
            pr = task.get("pr")
            if (
                merge_settings(state, config)["surface"] == "pr"
                and isinstance(pr, str) and pr and not pr.startswith("branch:")
            ):
                try:
                    comment_pr(
                        require_repository(args.repo), pr, _review_comment_body(findings, args.reviewer)
                    )
                except IllegalTransition as error:
                    # State is already recorded above; the PR is a projection
                    # of it, not the source of truth, so a mirroring failure
                    # degrades to a warning instead of losing the review.
                    warning = f"PR comment failed: {error}"
            payload = {
                "revision": state["revision"],
                "duplicate": duplicate,
                "outcome": (task.get("review") or {}).get("outcome"),
                "status": task["status"],
            }
            if warning:
                payload["warning"] = warning
            _print_json(payload)
            return 0

        if args.command == "review" and args.review_command == "run":
            from .review import run_review

            _print_json(
                run_review(
                    store,
                    repo=args.repo,
                    task_id=args.task,
                    command=args.command_json,
                    output=args.output,
                )
            )
            return 0

        if args.command == "review" and args.review_command == "collect":
            from .review import collect_review

            state, duplicate = collect_review(
                store, task_id=args.task, result_paths=args.result, repo=args.repo,
                layers=admission_layers, **_concurrency(args)
            )
            _print_json({"revision": state["revision"], "duplicate": duplicate})
            return 0

        if args.command == "verify" and args.verify_command == "record":
            # Post-parse conflict check (spec AC-3): --run is a distinct
            # contract from the agent-reported --status/--command pair, and
            # argparse itself cannot express "mutually exclusive, with a
            # named message" -- checked before anything else in this branch
            # touches the repo or the store, so a bad combination never gets
            # far enough to execute a command (AC-2's "no command executes
            # on refusal" applies here too, transitively).
            if args.run:
                if args.status is not None or args.verify_command_text is not None:
                    raise ValidationError(
                        "verify record --run cannot be combined with --status or "
                        "--command: --run supplies the observed status and command "
                        "evidence itself"
                    )
                state, duplicate = _run_task_verification(
                    store, args=args, admission_layers=admission_layers
                )
            else:
                if args.status is None:
                    raise ValidationError(
                        "verify record requires --status (or --run to execute the "
                        "effective verification commands and record what was observed)"
                    )
                state, duplicate = record_verification(
                    store,
                    task_id=args.task,
                    status=args.status,
                    command=args.verify_command_text,
                    repo=args.repo,
                    layers=admission_layers,
                    **_concurrency(args),
                )
            _print_json(
                {
                    "revision": state["revision"],
                    "duplicate": duplicate,
                    "status": state["tasks"][args.task]["status"],
                }
            )
            return 0

        if args.command == "verify" and args.verify_command == "collect":
            from .review import collect_verification

            state, duplicate = collect_verification(
                store, task_id=args.task, result_path=args.result, repo=args.repo,
                layers=admission_layers, **_concurrency(args)
            )
            _print_json({"revision": state["revision"], "duplicate": duplicate})
            return 0

        if (
            args.command == "finalize"
            and args.finalize_command == "review"
            and args.finalize_review_command == "record"
        ):
            findings = validate_findings(args.findings)
            state, duplicate = record_final_review(
                store,
                findings=findings,
                reviewer=args.reviewer,
                repo=args.repo,
                layers=admission_layers,
                **_concurrency(args),
            )
            review = (state.get("finalize") or {}).get("review") or {}
            _print_json(
                {
                    "revision": state["revision"],
                    "duplicate": duplicate,
                    "outcome": review.get("outcome"),
                    "headSha": review.get("headSha"),
                }
            )
            return 0

        if (
            args.command == "finalize"
            and args.finalize_command == "verify"
            and args.finalize_verify_command == "record"
        ):
            if args.run:
                # Post-parse conflict check (spec AC-6), same discipline as
                # `verify record --run`'s --status/--command check above:
                # checked before anything else in this branch touches the
                # repo or the store, so a bad combination never gets far
                # enough to execute a command.
                if args.results is not None:
                    raise ValidationError(
                        "finalize verify record --run cannot be combined with "
                        "--results: --run supplies the observed results itself"
                    )
                if args.status is not None or args.finalize_verify_command_text is not None:
                    raise ValidationError(
                        "finalize verify record --run cannot be combined with "
                        "--status or --command: --run supplies the observed "
                        "results itself"
                    )
                state, duplicate = _run_final_verification(
                    store, args=args, admission_layers=admission_layers
                )
            else:
                state, duplicate = record_final_verification(
                    store,
                    status=args.status,
                    command=args.finalize_verify_command_text,
                    justification=args.justification,
                    results=args.results,
                    repo=args.repo,
                    layers=admission_layers,
                    **_concurrency(args),
                )
            verification = (state.get("finalize") or {}).get("verification") or {}
            _print_json(
                {
                    "revision": state["revision"],
                    "duplicate": duplicate,
                    "status": verification.get("status"),
                    "headSha": verification.get("headSha"),
                }
            )
            return 0

        if args.command == "finalize" and args.finalize_command == "handoff":
            _print_json(
                prepare_handoff(
                    store, repo=args.repo, layers=admission_layers, **_concurrency(args)
                )
            )
            return 0

        if args.command == "finalize" and args.finalize_command == "delivered":
            _print_json(
                record_delivered(
                    store, by=args.by, repo=args.repo, layers=admission_layers,
                    **_concurrency(args)
                )
            )
            return 0

        if args.command == "finalize" and args.finalize_command == "status":
            _print_json(finalize_status(store.read()))
            return 0

        if args.command == "epic" and args.epic_command == "new":
            state, duplicate = create_epic(
                store,
                store.path.parent,
                slug=args.slug,
                title=args.title,
                **_concurrency(args),
            )
            _print_json(
                {"epic": state["epic"], "revision": state["revision"], "duplicate": duplicate}
            )
            return 0

        if args.command == "epic" and args.epic_command == "park":
            config = _governed_config(admission_layers)
            state, duplicate = park_epic(
                store,
                repo=args.repo,
                worktrees_root=_worktrees_root(config),
                **_concurrency(args),
            )
            _print_json({"revision": state["revision"], "duplicate": duplicate})
            return 0

        if args.command == "epic" and args.epic_command == "resume":
            state, duplicate = resume_epic(
                store,
                slug=args.slug,
                wdd_dir=store.path.parent,
                **_concurrency(args),
            )
            _print_json(
                {"epic": state["epic"], "revision": state["revision"], "duplicate": duplicate}
            )
            return 0

        if args.command == "intake" and args.intake_command == "spec":
            state, duplicate = record_spec(
                store, store.path.parent, approved_by=args.approved_by, **_concurrency(args)
            )
            _print_json(
                {
                    "revision": state["revision"],
                    "duplicate": duplicate,
                    "criteria": state["intake"]["spec"]["criteria"],
                }
            )
            return 0

        if args.command == "intake" and args.intake_command == "research":
            # Named, explicit validation instead of letting the ambiguous
            # combinations reach record_research's own generic "exactly one
            # of --done/--skip" check: that check only looks at whether
            # done_artifacts/skip_reason ended up non-None below, which is
            # not the same question as which flags the caller actually
            # passed. In particular "--done --skip --reason r" (no
            # --artifacts) computes done_artifacts=None (no --artifacts) and
            # skip_reason="r" (--skip present) -- exactly one is non-None, so
            # the generic check would pass and silently record a skip despite
            # --done also being requested.
            if args.done and args.skip:
                raise ValidationError(
                    "intake research: --done and --skip are mutually exclusive; pass exactly one"
                )
            if not args.done and not args.skip:
                raise ValidationError(
                    "intake research requires exactly one of --done or --skip"
                )
            if args.done and not args.artifacts:
                raise ValidationError("intake research --done requires --artifacts PATH...")
            if args.skip and not args.reason:
                raise ValidationError("intake research --skip requires --reason '...'")
            state, duplicate = record_research(
                store,
                store.path.parent,
                by=args.by,
                done_artifacts=args.artifacts if args.done else None,
                skip_reason=args.reason if args.skip else None,
                **_concurrency(args),
            )
            _print_json({"revision": state["revision"], "duplicate": duplicate})
            return 0

        if args.command == "intake" and args.intake_command == "design":
            state, duplicate = record_design(
                store,
                store.path.parent,
                approved_by=args.approved_by,
                deliverable_command=args.deliverable_command,
                **_concurrency(args),
            )
            _print_json({"revision": state["revision"], "duplicate": duplicate})
            return 0

        if args.command == "intake" and args.intake_command == "configure":
            state, duplicate = record_configure(
                store,
                store.path.parent,
                approved_by=args.approved_by,
                use_defaults=args.use_defaults,
                by=args.by,
                **_concurrency(args),
            )
            _print_json(
                {
                    "revision": state["revision"],
                    "duplicate": duplicate,
                    "sha256": state["intake"]["configure"]["sha256"],
                }
            )
            return 0

        if args.command == "intake" and args.intake_command == "status":
            _print_json(intake_status(store.read()))
            return 0

        if args.command == "scope" and args.scope_command == "archive":
            _print_json(archive_scope(store, repo=args.repo, **_concurrency(args)))
            return 0

        if args.command == "freshness" and args.freshness_command == "check":
            _print_json(
                check_freshness(
                    args.repo,
                    base_ref=args.base,
                    head_ref=args.head,
                    conflict_domains=args.conflict_domain,
                )
            )
            return 0

        if args.command == "freshness" and args.freshness_command == "record":
            state, duplicate, result = record_freshness(
                store, repo=args.repo, task_id=args.task, **_concurrency(args)
            )
            _print_json(
                {
                    "classification": result["classification"],
                    "revision": state["revision"],
                    "duplicate": duplicate,
                }
            )
            return 0

        if args.command == "refresh":
            config = _governed_config(admission_layers)
            result = refresh_task(
                store, repo=args.repo, task_id=args.task,
                worktrees_root=_worktrees_root(config), **_concurrency(args)
            )
            state = store.read()
            if merge_settings(state, config)["surface"] == "pr":
                branch = (state["tasks"].get(args.task) or {}).get("branch")
                if branch:
                    try:
                        push_branch(require_repository(args.repo), branch)
                    except IllegalTransition as error:
                        # The refresh itself already committed the new head to
                        # state; the remote task branch is a projection of that
                        # fact (same contract as merge's push of the advanced
                        # base below), so a push failure degrades to a warning
                        # instead of losing the refresh. Leaving the remote
                        # stale here would make a human merge a stale PR
                        # (never satisfying --observed ancestry) or a
                        # pr-surface review mirror onto a stale diff.
                        result["warning"] = f"push of {branch} to origin failed: {error}"
            _print_json(result)
            return 0

        if args.command == "merge":
            if args.observed:
                _print_json(
                    observe_merge(
                        store, repo=args.repo, task_id=args.task, layers=admission_layers,
                        **_concurrency(args)
                    )
                )
                return 0
            state = store.read()
            config = _governed_config(admission_layers)
            settings = merge_settings(state, config)
            if settings["mode"] == "human":
                try:
                    task = state["tasks"][args.task]
                except KeyError as error:
                    raise ValidationError(f"unknown task: {args.task}") from error
                reference = human_reference(task)
                raise IllegalTransition(
                    f"merge mode is human: {reference} must be merged by its human owner "
                    "directly; wddctl will not merge it. Once merged, run "
                    f"'wddctl merge --task {args.task} --repo {args.repo} --observed' to record it."
                )
            result = merge_task(
                store, repo=args.repo, task_id=args.task, layers=admission_layers,
                **_concurrency(args)
            )
            if settings["surface"] == "pr":
                base_ref = store.read()["scope"].get("baseRef")
                if base_ref:
                    try:
                        push_branch(require_repository(args.repo), base_ref)
                    except IllegalTransition as error:
                        # The merge itself already committed to state; pushing
                        # the advanced base is a projection of that fact and
                        # is idempotent to retry, so a push failure degrades
                        # to a warning instead of losing the merge.
                        result["warning"] = f"push of {base_ref} to origin failed: {error}"
            _print_json(result)
            return 0

        if args.command == "rebind":
            state, duplicate = rebind_attempt(
                store,
                task_id=args.task,
                wdd_dir=store.path.parent,
                by=args.by,
                **_concurrency(args),
            )
            task = state["tasks"][args.task]
            _print_json(
                {
                    "revision": state["revision"],
                    "duplicate": duplicate,
                    "inputsRecorded": len(task.get("inputs") or []),
                }
            )
            return 0

        if args.command == "dispatch":
            selected = [
                flag
                for flag, value in (
                    ("--probe-command", args.probe_command),
                    ("--probe", args.probe),
                    ("--task", args.task),
                )
                # `is not None`, matching main()'s chokepoint predicate for
                # --probe-command exactly (getattr(..., None) is not None):
                # a plain truthiness check here would disagree with it the
                # moment either predicate's value type admits a falsy-but-
                # present value (e.g. an empty-but-non-None argv).
                if value is not None
            ]
            if len(selected) != 1:
                raise ValidationError(
                    "dispatch requires exactly one of --probe-command, --probe, or --task"
                )
            timeout_kwargs: dict[str, Any] = {} if args.timeout is None else {"timeout": args.timeout}

            if args.probe_command is not None:
                command = args.probe_command
                result = probe_command(command, **timeout_kwargs)
                result["digest"] = runner_command_digest(command)
                if result["ok"] and store.exists():
                    record_probe(store, command=command, **_concurrency(args))
                    result["recorded"] = True
                else:
                    result["recorded"] = False
                    if result["ok"]:
                        result["note"] = (
                            "no state.json yet; this probe will be re-verified once the "
                            "runner is registered and state exists"
                        )
                _print_json(result)
                return 0

            if args.probe is not None:
                config = load_config(store.path.parent)
                runners = config.get("runners") or {}
                if args.probe not in runners:
                    raise ValidationError(f"unknown runner: {args.probe}")
                command = runners[args.probe]["command"]
                result = probe_command(command, **timeout_kwargs)
                result["digest"] = runner_command_digest(command)
                result["runner"] = args.probe
                if result["ok"] and store.exists():
                    record_probe(store, command=command, **_concurrency(args))
                    result["recorded"] = True
                else:
                    result["recorded"] = False
                _print_json(result)
                return 0

            if not args.role:
                raise ValidationError("dispatch --task requires --role worker|reviewer")
            state = store.read()
            # fix-round F2: this used to re-read the bare global config,
            # silently dropping an active epic's models.implementation/
            # models.review override at the one point that actually execs a
            # worker/reviewer -- the chokepoint above already resolved the
            # layered snapshot for this exact invocation.
            config = _governed_config(admission_layers)
            result = dispatch_task(
                state,
                config,
                repo=args.repo,
                wdd_dir=store.path.parent,
                task_id=args.task,
                role=args.role,
                **timeout_kwargs,
            )
            _print_json(result)
            return 0

        if args.command == "release":
            config = (
                load_config(store.path.parent)
                if config_path(store.path.parent).exists()
                else None
            )
            result, duplicate = release_task(
                store,
                repo=args.repo,
                task_id=args.task,
                keep_worktree=args.keep_worktree,
                worktrees_root=_worktrees_root(config),
                **_concurrency(args),
            )
            _print_json({**result, "duplicate": duplicate})
            return 0

        if args.command == "block":
            return _simple_event(store, args, "task.blocked", {"reason": args.reason})

        if args.command == "unblock":
            return _simple_event(store, args, "task.unblocked", {})

        if args.command == "cancel":
            return _simple_event(store, args, "task.cancelled", {})

        if args.command == "note":
            return _simple_event(store, args, "note.added", {"note": args.note})

        if args.command == "reconcile" and args.reconcile_command == "status":
            state = store.read()
            _print_json(
                {
                    "due": status_summary(state)["reconcile"]["due"],
                    "mergesSinceCheckpoint": state["reconcile"]["mergesSinceCheckpoint"],
                    "everyNMerges": state["reconcile"]["everyNMerges"],
                    "pendingNotes": state["reconcile"]["pendingNotes"],
                }
            )
            return 0

        if args.command == "reconcile" and args.reconcile_command == "done":
            state, duplicate = apply_event(
                store,
                event_type="reconcile.completed",
                task_id=None,
                data={},
                **_concurrency(args),
            )
            _print_json({"revision": state["revision"], "duplicate": duplicate})
            return 0

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

        if args.command == "migrate":
            if args.apply == args.dry_run:
                parser.error("choose exactly one of --dry-run or --apply")
            runner = apply_migration if args.apply else plan_migration
            _print_json(runner(args.state, review_policy=args.review_policy))
            return 0

        if args.command == "monitor":
            config = (
                load_config(store.path.parent)
                if config_path(store.path.parent).exists()
                else None
            )
            _print_json(
                monitor_once(
                    store, repo=args.repo, dry_run=args.dry_run, state_path=_state_option(args),
                    worktrees_root=_worktrees_root(config),
                )
            )
            return 0

        if args.command == "event" and args.event_command == "apply":
            if args.event == "task.merged":
                # Keeps the Git-verified-merge guarantee true for the CLI surface:
                # completion cannot be asserted, only proved by 'wddctl merge'.
                raise IllegalTransition(
                    "use 'wddctl merge --task ID --repo .' so live Git proves the merge"
                )
            if args.event == "scope.delivered":
                # Same guarantee, at scope granularity: delivered can only be
                # asserted by 'wddctl finalize delivered', which proves the
                # human's final merge with live Git ancestry.
                raise IllegalTransition(
                    "use 'wddctl finalize delivered --by NAME --repo .' so live Git proves the merge"
                )
            return _simple_event(store, args, args.event, args.data)

        if args.command == "config":
            wdd_dir = store.path.parent
            if getattr(args, "epic", False):
                # epic overlay resolution (epic-scoped-state plan, Task 2,
                # spec Sec2) -- state.epic is a v6 field (Task 3).
                state = store.read() if store.exists() else None
                epic = (state or {}).get("epic")
                if epic is None:
                    # Refuses for BOTH get and set (epic park/resume spec:
                    # "config get/set --epic with no active epic refuses --
                    # and the refusal names any parked slugs and 'epic
                    # resume'"). Without this, `get --epic` on a PARKED
                    # epic silently read the global layer, telling an
                    # operator inspecting the epic's own overlay nothing
                    # about the real (parked) state.
                    parked = sorted((state or {}).get("parked") or {})
                    hint = (
                        f"; parked epic(s): {parked} -- resume one with 'wddctl epic "
                        "resume --slug SLUG', or start a new one with 'wddctl epic new "
                        "--slug SLUG'"
                        if parked
                        else "; run 'wddctl epic new --slug SLUG' first"
                    )
                    raise ValidationError(
                        f"config {args.config_command} --epic: no active epic "
                        f"(state.epic is null){hint}"
                    )
                if args.config_command == "get":
                    layers = load_layers(wdd_dir, epic)
                    value, source = resolve_config_source(layers, args.path)
                    _print_json({"path": args.path, "value": value, "source": source})
                    return 0
                if args.config_command == "set":
                    layers = load_layers(wdd_dir, epic)
                    try:
                        value = json.loads(args.value)
                    except json.JSONDecodeError:
                        value = args.value
                    patch = set_overlay_value(
                        layers["overlay"], args.path, value, effective=layers["effective"]
                    )
                    derived = derive_effective(layers, patch)
                    save_overlay(wdd_dir, epic, derived["overlay"])
                    _print_json(
                        {
                            "path": args.path,
                            "value": get_value(derived["effective"], args.path),
                            "epic": epic,
                        }
                    )
                    return 0
            config = load_config(wdd_dir)
            if args.config_command == "get":
                # Resolve through the same hydrated view load_layers'
                # `effective` uses (fix-round P2): a legacy config.json
                # predating an optional key (verification.timeoutSeconds,
                # runners, worktrees) has no in-tool path to read it via a
                # raw get_value walk otherwise -- 'unknown path' every time,
                # with nothing to backfill it. Pure read: this never writes,
                # so the file and governance fingerprint stay untouched.
                _print_json(get_value(_hydrate_optional_sections(config), args.path))
                return 0
            if args.config_command == "show":
                _print_json(config)
                return 0
            if args.config_command == "set":
                try:
                    value = json.loads(args.value)
                except json.JSONDecodeError:
                    value = args.value
                # Same hydration as `get` above, so a missing optional key
                # can be created rather than refused by name -- an explicit
                # `set` IS supposed to move the governance fingerprint
                # (that's governance working), unlike a mere `get`.
                updated = set_value(_hydrate_optional_sections(config), args.path, value)
                save_config(wdd_dir, updated)
                _print_json(
                    {
                        "path": args.path,
                        "value": get_value(updated, args.path),
                        "openQuestions": len(updated["openQuestions"]),
                    }
                )
                return 0

        if args.command == "constitution" and args.constitution_command in {"ratify", "amend"}:
            wdd_dir = store.path.parent
            result: dict[str, Any] = {}
            # Legacy fallback: repos that predate config.json (e.g. no
            # 'wddctl init' schema-v4 setup) have no governance file to
            # fingerprint. Keep signing whatever the caller pins via
            # --proposal/--decision-fingerprint for them; new repos with a
            # config.json get the governance fingerprint and the
            # openQuestions gate below.
            if config_path(wdd_dir).exists():
                check_ratifiable(wdd_dir)
                fingerprint = governance_fingerprint(wdd_dir)
                if args.decision_fingerprint and args.decision_fingerprint != fingerprint:
                    raise ValidationError(
                        "provided --decision-fingerprint does not match the current "
                        "config.json + constitution.md; re-read the files before ratifying"
                    )
                if args.proposal:
                    result["warning"] = (
                        "--proposal is deprecated; the fingerprint now covers "
                        ".wdd/config.json + constitution.md"
                    )
            else:
                fingerprint = (
                    read_proposal(args.proposal)["decisionFingerprint"]
                    if args.proposal
                    else args.decision_fingerprint
                )
            state, duplicate = apply_event(
                store,
                event_type=f"constitution.{'ratified' if args.constitution_command == 'ratify' else 'amended'}",
                task_id=None,
                data={"by": args.by, "decisionFingerprint": fingerprint},
                **_concurrency(args),
            )
            result.update(
                {
                    "revision": state["revision"],
                    "duplicate": duplicate,
                    "decisionFingerprint": fingerprint,
                }
            )
            _print_json(result)
            return 0

        if args.command == "constitution" and args.constitution_command == "probe":
            proposal = probe_repository(args.root)
            if args.output:
                write_proposal(args.output, proposal)
                proposal["writtenTo"] = str(args.output)
            _print_json(proposal)
            return 0

        if args.command == "constitution" and args.constitution_command == "status":
            proposal = read_proposal(args.proposal) if args.proposal else None
            _print_json(ratification_status(store.read(), proposal))
            return 0
    except WaveDeliveryError as error:
        print(f"wddctl: {error}", file=sys.stderr)
        return error.exit_code
    parser.error("unknown command")
    return 2
