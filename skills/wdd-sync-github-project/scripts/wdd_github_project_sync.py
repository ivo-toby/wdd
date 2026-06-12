#!/usr/bin/env python3
"""Plan and apply conservative WDD <-> GitHub Projects sync operations.

The script is intentionally dry-run first. It can write local WDD artifacts when
pulling from a GitHub Project snapshot. Remote mutations are represented as
explicit operation plans, with optional issue creation helpers for guarded use.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


KANBAN_STATUSES = {
    "todo",
    "in-progress",
    "review",
    "done",
    "blocked",
    "cancelled",
}

REMOTE_TO_LOCAL_STATUS = {
    "": "todo",
    "backlog": "todo",
    "todo": "todo",
    "to do": "todo",
    "ready": "todo",
    "in progress": "in-progress",
    "doing": "in-progress",
    "review": "review",
    "in review": "review",
    "blocked": "blocked",
    "done": "done",
    "closed": "done",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}

LOCAL_TO_REMOTE_STATUS = {
    "todo": "Todo",
    "planned": "Todo",
    "in-progress": "In Progress",
    "review": "Review",
    "done": "Done",
    "blocked": "Blocked",
    "cancelled": "Cancelled",
}


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug) or "item"


def today() -> str:
    return dt.date.today().isoformat()


def fingerprint_text(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def fingerprint_remote_item(item: dict[str, Any]) -> str:
    normalized = {
        "body": item.get("body") or "",
        "issue_number": item.get("issue_number") or item.get("number"),
        "kind": item.get("kind") or "",
        "state": item.get("state") or "",
        "status": item.get("status") or "",
        "ticket": item.get("ticket") or "",
        "title": item.get("title") or "",
        "url": item.get("url") or "",
        "wave": item.get("wave") or "",
        "wdd_id": item.get("wdd_id") or "",
    }
    return fingerprint_text(json.dumps(normalized, sort_keys=True))


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if value in {"", "null", "None", "~"}:
        return None
    if value == "[]":
        return []
    if value == "{}":
        return {}
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if (
        (value.startswith('"') and value.endswith('"'))
        or (value.startswith("'") and value.endswith("'"))
    ):
        return value[1:-1]
    return value


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    raw = text[4:end].strip("\n")
    body = text[text.find("\n", end + 4) + 1 :]
    data: dict[str, Any] = {}
    current_key: str | None = None
    current_list: str | None = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") and current_list:
            data.setdefault(current_list, []).append(parse_scalar(line[4:]))
            continue
        if line.startswith("  ") and current_key:
            child_line = line.strip()
            if ":" in child_line:
                child_key, child_value = child_line.split(":", 1)
                current = data.setdefault(current_key, {})
                if isinstance(current, dict):
                    current[child_key.strip()] = parse_scalar(child_value)
            continue
        current_key = None
        current_list = None
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value == "":
            data[key] = {}
            current_key = key
        else:
            parsed = parse_scalar(value)
            data[key] = parsed
            if parsed == []:
                current_list = key
    return data, body


def read_artifact(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(text)
    return {
        "frontmatter": frontmatter,
        "body": body,
        "fingerprint": fingerprint_text(text),
        "path": path,
        "text": text,
    }


def yaml_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    string = str(value)
    if string == "" or any(ch in string for ch in [":", "#", "[", "]", "{", "}"]):
        return json.dumps(string)
    return string


def frontmatter_block(data: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{key}:")
            for child_key, child_value in value.items():
                lines.append(f"  {child_key}: {yaml_value(child_value)}")
        elif isinstance(value, list):
            lines.append(f"{key}: []" if not value else f"{key}:")
            for item in value:
                lines.append(f"  - {yaml_value(item)}")
        else:
            lines.append(f"{key}: {yaml_value(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def heading_body(text: str, fallback: str = "") -> str:
    _, body = parse_frontmatter(text)
    return body.strip() or fallback


def extract_summary(body: str) -> str:
    stripped = body.strip()
    if not stripped:
        return ""
    lines = []
    for line in stripped.splitlines():
        if line.startswith("#") and lines:
            break
        if line.startswith("#"):
            continue
        if line.strip():
            lines.append(line.strip())
        if len(" ".join(lines)) > 280:
            break
    return " ".join(lines).strip()


def remote_status_to_local(status: str | None, *, kind: str, trust_done: bool) -> str:
    local = REMOTE_TO_LOCAL_STATUS.get((status or "").strip().lower(), "todo")
    if kind == "task" and local == "done" and not trust_done:
        return "review"
    return local


def local_status_to_remote(status: str | None) -> str:
    return LOCAL_TO_REMOTE_STATUS.get((status or "").strip().lower(), "Todo")


def normalize_project(snapshot: dict[str, Any], epic_id: str | None) -> dict[str, Any]:
    project = dict(snapshot.get("project") or {})
    title = project.get("title") or project.get("name") or "GitHub Project"
    project["title"] = title
    project["wdd_id"] = epic_id or project.get("wdd_id") or f"EPIC-{slugify(title)}"
    return project


def field_value(raw: dict[str, Any], *names: str) -> Any:
    fields = raw.get("fields")
    if isinstance(fields, dict):
        for name in names:
            if name in fields:
                return fields[name]
    field_values = raw.get("fieldValues") or raw.get("field_values")
    if isinstance(field_values, dict):
        for name in names:
            if name in field_values:
                return field_values[name]
    return None


def issue_number(raw: dict[str, Any]) -> int | None:
    value = raw.get("issue_number") or raw.get("number")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    content = raw.get("content")
    if isinstance(content, dict):
        content_number = content.get("number")
        if isinstance(content_number, int):
            return content_number
    return None


def normalize_remote_items(
    snapshot: dict[str, Any], *, trust_done: bool
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    ticket_counter = 1
    task_counter = 1
    raw_items = snapshot.get("items") or []
    if isinstance(raw_items, dict):
        raw_items = raw_items.get("nodes") or raw_items.get("items") or []
    for raw in raw_items:
        content = raw.get("content") if isinstance(raw.get("content"), dict) else {}
        raw_title = raw.get("title") or content.get("title") or "Untitled"
        wdd_id = (
            raw.get("wdd_id")
            or field_value(raw, "WDD ID", "wdd_id", "WDDID")
            or ""
        )
        kind = (
            raw.get("kind")
            or raw.get("wdd_kind")
            or field_value(raw, "WDD Kind", "Kind", "wdd_kind")
            or ""
        )
        ticket = raw.get("ticket") or field_value(raw, "Ticket", "WDD Ticket") or ""
        if not kind and str(wdd_id).startswith("TICKET-"):
            kind = "ticket"
        if not kind and str(wdd_id).startswith("TASK-"):
            kind = "task"
        if not kind and ticket:
            kind = "task"
        if not kind and raw_title.lower().startswith("task:"):
            kind = "task"
        if not kind:
            kind = "ticket"
        kind = str(kind).strip().lower()
        if kind not in {"epic", "ticket", "task"}:
            kind = "ticket"
        if kind == "epic":
            continue
        if not wdd_id:
            if kind == "ticket":
                wdd_id = f"TICKET-{ticket_counter:03d}-{slugify(raw_title)}"
                ticket_counter += 1
            else:
                wdd_id = f"TASK-{task_counter:03d}-{slugify(raw_title)}"
                task_counter += 1
        status = (
            raw.get("status")
            or field_value(raw, "Status", "status")
            or content.get("state")
            or ""
        )
        item = {
            "kind": kind,
            "wdd_id": str(wdd_id),
            "ticket": str(ticket or raw.get("parent") or ""),
            "wave": str(raw.get("wave") or field_value(raw, "Wave", "WDD Wave") or ""),
            "item_id": raw.get("item_id") or raw.get("id"),
            "issue_number": issue_number(raw),
            "url": raw.get("url") or content.get("url"),
            "title": raw_title.replace("Ticket:", "").replace("Task:", "").strip(),
            "body": raw.get("body") or content.get("body") or "",
            "status": str(status),
            "local_status": remote_status_to_local(
                str(status), kind=kind, trust_done=trust_done
            ),
            "state": raw.get("state") or content.get("state") or "",
            "updated_at": raw.get("updated_at")
            or raw.get("updatedAt")
            or content.get("updatedAt"),
        }
        normalized.append(item)
    return normalized


def epic_dir_for(root: Path, epic_id: str) -> Path:
    return root / ".wdd" / "epics" / epic_id


def manifest_path(epic_dir: Path) -> Path:
    return epic_dir / "adapters" / "github-project.json"


def load_manifest(epic_dir: Path | None) -> dict[str, Any]:
    if not epic_dir:
        return {"schemaVersion": 1, "items": {}}
    path = manifest_path(epic_dir)
    if not path.exists():
        return {"schemaVersion": 1, "items": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def load_local_state(root: Path, epic_id: str | None) -> dict[str, Any]:
    epics_root = root / ".wdd" / "epics"
    candidates: list[Path] = []
    if epic_id:
        candidates = [epic_dir_for(root, epic_id)]
    elif epics_root.exists():
        candidates = sorted(path for path in epics_root.iterdir() if path.is_dir())
    for candidate in candidates:
        epic_file = candidate / "epic.md"
        if not epic_file.exists():
            continue
        epic_artifact = read_artifact(epic_file)
        found_id = epic_artifact["frontmatter"].get("id") or candidate.name
        if epic_id and found_id != epic_id and candidate.name != epic_id:
            continue
        items: dict[str, dict[str, Any]] = {
            found_id: {
                "kind": "epic",
                "id": found_id,
                "title": epic_artifact["frontmatter"].get("title") or found_id,
                "status": epic_artifact["frontmatter"].get("status") or "draft",
                "path": epic_file,
                "relpath": "epic.md",
                "fingerprint": epic_artifact["fingerprint"],
                "frontmatter": epic_artifact["frontmatter"],
                "body": epic_artifact["body"],
            }
        }
        for ticket_file in sorted(candidate.glob("TICKET-*/ticket.md")):
            artifact = read_artifact(ticket_file)
            item_id = artifact["frontmatter"].get("id") or ticket_file.parent.name
            items[item_id] = {
                "kind": "ticket",
                "id": item_id,
                "title": artifact["frontmatter"].get("title") or item_id,
                "status": artifact["frontmatter"].get("status") or "planned",
                "path": ticket_file,
                "relpath": str(ticket_file.relative_to(candidate)),
                "fingerprint": artifact["fingerprint"],
                "frontmatter": artifact["frontmatter"],
                "body": artifact["body"],
            }
        for task_file in sorted(candidate.glob("TICKET-*/*/TASK-*.md")):
            if task_file.name == "ticket.md":
                continue
            artifact = read_artifact(task_file)
            status_dir = task_file.parent.name
            item_id = artifact["frontmatter"].get("id") or task_file.stem
            items[item_id] = {
                "kind": "task",
                "id": item_id,
                "title": artifact["frontmatter"].get("title") or item_id,
                "status": artifact["frontmatter"].get("status")
                or (status_dir if status_dir in KANBAN_STATUSES else "todo"),
                "ticket": artifact["frontmatter"].get("ticket") or task_file.parent.parent.name,
                "wave": artifact["frontmatter"].get("wave") or "",
                "path": task_file,
                "relpath": str(task_file.relative_to(candidate)),
                "fingerprint": artifact["fingerprint"],
                "frontmatter": artifact["frontmatter"],
                "body": artifact["body"],
            }
        return {"epic_dir": candidate, "epic_id": found_id, "items": items}
    return {"epic_dir": None, "epic_id": epic_id, "items": {}}


def ticket_for_task(task: dict[str, Any], tickets: list[dict[str, Any]]) -> str:
    ticket = task.get("ticket") or ""
    if ticket:
        return ticket
    if len(tickets) == 1:
        return tickets[0]["wdd_id"]
    return f"TICKET-001-{slugify(task.get('title') or 'imported')}"


def local_relpath_for_remote(item: dict[str, Any], tickets: list[dict[str, Any]]) -> str:
    if item["kind"] == "ticket":
        return f"{item['wdd_id']}/ticket.md"
    ticket = ticket_for_task(item, tickets)
    status = item.get("local_status") or "todo"
    if status not in KANBAN_STATUSES:
        status = "todo"
    return f"{ticket}/{status}/{item['wdd_id']}.md"


def local_item_body(local_item: dict[str, Any]) -> str:
    title = local_item.get("title") or local_item["id"]
    summary = extract_summary(local_item.get("body") or "")
    metadata = f"WDD ID: {local_item['id']}\nWDD Kind: {local_item['kind']}\n"
    if local_item["kind"] == "task":
        metadata += f"Ticket: {local_item.get('ticket') or ''}\n"
        metadata += f"Wave: {local_item.get('wave') or ''}\n"
    return f"{summary or title}\n\n---\n\n{metadata}".strip()


def create_remote_ops(
    local_item: dict[str, Any], project: dict[str, Any]
) -> list[dict[str, Any]]:
    repo = project.get("repo") or ""
    labels = ["wdd", f"wdd:{local_item['kind']}"]
    issue_op = {
        "action": "create_remote_issue",
        "wdd_id": local_item["id"],
        "kind": local_item["kind"],
        "repo": repo,
        "title": local_item.get("title") or local_item["id"],
        "body": local_item_body(local_item),
        "labels": labels,
    }
    add_op = {
        "action": "add_issue_to_project",
        "wdd_id": local_item["id"],
        "project_owner": project.get("owner"),
        "project_number": project.get("number"),
        "requires_issue_url_from": local_item["id"],
    }
    field_op = {
        "action": "update_project_fields",
        "wdd_id": local_item["id"],
        "fields": {
            "WDD ID": local_item["id"],
            "WDD Kind": local_item["kind"],
            "Status": local_status_to_remote(local_item.get("status")),
        },
    }
    if local_item["kind"] == "task":
        field_op["fields"]["Ticket"] = local_item.get("ticket") or ""
        field_op["fields"]["Wave"] = local_item.get("wave") or ""
    return [issue_op, add_op, field_op]


def plan_sync(
    root: Path | str,
    remote_snapshot: dict[str, Any],
    *,
    mode: str = "sync",
    epic_id: str | None = None,
    trust_remote_done: bool = False,
) -> dict[str, Any]:
    root = Path(root)
    project = normalize_project(remote_snapshot, epic_id)
    effective_epic_id = project["wdd_id"]
    remote_items = normalize_remote_items(
        remote_snapshot, trust_done=trust_remote_done
    )
    remote_by_id = {item["wdd_id"]: item for item in remote_items}
    local = load_local_state(root, effective_epic_id)
    epic_dir = local["epic_dir"] or epic_dir_for(root, effective_epic_id)
    manifest = load_manifest(epic_dir)
    manifest_items = manifest.get("items") or {}
    local_items = local["items"]
    local_non_epic = {
        item_id: item
        for item_id, item in local_items.items()
        if item.get("kind") in {"ticket", "task"}
    }
    ticket_items = [item for item in remote_items if item["kind"] == "ticket"]
    operations: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    if mode in {"pull", "sync"}:
        if not local["epic_dir"]:
            operations.append(
                {
                    "action": "create_local_epic",
                    "epic_id": effective_epic_id,
                    "title": project["title"],
                    "project": project,
                }
            )
        for item in remote_items:
            local_item = local_non_epic.get(item["wdd_id"])
            manifest_item = manifest_items.get(item["wdd_id"]) or {}
            remote_fp = fingerprint_remote_item(item)
            if local_item and manifest_item:
                local_changed = (
                    local_item["fingerprint"]
                    != (manifest_item.get("fingerprints") or {}).get("local")
                )
                remote_changed = (
                    remote_fp
                    != (manifest_item.get("fingerprints") or {}).get("remote")
                )
                if mode == "sync" and local_changed and remote_changed:
                    conflicts.append(
                        {
                            "wdd_id": item["wdd_id"],
                            "kind": item["kind"],
                            "reason": "local and GitHub item changed since last sync",
                            "local_path": local_item["relpath"],
                            "github_url": item.get("url"),
                        }
                    )
                    continue
                if remote_changed and not local_changed:
                    desired_path = (
                        local_relpath_for_remote(item, ticket_items)
                        if item["kind"] == "task"
                        else local_item["relpath"]
                    )
                    operations.append(
                        {
                            "action": f"update_local_{item['kind']}",
                            "wdd_id": item["wdd_id"],
                            "item": item,
                            "path": desired_path,
                            "old_path": local_item["relpath"],
                        }
                    )
            elif not local_item:
                operations.append(
                    {
                        "action": f"create_local_{item['kind']}",
                        "wdd_id": item["wdd_id"],
                        "item": item,
                        "path": local_relpath_for_remote(item, ticket_items),
                    }
                )

    if mode in {"push", "sync"} and not conflicts:
        for item_id, local_item in local_non_epic.items():
            manifest_item = manifest_items.get(item_id) or {}
            remote_item = remote_by_id.get(item_id)
            if not remote_item and not (manifest_item.get("github") or {}).get("url"):
                operations.extend(create_remote_ops(local_item, project))
                continue
            if remote_item and manifest_item:
                local_changed = (
                    local_item["fingerprint"]
                    != (manifest_item.get("fingerprints") or {}).get("local")
                )
                remote_changed = (
                    fingerprint_remote_item(remote_item)
                    != (manifest_item.get("fingerprints") or {}).get("remote")
                )
                if mode == "sync" and local_changed and remote_changed:
                    conflicts.append(
                        {
                            "wdd_id": item_id,
                            "kind": local_item["kind"],
                            "reason": "local and GitHub item changed since last sync",
                            "local_path": local_item["relpath"],
                            "github_url": remote_item.get("url"),
                        }
                    )
                    continue
                if local_changed and not remote_changed:
                    operations.append(
                        {
                            "action": "update_remote_issue",
                            "wdd_id": item_id,
                            "kind": local_item["kind"],
                            "repo": project.get("repo"),
                            "issue_number": remote_item.get("issue_number"),
                            "title": local_item.get("title") or item_id,
                            "body": local_item_body(local_item),
                        }
                    )
                    operations.append(
                        {
                            "action": "update_project_fields",
                            "wdd_id": item_id,
                            "fields": {
                                "WDD ID": item_id,
                                "WDD Kind": local_item["kind"],
                                "Status": local_status_to_remote(
                                    local_item.get("status")
                                ),
                            },
                        }
                    )

    local_write_actions = {
        "create_local_epic",
        "create_local_ticket",
        "create_local_task",
        "update_local_ticket",
        "update_local_task",
    }
    if any(op["action"] in local_write_actions for op in operations) and not conflicts:
        operations.append({"action": "write_manifest"})

    return {
        "schemaVersion": 1,
        "mode": mode,
        "epic_id": effective_epic_id,
        "epic_dir": str(epic_dir),
        "project": project,
        "operations": operations if not conflicts else [],
        "conflicts": conflicts,
        "remote_items": remote_items,
    }


def remote_link(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "itemId": item.get("item_id"),
        "issueNumber": item.get("issue_number"),
        "url": item.get("url"),
    }


def render_epic(operation: dict[str, Any], ticket_count: int, task_count: int) -> str:
    project = operation["project"]
    epic_id = operation["epic_id"]
    title = operation["title"]
    slug = slugify(epic_id.replace("EPIC-", "") or title)
    frontmatter = {
        "id": epic_id,
        "kind": "epic",
        "type": "feature",
        "slug": slug,
        "title": title,
        "status": "draft",
        "created_at": today(),
        "updated_at": today(),
        "target_branch": "main",
        "epic_branch": f"epic/{slug}",
        "schema_version": 1,
        "ticket_count": ticket_count,
        "task_count": task_count,
        "adapter_links": {
            "github_issue": None,
            "github_project": project.get("url"),
        },
    }
    return frontmatter_block(frontmatter) + f"""# {title}

