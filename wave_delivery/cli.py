"""Command-line interface for the initial deterministic WDD controller."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .engine import apply_event, bounded_next_actions, render_to_path, status_summary
from .errors import WaveDeliveryError
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
    ratify.add_argument("--decision-fingerprint", required=True)
    ratify.add_argument("--idempotency-key", required=True)
    ratify.add_argument("--expected-revision", required=True, type=int)
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
            state, duplicate = apply_event(
                StateStore(args.state),
                event_type="constitution.ratified",
                task_id=None,
                data={"by": args.by, "decisionFingerprint": args.decision_fingerprint},
                idempotency_key=args.idempotency_key,
                expected_revision=args.expected_revision,
            )
            _print_json({"revision": state["revision"], "duplicate": duplicate})
            return 0
    except WaveDeliveryError as error:
        print(f"wdctl: {error}", file=sys.stderr)
        return error.exit_code
    parser.error("unknown command")
    return 2
