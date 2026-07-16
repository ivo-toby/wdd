"""Command-line interface for the initial deterministic WDD controller."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .constitution import probe_repository, ratification_status, read_proposal, write_proposal
from .engine import apply_event, bounded_next_actions, render_to_path, status_summary
from .errors import WaveDeliveryError
from .freshness import check_freshness
from .leases import ensure_lease, release_lease
from .monitor import monitor_once
from .migration import apply_migration, build_migration_plan, rollback_migration
from .schema import new_state, task_state
from .store import StateStore


def _json_argument(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError(f"invalid JSON: {error.msg}") from error
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("JSON data must be an object")
    return parsed


def _json_list_argument(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError(f"invalid JSON: {error.msg}") from error
    if not isinstance(parsed, list) or not all(isinstance(item, str) and item for item in parsed):
        raise argparse.ArgumentTypeError("JSON command must be a non-empty string array")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wdctl", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="create a schema-v2 controller state file")
    init.add_argument("--state", required=True, type=Path)
    init.add_argument("--scope-id", required=True)
    init.add_argument("--scope-kind", choices=("epic", "micro_wave"), default="epic")
    init.add_argument("--task", action="append", default=[])

    status = subparsers.add_parser("status", help="show a concise state summary")
    status.add_argument("--state", required=True, type=Path)
    status.add_argument("--brief", action="store_true")
    status.add_argument("--json", action="store_true")

    next_command = subparsers.add_parser("next", help="show executable next actions and blockers")
    next_command.add_argument("--state", required=True, type=Path)
    next_command.add_argument("--max-bytes", type=int, default=2048)

    render = subparsers.add_parser("render", help="render a controller-state Markdown projection")
    render.add_argument("--state", required=True, type=Path)
    render.add_argument("--output", required=True, type=Path)

    monitor = subparsers.add_parser("monitor", help="perform one cheap Git observation tick")
    monitor.add_argument("--once", action="store_true", required=True)
    monitor.add_argument("--state", required=True, type=Path)
    monitor.add_argument("--repo", required=True, type=Path)
    monitor.add_argument("--dry-run", action="store_true")

    lease = subparsers.add_parser("lease", help="acquire or release a task branch/worktree lease")
    lease_subparsers = lease.add_subparsers(dest="lease_command", required=True)
    ensure = lease_subparsers.add_parser("ensure", help="create or reuse an isolated worker worktree")
    ensure.add_argument("--state", required=True, type=Path)
    ensure.add_argument("--repo", required=True, type=Path)
    ensure.add_argument("--task", required=True)
    ensure.add_argument("--branch")
    ensure.add_argument("--worktree", type=Path)
    ensure.add_argument("--base-ref")
    ensure.add_argument("--idempotency-key", required=True)
    ensure.add_argument("--expected-revision", required=True, type=int)
    ensure.add_argument("--dry-run", action="store_true")
    release = lease_subparsers.add_parser("release", help="safely remove or retain a worker worktree")
    release.add_argument("--state", required=True, type=Path)
    release.add_argument("--repo", required=True, type=Path)
    release.add_argument("--task", required=True)
    release.add_argument("--idempotency-key", required=True)
    release.add_argument("--expected-revision", required=True, type=int)
    release.add_argument("--keep-worktree", action="store_true")

    freshness = subparsers.add_parser("freshness", help="classify a task branch against its base")
    freshness_subparsers = freshness.add_subparsers(dest="freshness_command", required=True)
    for command_name, help_text in (("check", "inspect freshness without mutating state"), ("record", "inspect and record freshness evidence")):
        command = freshness_subparsers.add_parser(command_name, help=help_text)
        command.add_argument("--repo", required=True, type=Path)
        command.add_argument("--base", required=True)
        command.add_argument("--head", required=True)
        command.add_argument("--conflict-domain", action="append", default=[])
        if command_name == "record":
            command.add_argument("--state", required=True, type=Path)
            command.add_argument("--task", required=True)
            command.add_argument("--idempotency-key", required=True)
            command.add_argument("--expected-revision", required=True, type=int)

    review = subparsers.add_parser("review", help="run or collect normalized review results")
    review_subparsers = review.add_subparsers(dest="review_command", required=True)
    review_run = review_subparsers.add_parser("run", help="run one configured reviewer against frozen SHAs")
    review_run.add_argument("--state", required=True, type=Path)
    review_run.add_argument("--repo", required=True, type=Path)
    review_run.add_argument("--task", required=True)
    review_run.add_argument("--command-json", required=True, type=_json_list_argument)
    review_run.add_argument("--output", required=True, type=Path)
    review_run.add_argument("--base-sha")
    review_collect = review_subparsers.add_parser("collect", help="aggregate one or more review results once")
    review_collect.add_argument("--state", required=True, type=Path)
    review_collect.add_argument("--task", required=True)
    review_collect.add_argument("--result", required=True, type=Path, action="append")
    review_collect.add_argument("--idempotency-key", required=True)
    review_collect.add_argument("--expected-revision", required=True, type=int)

    verify = subparsers.add_parser("verify", help="collect normalized verification evidence")
    verify_subparsers = verify.add_subparsers(dest="verify_command", required=True)
    verify_collect = verify_subparsers.add_parser("collect", help="record verification evidence")
    verify_collect.add_argument("--state", required=True, type=Path)
    verify_collect.add_argument("--task", required=True)
    verify_collect.add_argument("--result", required=True, type=Path)
    verify_collect.add_argument("--idempotency-key", required=True)
    verify_collect.add_argument("--expected-revision", required=True, type=int)

    migration = subparsers.add_parser("migrate", help="plan or apply a v1-to-v2 migration")
    migration.add_argument("--state", type=Path)
    migration.add_argument("--to", type=int, choices=(2,), default=2)
    migration.add_argument("--dry-run", action="store_true")
    migration.add_argument("--apply", action="store_true")
    migration.add_argument("--rollback", type=Path)

    event = subparsers.add_parser("event", help="apply a legal state transition")
    event_subparsers = event.add_subparsers(dest="event_command", required=True)
    apply = event_subparsers.add_parser("apply", help="apply one event atomically")
    apply.add_argument("--state", required=True, type=Path)
    apply.add_argument("--event", required=True)
    apply.add_argument("--task")
    apply.add_argument("--data", type=_json_argument, default={})
    apply.add_argument("--idempotency-key", required=True)
    apply.add_argument("--expected-revision", required=True, type=int)

    constitution = subparsers.add_parser("constitution", help="manage constitution ratification")
    constitution_subparsers = constitution.add_subparsers(
        dest="constitution_command", required=True
    )
    ratify = constitution_subparsers.add_parser("ratify", help="record explicit ratification")
    ratify.add_argument("--state", required=True, type=Path)
    ratify.add_argument("--by", required=True)
    fingerprint_source = ratify.add_mutually_exclusive_group(required=True)
    fingerprint_source.add_argument("--decision-fingerprint")
    fingerprint_source.add_argument("--proposal", type=Path)
    ratify.add_argument("--idempotency-key", required=True)
    ratify.add_argument("--expected-revision", required=True, type=int)
    probe = constitution_subparsers.add_parser("probe", help="gather repository evidence and propose decisions")
    probe.add_argument("--root", required=True, type=Path)
    probe.add_argument("--output", type=Path)
    constitution_status = constitution_subparsers.add_parser("status", help="check ratification and proposal drift")
    constitution_status.add_argument("--state", required=True, type=Path)
    constitution_status.add_argument("--proposal", type=Path)
    return parser


def _print_json(value: dict[str, Any]) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _brief(summary: dict[str, Any]) -> str:
    counts = ", ".join(f"{status}={count}" for status, count in summary["taskCounts"].items())
    return "\n".join(
        [
            f"{summary['scope']['id']} revision {summary['revision']}",
            f"constitution: {summary['constitution']}",
            f"tasks: {counts}",
            f"active: {len(summary['activeTasks'])}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            store = StateStore(args.state)
            if store.exists():
                parser.error(f"state file already exists: {args.state}")
            state = new_state(args.scope_id, args.scope_kind)
            for task_id in args.task:
                if task_id in state["tasks"]:
                    parser.error(f"duplicate --task: {task_id}")
                state["tasks"][task_id] = task_state(task_id)
            store.write(state)
            _print_json({"created": str(args.state), "revision": 0, "scope": state["scope"]})
            return 0

        if args.command == "status":
            summary = status_summary(StateStore(args.state).read())
            if args.json:
                _print_json(summary)
            else:
                print(_brief(summary))
            return 0

        if args.command == "next":
            _print_json(
                bounded_next_actions(StateStore(args.state).read(), max_bytes=args.max_bytes)
            )
            return 0

        if args.command == "render":
            state = StateStore(args.state).read()
            render_to_path(state, args.output)
            _print_json({"rendered": str(args.output), "revision": state["revision"]})
            return 0

        if args.command == "monitor":
            _print_json(monitor_once(StateStore(args.state), repo=args.repo, dry_run=args.dry_run))
            return 0

        if args.command == "lease" and args.lease_command == "ensure":
            result, duplicate = ensure_lease(
                StateStore(args.state),
                repo=args.repo,
                task_id=args.task,
                branch=args.branch,
                worktree=args.worktree,
                base_ref=args.base_ref,
                idempotency_key=args.idempotency_key,
                expected_revision=args.expected_revision,
                dry_run=args.dry_run,
            )
            _print_json({**result, "duplicate": duplicate})
            return 0

        if args.command == "lease" and args.lease_command == "release":
            result, duplicate = release_lease(
                StateStore(args.state),
                repo=args.repo,
                task_id=args.task,
                idempotency_key=args.idempotency_key,
                expected_revision=args.expected_revision,
                keep_worktree=args.keep_worktree,
            )
            _print_json({**result, "duplicate": duplicate})
            return 0

        if args.command == "freshness":
            result = check_freshness(
                args.repo,
                base_ref=args.base,
                head_ref=args.head,
                conflict_domains=args.conflict_domain,
            )
            if args.freshness_command == "check":
                _print_json(result)
                return 0
            state, duplicate = apply_event(
                StateStore(args.state),
                event_type="freshness.recorded",
                task_id=args.task,
                data=result,
                idempotency_key=args.idempotency_key,
                expected_revision=args.expected_revision,
            )
            _print_json({"freshness": result, "revision": state["revision"], "duplicate": duplicate})
            return 0

        if args.command == "review":
            from .review import collect_review, run_review

            if args.review_command == "run":
                _print_json(
                    run_review(
                        StateStore(args.state),
                        repo=args.repo,
                        task_id=args.task,
                        command=args.command_json,
                        output=args.output,
                        base_sha=args.base_sha,
                    )
                )
                return 0
            state, duplicate = collect_review(
                StateStore(args.state),
                task_id=args.task,
                result_paths=args.result,
                idempotency_key=args.idempotency_key,
                expected_revision=args.expected_revision,
            )
            _print_json({"revision": state["revision"], "duplicate": duplicate})
            return 0

        if args.command == "verify" and args.verify_command == "collect":
            from .review import collect_verification

            state, duplicate = collect_verification(
                StateStore(args.state),
                task_id=args.task,
                result_path=args.result,
                idempotency_key=args.idempotency_key,
                expected_revision=args.expected_revision,
            )
            _print_json({"revision": state["revision"], "duplicate": duplicate})
            return 0

        if args.command == "migrate":
            if args.rollback:
                if args.state or args.apply or args.dry_run:
                    parser.error("--rollback cannot be combined with --state, --apply, or --dry-run")
                _print_json(rollback_migration(args.rollback))
                return 0
            if not args.state:
                parser.error("--state is required unless --rollback is used")
            if args.apply == args.dry_run:
                parser.error("choose exactly one of --dry-run or --apply")
            plan = build_migration_plan(args.state)
            if args.dry_run:
                _print_json({
                    "id": plan["id"],
                    "scope": plan["scope"],
                    "moves": plan["moves"],
                    "backupDirectory": plan["backupDirectory"],
                    "targetSchemaVersion": 2,
                })
            else:
                _print_json(apply_migration(plan))
            return 0

        if args.command == "event" and args.event_command == "apply":
            state, duplicate = apply_event(
                StateStore(args.state),
                event_type=args.event,
                task_id=args.task,
                data=args.data,
                idempotency_key=args.idempotency_key,
                expected_revision=args.expected_revision,
            )
            _print_json({"revision": state["revision"], "duplicate": duplicate})
            return 0

        if args.command == "constitution" and args.constitution_command == "ratify":
            if args.proposal:
                fingerprint = read_proposal(args.proposal)["decisionFingerprint"]
            else:
                fingerprint = args.decision_fingerprint
            state, duplicate = apply_event(
                StateStore(args.state),
                event_type="constitution.ratified",
                task_id=None,
                data={"by": args.by, "decisionFingerprint": fingerprint},
                idempotency_key=args.idempotency_key,
                expected_revision=args.expected_revision,
            )
            _print_json({"revision": state["revision"], "duplicate": duplicate})
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
            _print_json(ratification_status(StateStore(args.state).read(), proposal))
            return 0
    except WaveDeliveryError as error:
        print(f"wdctl: {error}", file=sys.stderr)
        return error.exit_code
    parser.error("unknown command")
    return 2