## Summary

Imported from GitHub Project {project.get('owner')}/{project.get('number')}.

## Goal

Refine this imported epic before planning or starting waves.

## Background

Source project: {project.get('url') or 'unknown'}

## Product Context

Imported from GitHub Project metadata.

## Technical Context

Inspect repository code, tests, and linked issues before implementation.

## Deliverables

- Imported tickets and tasks are represented as local WDD artifacts.

## Non-Goals

- None recorded during import.

## Assumptions

- GitHub Project items are the remote planning mirror for this WDD epic.

## Constraints

- Local WDD artifacts remain the execution source of truth.

## Risks

- Imported project items may need decomposition before worker dispatch.

## Dependencies

- GitHub Project access through `gh`.

## Affected Areas

- To be refined during planning.

## Validation Strategy

Define project-native checks before starting the first wave.

## Definition of Done

- [ ] Imported items are reconciled.
- [ ] Task reviews have no unresolved P1/P2 findings.
- [ ] Epic validation passes.
- [ ] Final PR is ready for human review.

## Open Questions

- Confirm imported ticket/task boundaries before execution.

## Planning Notes

Run `wdd-plan-epic` after import if tasks need dependencies, waves, or shared context.
"""


def render_ticket(item: dict[str, Any], task_count: int) -> str:
    local_status = item.get("local_status") or "planned"
    status = "planned" if local_status == "todo" else local_status
    slug = slugify(item["wdd_id"].replace("TICKET-", ""))
    frontmatter = {
        "id": item["wdd_id"],
        "kind": "ticket",
        "epic": item.get("epic") or "",
        "slug": slug,
        "title": item.get("title") or item["wdd_id"],
        "status": status,
        "task_count": task_count,
        "depends_on": [],
        "conflict_domains": [],
        "adapter_links": {"github_issue": item.get("url")},
    }
    body = item.get("body") or "Imported from GitHub."
    return frontmatter_block(frontmatter) + f"""# {item.get('title') or item['wdd_id']}

