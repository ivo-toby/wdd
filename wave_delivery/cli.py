"""wddctl: the deterministic half of Wave-Driven Development.

The agent loop is: `wddctl next` -> do the one thing that needs judgment ->
`wddctl <verb>` -> repeat. Every mechanical action is a verb here, so no
mechanical step depends on prompt interpretation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .config import (
    check_ratifiable,
    config_path,
    get_value,
    governance_drift,
    governance_fingerprint,
    load_config,
    merge_settings,
    require_fresh_governance,
    save_config,
    set_value,
)
from .constitution import probe_repository, ratification_status, read_proposal, write_proposal
from .setup import init_repository, migrate_governance, setup_next_actions
from .doctor import inspect_capabilities
from .engine import (
    admission_schedule,
    apply_event,
    bounded_next_actions,
    human_reference,
    render_to_path,
    status_summary,
)
from .errors import IllegalTransition, ValidationError, WaveDeliveryError
from .schema import derived_phase
from .finalize import (
    finalize_next_actions,
    finalize_status,
    prepare_handoff,
    record_delivered,
    record_final_review,
    record_final_verification,
)
from .freshness import check_freshness, record_freshness
from .git import require_repository, resolve_ref
from .github import comment_pr, create_pr, push_branch
from .leases import release_task, start_task, submit_task
from .lint import lint_plan
from .merge import merge_task, observe_merge, refresh_task
from .migration import apply_migration, plan_migration
from .monitor import monitor_once
from .plan import apply_config_defaults, apply_plan, apply_risk_rules, read_plan, state_from_plan
from .review import record_review, record_verification, validate_findings
from .store import StateStore


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
    # The escape hatch bypasses transitions, not governance.
    ("event", "apply"),
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wddctl", description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
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
    verify_record.add_argument("--status", required=True, choices=("passed", "failed", "unavailable"))
    # dest must not be "command": that is the top-level subparser destination.
    verify_record.add_argument(
        "--command", dest="verify_command_text", help="the command that produced this result"
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
    finalize_verify_record.add_argument(
        "--status", required=True, choices=("passed", "failed", "unavailable")
    )
    # dest must not be "command": that is the top-level subparser destination.
    finalize_verify_record.add_argument(
        "--command", dest="finalize_verify_command_text", help="the command that produced this result"
    )
    finalize_verify_record.add_argument(
        "--justification",
        help="required when --status unavailable and no config "
        "verification.unavailableJustification exists",
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
    config_set = config_subparsers.add_parser(
        "set", help="set one value (JSON literal, or bare string fallback)"
    )
    config_set.add_argument("path", help="dotted path, e.g. merge.surface")
    config_set.add_argument("value", help='e.g. local or \'["pytest -q"]\'')
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


def _overlaid_plan(args: argparse.Namespace, store: StateStore) -> tuple[dict[str, Any], Path]:
    """Read a plan file and apply the same config overlays 'plan apply' does.

    Shared by 'plan lint' and 'plan apply' so lint always sees exactly what
    apply would see: raw scope defaults, then risk-rule overrides.
    """
    plan = read_plan(args.plan)
    wdd_dir = store.path.parent
    if config_path(wdd_dir).exists():
        raw_plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        config = load_config(wdd_dir)
        plan = apply_config_defaults(plan, raw_plan["scope"], config)
        state = store.read() if store.exists() else None
        plan = apply_risk_rules(plan, config, state)
    return plan, wdd_dir


def _simple_event(store: StateStore, args: argparse.Namespace, event: str, data: dict[str, Any]) -> int:
    state, duplicate = apply_event(
        store, event_type=event, task_id=getattr(args, "task", None), data=data, **_concurrency(args)
    )
    _print_json({"revision": state["revision"], "duplicate": duplicate})
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = StateStore(args.state)
    try:
        if (args.command, _subcommand(args)) in GOVERNED_VERBS and store.exists():
            require_fresh_governance(store.read(), store.path.parent)

        if args.command == "doctor":
            state = store.read() if store.exists() else None
            _print_json(inspect_capabilities(store.path.parent, state))
            return 0

        if args.command == "init":
            _print_json(init_repository(store.path.parent, args.repo))
            return 0

        if args.command == "plan" and args.plan_command == "apply":
            if args.approved_by is not None and not args.approved_by.strip():
                raise ValidationError("--approved-by requires a non-empty name")
            plan, wdd_dir = _overlaid_plan(args, store)
            findings = lint_plan(plan, wdd_dir if wdd_dir.exists() else None)
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
            plan_dict, wdd_dir = _overlaid_plan(args, store)
            findings = lint_plan(plan_dict, wdd_dir if wdd_dir.exists() else None)
            if args.strict and findings:
                raise ValidationError(
                    "plan lint --strict: " + ", ".join(sorted({f["code"] for f in findings}))
                )
            _print_json({"findings": findings, "strict": args.strict})
            return 0

        if args.command == "plan" and args.plan_command == "preview":
            state = state_from_plan(read_plan(args.plan)) if args.plan else store.read()
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
            state = store.read()
            if derived_phase(state) == "setup" and (state["scope"] is None or config_path(store.path.parent).exists()):
                config = load_config(store.path.parent)
                _print_json(
                    {
                        "phase": "setup",
                        "openQuestions": len(config["openQuestions"]),
                        "constitution": state["constitution"]["status"],
                        "scope": (state.get("scope") or {}).get("id"),
                    }
                )
                return 0
            if derived_phase(state) in {"finalize", "delivered"}:
                _print_json(finalize_status(state))
                return 0
            summary = status_summary(state)
            _print_json(summary) if args.json else print(_brief(summary))
            return 0

        if args.command == "next":
            state = store.read()
            if derived_phase(state) == "setup" and (state["scope"] is None or config_path(store.path.parent).exists()):
                _print_json(
                    setup_next_actions(
                        state, store.path.parent, state_path=_state_option(args)
                    )
                )
                return 0
            if derived_phase(state) in {"finalize", "delivered"}:
                _print_json(
                    finalize_next_actions(
                        state, store.path.parent, str(args.repo), state_path=_state_option(args)
                    )
                )
                return 0
            config = (
                load_config(store.path.parent)
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
            return 0

        if args.command == "render":
            state = store.read()
            render_to_path(
                state, args.output, state_path=_state_option(args), repo=str(args.repo)
            )
            _print_json({"rendered": str(args.output), "revision": state["revision"]})
            return 0

        if args.command == "start":
            result, duplicate = start_task(
                store,
                repo=args.repo,
                task_id=args.task,
                branch=args.branch,
                worktree=args.worktree,
                **_concurrency(args),
            )
            _print_json({**result, "duplicate": duplicate})
            return 0

        if args.command == "submit":
            warning = None
            pr = args.pr
            if pr is None:
                state = store.read()
                config = (
                    load_config(store.path.parent)
                    if config_path(store.path.parent).exists()
                    else None
                )
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
                store, repo=args.repo, task_id=args.task, pr=pr, **_concurrency(args)
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
                **_concurrency(args),
            )
            task = state["tasks"][args.task]
            warning = None
            config = (
                load_config(store.path.parent)
                if config_path(store.path.parent).exists()
                else None
            )
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
                store, task_id=args.task, result_paths=args.result, repo=args.repo, **_concurrency(args)
            )
            _print_json({"revision": state["revision"], "duplicate": duplicate})
            return 0

        if args.command == "verify" and args.verify_command == "record":
            state, duplicate = record_verification(
                store,
                task_id=args.task,
                status=args.status,
                command=args.verify_command_text,
                repo=args.repo,
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
                store, task_id=args.task, result_path=args.result, repo=args.repo, **_concurrency(args)
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
            state, duplicate = record_final_verification(
                store,
                status=args.status,
                command=args.finalize_verify_command_text,
                justification=args.justification,
                repo=args.repo,
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
            _print_json(prepare_handoff(store, repo=args.repo, **_concurrency(args)))
            return 0

        if args.command == "finalize" and args.finalize_command == "delivered":
            _print_json(
                record_delivered(store, by=args.by, repo=args.repo, **_concurrency(args))
            )
            return 0

        if args.command == "finalize" and args.finalize_command == "status":
            _print_json(finalize_status(store.read()))
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
            result = refresh_task(store, repo=args.repo, task_id=args.task, **_concurrency(args))
            state = store.read()
            config = (
                load_config(store.path.parent)
                if config_path(store.path.parent).exists()
                else None
            )
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
                    observe_merge(store, repo=args.repo, task_id=args.task, **_concurrency(args))
                )
                return 0
            state = store.read()
            config = (
                load_config(store.path.parent)
                if config_path(store.path.parent).exists()
                else None
            )
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
            result = merge_task(store, repo=args.repo, task_id=args.task, **_concurrency(args))
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

        if args.command == "release":
            result, duplicate = release_task(
                store,
                repo=args.repo,
                task_id=args.task,
                keep_worktree=args.keep_worktree,
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
            _print_json(
                monitor_once(
                    store, repo=args.repo, dry_run=args.dry_run, state_path=_state_option(args)
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