## Summary

{body.strip()}

## Objective

Refine this imported ticket before execution.

## Scope

- Included: imported GitHub issue scope.
- Excluded: work not represented by linked tasks.

## Non-Scope

- Unspecified until local planning refines this ticket.

## Shared Context References

- `../shared-context/index.md`

## Task Inventory

| Task | Status | Wave | Summary |
|------|--------|------|---------|

## Dependencies

- Depends on: none recorded during import.
- Blocks: none recorded during import.

## Conflict Domains

- To be refined during planning.

## Validation Expectations

- Ticket is complete when all child tasks are done or explicitly cancelled.

## Review Focus

- Imported scope clarity.

## Completion Criteria

- [ ] All child tasks have resolved review and verification gates.
- [ ] Shared context updates were reconciled.
- [ ] Ticket status matches child task state.
"""


def render_task(item: dict[str, Any], ticket: str) -> str:
    status = item.get("local_status") or "todo"
    slug = slugify(item["wdd_id"].replace("TASK-", ""))
    wave = item.get("wave") or "WAVE-001"
    frontmatter = {
        "id": item["wdd_id"],
        "kind": "task",
        "epic": item.get("epic") or "",
        "ticket": ticket,
        "wave": wave,
        "slug": slug,
        "title": item.get("title") or item["wdd_id"],
        "status": status,
        "depends_on": [],
        "conflict_domains": [],
        "assigned_model_class": "simple-implementation",
        "review_model_class": "review",
        "branch": f"task/{item['wdd_id']}",
        "worker_worktree": None,
        "worktree_status": "unassigned",
        "pr": None,
        "current_gate": "not_started",
        "branch_freshness": "unknown",
        "verification": ["project-specific verification command"],
        "adapter_links": {"github_issue": item.get("url")},
    }
    body = item.get("body") or "Imported from GitHub."
    return frontmatter_block(frontmatter) + f"""# {item['wdd_id']}: {item.get('title') or item['wdd_id']}

## Status

{status}

## Parent Ticket

{ticket}

## Wave

{wave}

## Objective

{body.strip()}

## Scope

- Included: imported GitHub issue scope.
- Excluded: work outside the linked issue.

## Non-Scope

- Unspecified until local planning refines this task.

## Relevant Context

### Local Context

Inspect files named by the linked GitHub issue and local shared context.

### Shared Context References

- `../../shared-context/index.md`

## Likely Files / Areas

- To be refined during planning.

## Dependencies

- None recorded during import.

## Conflict Domains

- To be refined during planning.

## Assigned Model Class

simple-implementation

## Branch

task/{item['wdd_id']}

## Worker Worktree

None assigned yet.

## PR / Patch Reference

None yet.

## RED-GREEN TDD Plan

### RED

Define the first failing check before implementation.

### GREEN

Implement the smallest change that satisfies the check.

### REFACTOR

Clean up after green.

## Implementation Notes

- Confirm imported scope before editing code.
- Stay within this task.

## Durable Memory Notes To Consider

- Record discoveries that affect later tasks in shared context.

## Task-Level Definition of Done

- [ ] Objective is complete.
- [ ] Verification evidence is recorded.
- [ ] No unresolved P1/P2 review findings remain.
- [ ] Shared-context updates, if any, are proposed for controller reconciliation.

## Validation Steps

- `project-specific verification command`

## Verification Evidence

- Not run yet.

## Review Feedback

### P1

- None.

### P2

- None.

### P3

- None.

## Completion Notes

- Imported from GitHub issue {item.get('url') or 'unknown'}.
"""


def write_if_missing_or_update(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_manifest(root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    epic_dir = Path(plan["epic_dir"])
    local = load_local_state(root, plan["epic_id"])
    remote_items = {item["wdd_id"]: item for item in plan["remote_items"]}
    manifest_items: dict[str, Any] = {}
    for item_id, remote_item in remote_items.items():
        local_item = local["items"].get(item_id)
        if not local_item:
            continue
        manifest_items[item_id] = {
            "kind": remote_item["kind"],
            "localPath": local_item["relpath"],
            "github": remote_link(remote_item),
            "fingerprints": {
                "local": local_item["fingerprint"],
                "remote": fingerprint_remote_item(remote_item),
            },
        }
    return {
        "schemaVersion": 1,
        "updatedAt": dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "epic": {"id": plan["epic_id"]},
        "project": plan["project"],
        "items": manifest_items,
    }


def apply_local_operations(root: Path | str, plan: dict[str, Any]) -> None:
    root = Path(root)
    if plan.get("conflicts"):
        raise RuntimeError("Refusing to apply local operations while conflicts exist")
    epic_dir = Path(plan["epic_dir"])
    remote_items = {item["wdd_id"]: item for item in plan["remote_items"]}
    tickets = [item for item in plan["remote_items"] if item["kind"] == "ticket"]
    tasks_by_ticket: dict[str, int] = {}
    for item in plan["remote_items"]:
        if item["kind"] == "task":
            ticket = ticket_for_task(item, tickets)
            tasks_by_ticket[ticket] = tasks_by_ticket.get(ticket, 0) + 1
    for operation in plan["operations"]:
        action = operation["action"]
        if action == "create_local_epic":
            ticket_count = sum(1 for item in plan["remote_items"] if item["kind"] == "ticket")
            task_count = sum(1 for item in plan["remote_items"] if item["kind"] == "task")
            write_if_missing_or_update(
                epic_dir / "epic.md",
                render_epic(operation, ticket_count, task_count),
            )
        elif action in {"create_local_ticket", "update_local_ticket"}:
            item = dict(remote_items[operation["wdd_id"]])
            item["epic"] = plan["epic_id"]
            write_if_missing_or_update(
                epic_dir / operation["path"],
                render_ticket(item, tasks_by_ticket.get(item["wdd_id"], 0)),
            )
        elif action in {"create_local_task", "update_local_task"}:
            item = dict(remote_items[operation["wdd_id"]])
            item["epic"] = plan["epic_id"]
            ticket = ticket_for_task(item, tickets)
            old_path = operation.get("old_path")
            new_path = operation["path"]
            write_if_missing_or_update(
                epic_dir / new_path,
                render_task(item, ticket),
            )
            if old_path and old_path != new_path:
                stale_path = epic_dir / old_path
                if stale_path.exists():
                    stale_path.unlink()
        elif action == "write_manifest":
            manifest = build_manifest(root, plan)
            path = manifest_path(epic_dir)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def load_remote_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_json(command: list[str], *, input_text: str | None = None) -> dict[str, Any]:
    result = subprocess.run(
        command,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(command)}\n{result.stderr}"
        )
    return json.loads(result.stdout or "{}")


def fetch_project_snapshot(owner: str, number: int, repo: str | None) -> dict[str, Any]:
    if not shutil.which("gh"):
        raise RuntimeError("gh is required when --remote-json is not provided")
    project = run_json(
        [
            "gh",
            "project",
            "view",
            str(number),
            "--owner",
            owner,
            "--format",
            "json",
        ]
    )
    items_result = run_json(
        [
            "gh",
            "project",
            "item-list",
            str(number),
            "--owner",
            owner,
            "--format",
            "json",
            "--limit",
            "1000",
        ]
    )
    return {
        "project": {
            "owner": owner,
            "number": number,
            "id": project.get("id"),
            "title": project.get("title"),
            "url": project.get("url"),
            "repo": repo,
            "wdd_id": project.get("shortDescription") or None,
        },
        "items": items_result.get("items") or [],
    }


def print_text_plan(plan: dict[str, Any]) -> None:
    print(f"Epic: {plan['epic_id']}")
    print(f"Mode: {plan['mode']}")
    if plan["conflicts"]:
        print("\nConflicts:")
        for conflict in plan["conflicts"]:
            print(
                f"- {conflict['wdd_id']}: {conflict['reason']} "
                f"({conflict.get('local_path')}, {conflict.get('github_url')})"
            )
        return
    if not plan["operations"]:
        print("\nNo operations.")
        return
    print("\nOperations:")
    for operation in plan["operations"]:
        label = operation.get("wdd_id") or operation.get("epic_id") or ""
        print(f"- {operation['action']} {label}".rstrip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["inspect", "pull", "push", "sync"])
    parser.add_argument("--root", default=".", help="Repository root")
    parser.add_argument("--epic-id", help="Local WDD epic ID")
    parser.add_argument("--remote-json", type=Path, help="GitHub Project snapshot JSON")
    parser.add_argument("--project-owner", help="GitHub Project owner")
    parser.add_argument("--project-number", type=int, help="GitHub Project number")
    parser.add_argument("--repo", help="GitHub repository owner/name for issue writes")
    parser.add_argument("--trust-remote-done", action="store_true")
    parser.add_argument("--apply-local", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    args = parser.parse_args(argv)

    root = Path(args.root)
    if args.remote_json:
        snapshot = load_remote_json(args.remote_json)
    else:
        if not args.project_owner or not args.project_number:
            parser.error("--project-owner and --project-number are required without --remote-json")
        snapshot = fetch_project_snapshot(args.project_owner, args.project_number, args.repo)
    mode = "sync" if args.mode == "inspect" else args.mode
    plan = plan_sync(
        root,
        snapshot,
        mode=mode,
        epic_id=args.epic_id,
        trust_remote_done=args.trust_remote_done,
    )
    if args.apply_local:
        if args.mode not in {"pull", "sync"}:
            parser.error("--apply-local is only valid for pull or sync")
        apply_local_operations(root, plan)
        plan = plan_sync(
            root,
            snapshot,
            mode=mode,
            epic_id=args.epic_id,
            trust_remote_done=args.trust_remote_done,
        )
    if args.json:
        print(json.dumps(plan, indent=2, sort_keys=True))
    else:
        print_text_plan(plan)
    return 1 if plan["conflicts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
