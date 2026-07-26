#!/usr/bin/env python3
"""Plan and apply conservative WDD <-> GitHub Projects sync operations.

WDD's planning input is `.wdd/plan.json` (a single flat scope, no epics or
tickets) plus one worker brief per task at `.wdd/tasks/<TASK-ID>.md`. Task
execution state lives in `.wdd/state.json`, which is owned exclusively by
`wddctl` -- this script only ever reads it.

The script is dry-run first. It can write local WDD artifacts when pulling
from a GitHub Project snapshot (only with `--apply-local`). Remote mutations
are always represented as an explicit, human-reviewable operation plan; this
script never calls out to GitHub to mutate anything itself.
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


# ---------------------------------------------------------------------------
# Status mapping (wddctl task statuses <-> GitHub Project "Status" field text)
# ---------------------------------------------------------------------------

LOCAL_TO_REMOTE_STATUS = {
    "todo": "Todo",
    "in_progress": "In Progress",
    "review": "Review",
    "merge_ready": "Merge Ready",
    "done": "Done",
    "blocked": "Blocked",
    "cancelled": "Cancelled",
}


def local_status_to_remote(status: str | None) -> str:
    return LOCAL_TO_REMOTE_STATUS.get((status or "todo").strip().lower(), "Todo")


# ---------------------------------------------------------------------------
# WDD task-id validation -- a "WDD ID" field is attacker-controlled data from
# a remote GitHub Project board, and it feeds directly into filesystem paths
# (task specPath, brief files). It must be a single path segment: no `/`,
# no `..`, no other separators. See assert_path_contained() below for the
# second, independent line of defense (containment-check the resolved path).
# ---------------------------------------------------------------------------

TASK_ID_PATTERN = re.compile(r"^TASK-[A-Za-z0-9][A-Za-z0-9._-]*$")


def assert_no_symlink_between(root: Path, target: Path, what: str) -> None:
    """Walk every path component from `root` (exclusive) down to `target`
    (inclusive), refusing the write if any of them is a symlink.

    `root` must come from a source the caller trusts (the `--root` CLI
    argument) -- everything below it, including `.wdd` and `.wdd/tasks`
    themselves, is treated as untrusted until proven otherwise. A symlinked
    intermediate directory is exactly how a plain "resolve both sides and
    compare" containment check gets defeated: `.resolve()` silently follows
    the symlink, so both `target` and a symlinked container end up agreeing
    on some directory outside the repository. Walking the unresolved
    components and checking each with `Path.is_symlink()` catches that
    before any resolution happens.
    """
    root_resolved = root.resolve()
    try:
        relative_parts = target.relative_to(root).parts
    except ValueError:
        relative_parts = target.absolute().relative_to(root.absolute()).parts
    current = root_resolved
    for part in relative_parts:
        current = current / part
        if current.is_symlink():
            raise RuntimeError(
                f"Refusing to write {what}: {current} is a symlink. A symlinked "
                f".wdd, .wdd/tasks, or other intermediate directory cannot be used "
                f"to redirect a write outside {root_resolved}."
            )


def assert_path_contained(path: Path, container: Path, what: str, *, root: Path) -> Path:
    """Refuse to resolve `path` outside `container`.

    This is intentionally independent of TASK_ID_PATTERN validation upstream:
    every path this adapter writes is ultimately keyed by a "WDD ID" that can
    originate from remote, attacker-controlled data, so the write path itself
    is re-checked here even if ID-format validation was bypassed or buggy.

    Containment is anchored to `root` -- the repository root, which the
    caller must obtain from a trusted source (the `--root` argument) -- not
    to `container` itself. See `assert_no_symlink_between()`: if `container`
    (e.g. `.wdd` or `.wdd/tasks`) is itself a symlink pointing outside the
    repository, resolving `container` and `path` independently and comparing
    the results is not a safe check, because both then resolve under that
    same outside target and agree with each other. So this checks two
    independent things, both anchored to `root`: (1) no path component
    between `root` and either `container` or `path` is a symlink, and (2)
    the fully resolved `path` still lands under the fully resolved `root`.
    """
    root_resolved = root.resolve()
    assert_no_symlink_between(root, container, what)
    assert_no_symlink_between(root, path, what)

    resolved = path.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise RuntimeError(
            f"Refusing to write {what}: resolved path {resolved} is outside the "
            f"repository root {root_resolved}"
        )
    return resolved


# ---------------------------------------------------------------------------
# Risk heuristic -- deliberately small and documented (see CONTRACT: "risk").
# ---------------------------------------------------------------------------

RISK_KEYWORDS = (
    "auth",
    "security",
    "migrat",  # migration / migrations / migrating
    "persist",  # persistence / persistent / persisted
    "public api",
    "breaking change",
)


def infer_risk(title: str, labels: list[str], risk_hint: str) -> str:
    """"high" iff the item's title, labels, or an explicit Risk/WDD Risk field
    mentions auth, security, migrations, data persistence, a public API, or a
    breaking change. Everything else is "normal". This intentionally does not
    scan the full issue body -- only signals a human curating the Project
    board is likely to have set on purpose (title, labels, a risk field).
    """
    haystack = " ".join([title, " ".join(labels), risk_hint]).lower().replace("-", " ")
    return "high" if any(keyword.replace("-", " ") in haystack for keyword in RISK_KEYWORDS) else "normal"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug) or "item"


def fingerprint_text(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def split_field_list(value: Any) -> list[str]:
    """Split a comma- or newline-separated field into a clean string list."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    parts = re.split(r"[,\n]+", str(value))
    return [part.strip() for part in parts if part.strip()]


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


def field_value(raw: dict[str, Any], *names: str) -> Any:
    """Look up a field by name: a direct/compact key first, then GitHub
    Projects' "fields"/"fieldValues" containers."""
    for name in names:
        if name in raw and raw[name] not in (None, ""):
            return raw[name]
    fields = raw.get("fields")
    if isinstance(fields, dict):
        for name in names:
            if name in fields and fields[name] not in (None, ""):
                return fields[name]
    field_values = raw.get("fieldValues") or raw.get("field_values")
    if isinstance(field_values, dict):
        for name in names:
            if name in field_values and field_values[name] not in (None, ""):
                return field_values[name]
    return None


def get_labels(raw: dict[str, Any]) -> list[str]:
    raw_labels = raw.get("labels")
    if raw_labels is None:
        raw_labels = field_value(raw, "Labels", "labels")
    if isinstance(raw_labels, str):
        return split_field_list(raw_labels)
    if isinstance(raw_labels, list):
        out = []
        for entry in raw_labels:
            name = entry.get("name") or entry.get("label") or "" if isinstance(entry, dict) else str(entry)
            name = name.strip()
            if name:
                out.append(name)
        return out
    return []


def issue_number(raw: dict[str, Any]) -> int | None:
    value = raw.get("issue_number") or raw.get("number")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    content = raw.get("content")
    if isinstance(content, dict) and isinstance(content.get("number"), int):
        return content["number"]
    return None


# ---------------------------------------------------------------------------
# Remote item normalization + task-id assignment + dependency resolution
# ---------------------------------------------------------------------------


def normalize_project(snapshot: dict[str, Any], scope_id_override: str | None) -> dict[str, Any]:
    project = dict(snapshot.get("project") or {})
    title = project.get("title") or project.get("name") or "GitHub Project"
    project["title"] = title
    slug = slugify(title)
    project["slug"] = slug
    project["scope_id"] = scope_id_override or f"SCOPE-{slug}"
    project["base_ref"] = f"wdd/{slug}"
    return project


def normalize_remote_items(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = snapshot.get("items") or []
    if isinstance(raw_items, dict):
        raw_items = raw_items.get("nodes") or raw_items.get("items") or []
    normalized: list[dict[str, Any]] = []
    for raw in raw_items:
        content = raw.get("content") if isinstance(raw.get("content"), dict) else {}
        title = str(raw.get("title") or content.get("title") or "Untitled").strip()
        explicit_raw = field_value(raw, "wdd_id", "WDD ID", "WDDID")
        explicit_id = None
        invalid_wdd_id = None
        if explicit_raw:
            candidate = str(explicit_raw).strip()
            if candidate.upper().startswith("TASK-"):
                if TASK_ID_PATTERN.match(candidate):
                    explicit_id = candidate
                else:
                    # Looks like it was meant to be a WDD ID but fails the
                    # strict single-segment format (e.g. contains `/` or
                    # `..`). Never use it to build a path -- see
                    # process_remote_items(), which turns this into a
                    # blocking conflict instead of a generated task id.
                    invalid_wdd_id = candidate
        normalized.append(
            {
                "item_id": raw.get("item_id") or raw.get("id"),
                "issue_number": issue_number(raw),
                "url": raw.get("url") or content.get("url"),
                "title": title,
                "body": raw.get("body") or content.get("body") or "",
                "status_text": str(
                    raw.get("status") or field_value(raw, "Status", "status") or content.get("state") or ""
                ),
                "labels": get_labels(raw),
                "risk_hint": str(field_value(raw, "Risk", "WDD Risk") or ""),
                "explicit_id": explicit_id,
                "invalid_wdd_id": invalid_wdd_id,
                "depends_raw": field_value(
                    raw, "depends_on", "Depends On", "DependsOn", "Blocked By", "blocked_by"
                ),
                "conflict_raw": field_value(
                    raw, "conflict_domains", "Conflict Domains", "ConflictDomains", "Area", "Paths"
                ),
                "state": raw.get("state") or content.get("state") or "",
                "updated_at": raw.get("updated_at") or raw.get("updatedAt") or content.get("updatedAt"),
            }
        )
    return normalized


def build_reverse_lookup(manifest_items: dict[str, Any]) -> dict[str, dict[Any, str]]:
    """Map previously-seen GitHub links back to the task id this adapter
    already assigned them, so repeated pulls keep stable ids."""
    by_issue: dict[int, str] = {}
    by_item_id: dict[str, str] = {}
    for task_id, entry in manifest_items.items():
        github = entry.get("github") or {}
        if isinstance(github.get("issueNumber"), int):
            by_issue[github["issueNumber"]] = task_id
        if github.get("itemId"):
            by_item_id[str(github["itemId"])] = task_id
    return {"issue_number": by_issue, "item_id": by_item_id}


def assign_task_ids(
    items: list[dict[str, Any]], known_ids: set[str], reverse_lookup: dict[str, dict[Any, str]]
) -> None:
    used = set(known_ids)
    counter = 1
    pattern = re.compile(r"^TASK-(\d+)-")
    for task_id in used:
        match = pattern.match(task_id)
        if match:
            counter = max(counter, int(match.group(1)) + 1)
    by_issue = reverse_lookup.get("issue_number", {})
    by_item = reverse_lookup.get("item_id", {})
    for item in items:
        task_id = None
        if item.get("explicit_id"):
            task_id = item["explicit_id"]
        elif item.get("issue_number") is not None and item["issue_number"] in by_issue:
            task_id = by_issue[item["issue_number"]]
        elif item.get("item_id") and str(item["item_id"]) in by_item:
            task_id = by_item[str(item["item_id"])]
        if not task_id:
            slug = slugify(item["title"])
            candidate = f"TASK-{counter:03d}-{slug}"
            while candidate in used:
                counter += 1
                candidate = f"TASK-{counter:03d}-{slug}"
            task_id = candidate
            counter += 1
        used.add(task_id)
        item["task_id"] = task_id


def resolve_depends_on(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve a "Depends On"/"Blocked By" field to generated task ids.
    References that don't resolve are dropped and reported, never fatal."""
    by_issue = {item["issue_number"]: item["task_id"] for item in items if item.get("issue_number") is not None}
    by_item_id = {str(item["item_id"]): item["task_id"] for item in items if item.get("item_id")}
    by_title = {item["title"].strip().lower(): item["task_id"] for item in items}
    known_ids = {item["task_id"] for item in items}
    warnings: list[dict[str, Any]] = []

    def resolve_token(token: str) -> str | None:
        token = token[1:] if token.startswith("#") else token
        if token in known_ids:
            return token
        if token.isdigit() and int(token) in by_issue:
            return by_issue[int(token)]
        if token in by_item_id:
            return by_item_id[token]
        return by_title.get(token.lower())

    for item in items:
        resolved: list[str] = []
        for token in split_field_list(item.get("depends_raw")):
            match = resolve_token(token)
            if match and match != item["task_id"] and match not in resolved:
                resolved.append(match)
            else:
                warnings.append({"type": "unresolved_dependency", "task": item["task_id"], "reference": token})
        item["depends_on"] = resolved
    return warnings


def process_remote_items(
    snapshot: dict[str, Any],
    *,
    known_task_ids: set[str],
    reverse_lookup: dict[str, dict[Any, str]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    items = normalize_remote_items(snapshot)
    assign_task_ids(items, known_task_ids, reverse_lookup)
    warnings = resolve_depends_on(items)
    for item in items:
        if item.get("invalid_wdd_id"):
            warnings.append(
                {
                    "type": "invalid_wdd_id",
                    "task": item["task_id"],
                    "reference": item["invalid_wdd_id"],
                }
            )
    entries: dict[str, dict[str, Any]] = {}
    for item in items:
        conflict_domains = split_field_list(item.get("conflict_raw"))
        if not conflict_domains:
            warnings.append({"type": "empty_conflict_domains", "task": item["task_id"]})
        risk = infer_risk(item["title"], item["labels"], item["risk_hint"])
        item["conflict_domains"] = conflict_domains
        item["risk"] = risk
        entries[item["task_id"]] = {
            "id": item["task_id"],
            "title": item["title"],
            "specPath": f"tasks/{item['task_id']}.md",
            "risk": risk,
            "dependsOn": item["depends_on"],
            "conflictDomains": conflict_domains,
        }
    return items, entries, warnings


# ---------------------------------------------------------------------------
# Local plan / manifest / state IO (state.json is READ-ONLY, never written)
# ---------------------------------------------------------------------------


def load_local_plan(root: Path) -> dict[str, Any] | None:
    path = root / ".wdd" / "plan.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_task_entry(raw: dict[str, Any]) -> dict[str, Any]:
    task_id = raw["id"]
    return {
        "id": task_id,
        "title": raw.get("title") or task_id,
        "specPath": raw.get("specPath") or f"tasks/{task_id}.md",
        "risk": raw.get("risk") or "normal",
        "dependsOn": list(raw.get("dependsOn") or []),
        "conflictDomains": list(raw.get("conflictDomains") or []),
    }


def manifest_path(root: Path) -> Path:
    return root / ".wdd" / "adapters" / "github-project.json"


def load_manifest(root: Path) -> dict[str, Any]:
    path = manifest_path(root)
    if not path.exists():
        return {"schemaVersion": 1, "items": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def load_state(root: Path) -> dict[str, Any] | None:
    """Read `.wdd/state.json`. This adapter NEVER writes it -- wddctl owns it."""
    path = root / ".wdd" / "state.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Adoption links -- the convergence path for tasks created by `push`.
#
# `push` only ever emits a dry-run plan (create_remote_issue /
# add_issue_to_project / update_project_fields); this script never mutates
# GitHub. Once a human (or the GitHub CLI/connector) has actually applied
# that plan, the resulting issue number / project item id has to be written
# back into the manifest, or the *next* sync will not recognize the local
# task as already-linked and will report an id_collision. `--record-link`
# closes that loop without this adapter ever touching GitHub itself.
# ---------------------------------------------------------------------------


def parse_record_link(spec: str) -> tuple[str, dict[str, Any]]:
    """Parse one `--record-link TASK-ID=VALUE` argument. VALUE is an issue
    number (digits) or a GitHub Projects item id (anything else, e.g.
    `PVTI_...`)."""
    if "=" not in spec:
        raise RuntimeError(f"--record-link must look like TASK-ID=VALUE, got {spec!r}")
    task_id, _, value = spec.partition("=")
    task_id = task_id.strip()
    value = value.strip()
    if not TASK_ID_PATTERN.match(task_id):
        raise RuntimeError(
            f"--record-link task id {task_id!r} is not a valid task id "
            f"(must match {TASK_ID_PATTERN.pattern!r})"
        )
    if not value:
        raise RuntimeError(f"--record-link value for {task_id} is empty")
    if value.isdigit():
        return task_id, {"issueNumber": int(value)}
    return task_id, {"itemId": value}


def record_remote_links(root: Path | str, links: dict[str, dict[str, Any]]) -> list[str]:
    """Write previously-created remote issue/item identifiers for local-only
    tasks into `.wdd/adapters/github-project.json`, so the next pull/push
    matches the existing remote item instead of creating an id_collision.

    The local fingerprint is recomputed from the task's current plan.json
    entry and brief so an immediate follow-up sync does not misreport this
    as a "local changed" conflict; the remote fingerprint is left unset and
    is filled in naturally by the next real pull/push against a snapshot.
    """
    root = Path(root)
    plan = load_local_plan(root)
    if plan is None:
        raise RuntimeError("No .wdd/plan.json found; cannot record a link before a plan exists")
    tasks_by_id = {entry["id"]: normalize_task_entry(entry) for entry in plan.get("tasks", [])}

    manifest = load_manifest(root)
    items = dict(manifest.get("items") or {})
    tasks_dir = root / ".wdd" / "tasks"
    recorded: list[str] = []

    for task_id, link in links.items():
        entry = tasks_by_id.get(task_id)
        if entry is None:
            raise RuntimeError(f"--record-link references {task_id!r}, which is not a task in .wdd/plan.json")
        brief_path = root / ".wdd" / entry["specPath"]
        assert_path_contained(brief_path, tasks_dir, "task brief", root=root)
        brief_text = brief_path.read_text(encoding="utf-8") if brief_path.exists() else ""
        existing_entry = items.get(task_id) or {}
        github = dict(existing_entry.get("github") or {})
        github.update(link)
        items[task_id] = {
            "localPath": entry["specPath"],
            "github": github,
            "fingerprints": {
                "local": fingerprint_local_task(entry, brief_text),
                "remote": (existing_entry.get("fingerprints") or {}).get("remote"),
            },
        }
        recorded.append(task_id)

    manifest_out = {
        "schemaVersion": 1,
        "updatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "scope": manifest.get("scope") or {"id": (plan.get("scope") or {}).get("id")},
        "project": manifest.get("project") or {},
        "items": items,
    }
    manifest_file = manifest_path(root)
    assert_path_contained(manifest_file, root / ".wdd", "adapter manifest", root=root)
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(json.dumps(manifest_out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return recorded


def fingerprint_local_task(entry: dict[str, Any], brief_text: str) -> str:
    normalized = {
        "id": entry.get("id"),
        "title": entry.get("title"),
        "specPath": entry.get("specPath"),
        "risk": entry.get("risk"),
        "dependsOn": sorted(entry.get("dependsOn") or []),
        "conflictDomains": sorted(entry.get("conflictDomains") or []),
        "brief": brief_text,
    }
    return fingerprint_text(json.dumps(normalized, sort_keys=True))


def fingerprint_remote_task(item: dict[str, Any], entry: dict[str, Any]) -> str:
    normalized = {
        "title": item.get("title"),
        "body": item.get("body"),
        "status": (item.get("status_text") or "").strip().lower(),
        "labels": sorted(item.get("labels") or []),
        "url": item.get("url"),
        "issueNumber": item.get("issue_number"),
        "risk": entry.get("risk"),
        "dependsOn": sorted(entry.get("dependsOn") or []),
        "conflictDomains": sorted(entry.get("conflictDomains") or []),
    }
    return fingerprint_text(json.dumps(normalized, sort_keys=True))


# ---------------------------------------------------------------------------
# Task brief rendering (replaces render_epic/render_ticket/render_task)
# ---------------------------------------------------------------------------


def render_task(entry: dict[str, Any], item: dict[str, Any]) -> str:
    """One brief per task, matching skills/wdd-plan/templates/task.md."""
    summary = extract_summary(item.get("body") or "") or entry["title"]
    url = item.get("url") or "unknown"
    domains = entry["conflictDomains"]
    if domains:
        domains_block = "\n".join(f"- `{domain}`" for domain in domains)
    else:
        domains_block = (
            "- TODO: no conflict-domains field was found on the GitHub item.\n"
            "  Empty conflict domains give NO collision protection -- list every\n"
            "  path or glob this task writes before it is run."
        )
    depends_line = ", ".join(entry["dependsOn"]) if entry["dependsOn"] else "None recorded."
    return f"""# {entry['id']}: {entry['title']}

## Objective

{summary}

## Scope

- Included: the scope described by the linked GitHub item ({url}).

## Non-scope

- Unspecified until refined with `wdd-plan`.

## Files to read first

- The linked GitHub item: {url}
- Depends on: {depends_line}

## Conflict domains

{domains_block}

## Verification

- TODO: define the project-specific verification command.

## Definition of done

- [ ] Objective is complete.
- [ ] Verification passes.
- [ ] No changes outside the declared conflict domains.
"""


def local_task_body(root: Path, entry: dict[str, Any]) -> str:
    path = root / ".wdd" / entry["specPath"]
    if path.exists():
        return path.read_text(encoding="utf-8")
    return entry.get("title") or entry["id"]


# ---------------------------------------------------------------------------
# Planning: builds a dry-run operation plan for pull and/or push
# ---------------------------------------------------------------------------


def plan_sync(
    root: Path | str,
    remote_snapshot: dict[str, Any],
    *,
    mode: str = "sync",
    scope_id: str | None = None,
) -> dict[str, Any]:
    root = Path(root)
    project = normalize_project(remote_snapshot, scope_id)
    resolved_scope_id = project["scope_id"]

    existing_plan = load_local_plan(root)
    manifest = load_manifest(root)
    manifest_items = manifest.get("items") or {}

    if existing_plan is not None:
        existing_scope_id = (existing_plan.get("scope") or {}).get("id")
        if existing_scope_id and existing_scope_id != resolved_scope_id:
            conflict = {
                "type": "scope_mismatch",
                "reason": (
                    f"existing plan.json scope is {existing_scope_id!r}, but this "
                    f"GitHub Project maps to {resolved_scope_id!r}. Pass --scope-id "
                    f"{existing_scope_id!r} to target the existing scope explicitly."
                ),
            }
            return {
                "schemaVersion": 1,
                "mode": mode,
                "scopeId": resolved_scope_id,
                "project": project,
                "operations": [],
                "conflicts": [conflict],
                "warnings": [],
                "remoteItems": [],
                "planTasks": {},
                "briefs": {},
                "existingPlan": existing_plan,
            }

    existing_tasks_by_id = {
        entry["id"]: normalize_task_entry(entry) for entry in (existing_plan or {}).get("tasks", [])
    }
    known_ids = set(existing_tasks_by_id) | set(manifest_items)
    reverse_lookup = build_reverse_lookup(manifest_items)
    items, desired_entries, warnings = process_remote_items(
        remote_snapshot, known_task_ids=known_ids, reverse_lookup=reverse_lookup
    )

    # A malformed "WDD ID" field is remote, attacker-controlled data that this
    # adapter would otherwise turn directly into a filesystem path (task
    # specPath / brief path). Block the whole sync -- same fail-closed
    # treatment as scope_mismatch/id_collision -- rather than silently
    # falling back to a generated id. assert_path_contained() below is the
    # independent second check on the actual write path.
    invalid_id_warnings = [w for w in warnings if w["type"] == "invalid_wdd_id"]
    warnings = [w for w in warnings if w["type"] != "invalid_wdd_id"]
    conflicts: list[dict[str, Any]] = [
        {
            "type": "invalid_wdd_id",
            "task": w["task"],
            "reason": (
                f"remote WDD ID {w['reference']!r} is not a valid task id -- it must match "
                f"{TASK_ID_PATTERN.pattern!r} (no `/`, no `..`, no other path separators). "
                "Fix the WDD ID field on the GitHub Project item/issue and re-sync; nothing "
                "will be written until this is resolved."
            ),
        }
        for w in invalid_id_warnings
    ]
    operations: list[dict[str, Any]] = []
    plan_tasks: dict[str, dict[str, Any]] = {}
    brief_ops: list[dict[str, Any]] = []

    if mode in {"pull", "sync"}:
        for item in items:
            task_id = item["task_id"]
            entry = desired_entries[task_id]
            manifest_entry = manifest_items.get(task_id)
            existing_entry = existing_tasks_by_id.get(task_id)

            if manifest_entry:
                brief_path = root / ".wdd" / (existing_entry or entry)["specPath"]
                existing_brief_text = brief_path.read_text(encoding="utf-8") if brief_path.exists() else ""
                local_fp_now = fingerprint_local_task(existing_entry or entry, existing_brief_text)
                remote_fp_now = fingerprint_remote_task(item, entry)
                local_changed = local_fp_now != (manifest_entry.get("fingerprints") or {}).get("local")
                remote_changed = remote_fp_now != (manifest_entry.get("fingerprints") or {}).get("remote")
                if local_changed and remote_changed:
                    conflicts.append(
                        {
                            "type": "task_conflict",
                            "task": task_id,
                            "reason": "local task and GitHub item both changed since the last sync",
                            "localPath": entry["specPath"],
                            "githubUrl": item.get("url"),
                        }
                    )
                    continue
                if not remote_changed:
                    continue  # local wins, or nothing changed -- do not touch it
            elif existing_entry is not None:
                conflicts.append(
                    {
                        "type": "id_collision",
                        "task": task_id,
                        "reason": (
                            "plan.json already has a task with this id that this adapter "
                            "did not import; rename or remove it locally before pulling"
                        ),
                    }
                )
                continue

            plan_tasks[task_id] = entry
            brief_ops.append(
                {
                    "action": "create_task_brief" if task_id not in existing_tasks_by_id else "update_task_brief",
                    "task": task_id,
                    "path": entry["specPath"],
                    "brief": render_task(entry, item),
                }
            )

        if plan_tasks:
            if existing_plan is None:
                operations.append(
                    {
                        "action": "create_plan",
                        "scopeId": resolved_scope_id,
                        "baseRef": project["base_ref"],
                        "taskIds": sorted(plan_tasks),
                    }
                )
            else:
                operations.append(
                    {
                        "action": "update_plan",
                        "scopeId": resolved_scope_id,
                        "added": sorted(set(plan_tasks) - set(existing_tasks_by_id)),
                        "updated": sorted(set(plan_tasks) & set(existing_tasks_by_id)),
                    }
                )
        operations.extend(brief_ops)
        if operations and not conflicts:
            operations.append({"action": "write_manifest"})

    if conflicts:
        operations = []

    if mode in {"push", "sync"} and not conflicts:
        state = load_state(root)
        has_controller_state = state is not None
        state_tasks = (state or {}).get("tasks") or {}
        remote_by_link: dict[tuple[str, Any], dict[str, Any]] = {}
        for item in items:
            if item.get("issue_number") is not None:
                remote_by_link[("issue", item["issue_number"])] = item
            if item.get("item_id"):
                remote_by_link[("item", str(item["item_id"]))] = item

        for task_id, entry in sorted(existing_tasks_by_id.items()):
            # No controller state at all means we do not actually know this
            # task's status -- e.g. a task just imported by `pull
            # --apply-local`, which writes no state.json. Defaulting to
            # "todo" here would silently regress an already-in-progress
            # remote item the moment someone runs `push`. Only compute a
            # desired_status when wddctl has actually recorded one.
            task_state = state_tasks.get(task_id) if has_controller_state else None
            desired_status = local_status_to_remote(task_state["status"]) if task_state else None
            manifest_entry = manifest_items.get(task_id) or {}
            github_link = manifest_entry.get("github") or {}
            has_link = github_link.get("issueNumber") is not None or bool(github_link.get("itemId"))
            linked_item = None
            if github_link.get("issueNumber") is not None:
                linked_item = remote_by_link.get(("issue", github_link["issueNumber"]))
            if linked_item is None and github_link.get("itemId"):
                linked_item = remote_by_link.get(("item", str(github_link["itemId"])))

            if has_link and linked_item is None:
                # The manifest already records a remote link for this task,
                # but the fetched Project snapshot does not contain that
                # issue/item -- e.g. it was removed from the Project board,
                # or the snapshot fetch was partial/empty. This is NOT the
                # same as "no link at all": falling through to the
                # create_remote_issue branch below would create a duplicate
                # issue for a task that already has one. Never do that.
                if github_link.get("issueNumber") is not None:
                    # We at least know the issue number, so the safe,
                    # non-destructive recovery is to re-add that existing
                    # issue to the Project rather than manufacture a new one.
                    operations.append(
                        {
                            "action": "add_issue_to_project",
                            "task": task_id,
                            "projectOwner": project.get("owner"),
                            "projectNumber": project.get("number"),
                            "issueNumber": github_link["issueNumber"],
                        }
                    )
                    warnings.append(
                        {
                            "type": "linked_item_missing_from_snapshot",
                            "task": task_id,
                            "reason": (
                                f"task {task_id} is linked to issue #{github_link['issueNumber']} in "
                                ".wdd/adapters/github-project.json, but that issue was not present "
                                "in the fetched GitHub Project snapshot -- re-adding the existing "
                                "issue to the project instead of creating a duplicate. If it was "
                                "intentionally removed from the project, update or clear the link "
                                "in the manifest and re-push."
                            ),
                        }
                    )
                else:
                    # Only a bare Project item id was recorded (no issue
                    # number), and that item is gone from the snapshot.
                    # There is nothing safe to auto-recover here -- block and
                    # ask a human to look, rather than guess.
                    conflicts.append(
                        {
                            "type": "linked_item_missing_from_snapshot",
                            "task": task_id,
                            "reason": (
                                f"task {task_id} is linked to project item {github_link.get('itemId')!r} "
                                "in .wdd/adapters/github-project.json, but that item was not found in "
                                "the fetched GitHub Project snapshot. Refusing to create a duplicate "
                                "issue -- verify the item still exists on the GitHub Project, or "
                                "update/clear the manifest link, then re-push."
                            ),
                        }
                    )
                continue

            if linked_item is None:
                # Genuinely new: no manifest link exists for this task at
                # all. Creating the remote issue/item and setting its
                # non-status fields is safe even with no controller state:
                # there is no existing remote status to regress. Only omit
                # "Status" so the project's own default column applies
                # instead of an invented "todo".
                fields = {"WDD ID": task_id, "Risk": entry.get("risk", "normal")}
                if desired_status is not None:
                    fields["Status"] = desired_status
                else:
                    warnings.append(
                        {
                            "type": "status_skipped_no_controller_state",
                            "task": task_id,
                            "reason": (
                                "no .wdd/state.json found; creating the remote issue without "
                                "a Status field so the project's default column applies -- "
                                "run wddctl to establish controller state, then re-push"
                            ),
                        }
                    )
                operations.append(
                    {
                        "action": "create_remote_issue",
                        "task": task_id,
                        "repo": project.get("repo"),
                        "title": entry.get("title") or task_id,
                        "body": local_task_body(root, entry),
                        "labels": ["wdd", f"wdd:risk-{entry.get('risk', 'normal')}"],
                    }
                )
                operations.append(
                    {
                        "action": "add_issue_to_project",
                        "task": task_id,
                        "projectOwner": project.get("owner"),
                        "projectNumber": project.get("number"),
                        "requiresIssueUrlFrom": task_id,
                    }
                )
                operations.append({"action": "update_project_fields", "task": task_id, "fields": fields})
                continue

            if desired_status is None:
                # Task is already linked to a remote item with its own
                # status; refuse to emit a status-changing operation rather
                # than overwrite it with an invented "todo". Preserve
                # whatever the remote currently has.
                warnings.append(
                    {
                        "type": "status_skipped_no_controller_state",
                        "task": task_id,
                        "reason": (
                            "no .wdd/state.json found; refusing to push a Status change -- "
                            f"the remote Status ({linked_item.get('status_text') or 'unknown'!r}) "
                            "is preserved as-is. Run wddctl to establish controller state, "
                            "then re-push"
                        ),
                    }
                )
                continue

            current_status = (linked_item.get("status_text") or "").strip()
            if current_status.lower() != desired_status.lower():
                operations.append(
                    {
                        "action": "update_project_fields",
                        "task": task_id,
                        "issueNumber": linked_item.get("issue_number"),
                        "fields": {"WDD ID": task_id, "Status": desired_status, "Risk": entry.get("risk", "normal")},
                    }
                )

    if conflicts:
        # Re-assert the invariant "conflicts present => no operations" --
        # the push loop above can itself append a conflict (e.g.
        # linked_item_missing_from_snapshot) after other operations were
        # already appended for earlier tasks in the same loop.
        operations = []

    return {
        "schemaVersion": 1,
        "mode": mode,
        "scopeId": resolved_scope_id,
        "project": project,
        "operations": operations,
        "conflicts": conflicts,
        "warnings": warnings,
        "remoteItems": items,
        "planTasks": plan_tasks,
        "briefs": {op["task"]: op["brief"] for op in brief_ops},
        "existingPlan": existing_plan,
    }


# ---------------------------------------------------------------------------
# Applying local writes (pull/sync only; push never writes anything itself)
# ---------------------------------------------------------------------------


def build_manifest(root: Path, result: dict[str, Any], written_plan: dict[str, Any]) -> dict[str, Any]:
    manifest = load_manifest(root)
    items = dict(manifest.get("items") or {})
    written_tasks_by_id = {task["id"]: task for task in written_plan["tasks"]}
    remote_by_task_id = {item["task_id"]: item for item in result.get("remoteItems") or []}
    for task_id, entry in (result.get("planTasks") or {}).items():
        remote_item = remote_by_task_id.get(task_id)
        if not remote_item:
            continue
        brief_path = root / ".wdd" / entry["specPath"]
        brief_text = brief_path.read_text(encoding="utf-8") if brief_path.exists() else ""
        items[task_id] = {
            "localPath": entry["specPath"],
            "github": {
                "itemId": remote_item.get("item_id"),
                "issueNumber": remote_item.get("issue_number"),
                "url": remote_item.get("url"),
            },
            "fingerprints": {
                "local": fingerprint_local_task(written_tasks_by_id.get(task_id, entry), brief_text),
                "remote": fingerprint_remote_task(remote_item, entry),
            },
        }
    return {
        "schemaVersion": 1,
        "updatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "scope": {"id": result["scopeId"]},
        "project": result["project"],
        "items": items,
    }


def apply_local_operations(root: Path | str, result: dict[str, Any]) -> None:
    root = Path(root)
    if result.get("conflicts"):
        raise RuntimeError("Refusing to apply local operations while conflicts exist")
    plan_tasks: dict[str, dict[str, Any]] = result.get("planTasks") or {}
    briefs: dict[str, str] = result.get("briefs") or {}
    if not plan_tasks and not briefs:
        return

    existing_plan = result.get("existingPlan")
    if existing_plan is None:
        plan = {
            "schemaVersion": 1,
            "kind": "wdd_plan",
            "scope": {
                "id": result["scopeId"],
                "baseRef": result["project"]["base_ref"],
                "maxConcurrent": None,
                "reviewPolicy": "risk_based",
                "reconcileEveryNMerges": 3,
            },
            "tasks": [plan_tasks[task_id] for task_id in sorted(plan_tasks)],
        }
    else:
        tasks_by_id = {task["id"]: task for task in existing_plan.get("tasks", [])}
        ordered_ids = list(tasks_by_id)
        for task_id, entry in plan_tasks.items():
            if task_id not in tasks_by_id:
                ordered_ids.append(task_id)
            tasks_by_id[task_id] = entry
        plan = dict(existing_plan)
        plan["tasks"] = [tasks_by_id[task_id] for task_id in ordered_ids]

    wdd_dir = root / ".wdd"
    tasks_dir = wdd_dir / "tasks"

    plan_path = wdd_dir / "plan.json"
    assert_path_contained(plan_path, wdd_dir, "plan.json", root=root)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")

    for task_id, brief_text in briefs.items():
        brief_path = wdd_dir / plan_tasks[task_id]["specPath"]
        # Belt and braces (see assert_path_contained docstring): specPath is
        # derived from a remote "WDD ID" field, which is attacker-controlled.
        assert_path_contained(brief_path, tasks_dir, "task brief", root=root)
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        brief_path.write_text(brief_text, encoding="utf-8")

    manifest = build_manifest(root, result, plan)
    manifest_file = manifest_path(root)
    assert_path_contained(manifest_file, wdd_dir, "adapter manifest", root=root)
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# GitHub CLI access + CLI entrypoint
# ---------------------------------------------------------------------------


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
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)}\n{result.stderr}")
    return json.loads(result.stdout or "{}")


def fetch_project_snapshot(owner: str, number: int, repo: str | None) -> dict[str, Any]:
    if not shutil.which("gh"):
        raise RuntimeError("gh is required when --remote-json is not provided")
    project = run_json(["gh", "project", "view", str(number), "--owner", owner, "--format", "json"])
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
        },
        "items": items_result.get("items") or [],
    }


def print_text_plan(result: dict[str, Any]) -> None:
    print(f"Scope: {result['scopeId']}")
    print(f"Mode: {result['mode']}")
    if result["conflicts"]:
        print("\nConflicts (nothing will be written until these are resolved):")
        for conflict in result["conflicts"]:
            print(f"- [{conflict.get('type')}] {conflict.get('task', '')}: {conflict['reason']}".replace("  ", " "))
        return

    empty_domain_tasks = [w["task"] for w in result["warnings"] if w.get("type") == "empty_conflict_domains"]
    if empty_domain_tasks:
        print(
            "\nWARNING: the following tasks have NO conflictDomains from GitHub. Empty "
            "conflict domains give NO collision protection -- two tasks could be admitted "
            "concurrently and write the same files. Fill these in by hand (plan.json, or "
            "via the wdd-plan skill) before running `wddctl plan apply`:"
        )
        for task_id in empty_domain_tasks:
            print(f"  - {task_id}")

    unresolved = [w for w in result["warnings"] if w.get("type") == "unresolved_dependency"]
    if unresolved:
        print("\nUnresolved dependency references (dropped, not applied):")
        for warning in unresolved:
            print(f"  - {warning['task']}: {warning['reference']!r} did not match any known item")

    status_skipped = [w for w in result["warnings"] if w.get("type") == "status_skipped_no_controller_state"]
    if status_skipped:
        print(
            "\nWARNING: no .wdd/state.json found, so no Status changes were pushed for the "
            "following tasks (their current remote Status is preserved as-is):"
        )
        for warning in status_skipped:
            print(f"  - {warning['task']}: {warning['reason']}")

    missing_linked_items = [w for w in result["warnings"] if w.get("type") == "linked_item_missing_from_snapshot"]
    if missing_linked_items:
        print(
            "\nWARNING: the following tasks are already linked to a remote issue that is "
            "missing from the fetched Project snapshot. NOT creating a duplicate issue -- "
            "re-adding the existing one to the project instead:"
        )
        for warning in missing_linked_items:
            print(f"  - {warning['task']}: {warning['reason']}")

    applied = result.get("appliedOperations")
    if applied is not None:
        if applied:
            print("\nApplied:")
            for operation in applied:
                label = operation.get("task") or operation.get("scopeId") or ""
                print(f"- {operation['action']} {label}".rstrip())
        else:
            print("\nNothing to apply; local artifacts already match the project.")
        if result["operations"]:
            print("\nStill outstanding after applying (these need a remote change or a human):")
            for operation in result["operations"]:
                label = operation.get("task") or operation.get("scopeId") or ""
                print(f"- {operation['action']} {label}".rstrip())
        return

    if not result["operations"]:
        print("\nNo operations.")
        return
    print("\nOperations:")
    for operation in result["operations"]:
        label = operation.get("task") or operation.get("scopeId") or ""
        print(f"- {operation['action']} {label}".rstrip())

    if any(op["action"] == "create_remote_issue" for op in result["operations"]):
        print(
            "\nAfter applying create_remote_issue/add_issue_to_project by hand, record the "
            "resulting id so the next sync matches instead of colliding:\n"
            "  python3 wdd_github_project_sync.py record-link --root . "
            "--record-link TASK-ID=<issue-number-or-item-id>"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["inspect", "pull", "push", "sync", "record-link"])
    parser.add_argument("--root", default=".", help="Repository root (contains .wdd/)")
    parser.add_argument("--scope-id", help="Override the derived WDD scope id")
    parser.add_argument("--remote-json", type=Path, help="GitHub Project snapshot JSON")
    parser.add_argument("--project-owner", help="GitHub Project owner")
    parser.add_argument("--project-number", type=int, help="GitHub Project number")
    parser.add_argument("--repo", help="GitHub repository owner/name for issue writes")
    parser.add_argument("--apply-local", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    parser.add_argument(
        "--record-link",
        action="append",
        default=[],
        metavar="TASK-ID=ISSUE_NUMBER_OR_ITEM_ID",
        help=(
            "Only valid with the 'record-link' mode. Record a remote issue "
            "number or GitHub Projects item id for a local-only task that a "
            "previously-applied `push` plan created, so the next sync "
            "matches it instead of reporting an id_collision. Repeatable."
        ),
    )
    args = parser.parse_args(argv)

    root = Path(args.root)

    if args.mode == "record-link":
        if not args.record_link:
            parser.error("record-link mode requires at least one --record-link TASK-ID=VALUE")
        links: dict[str, dict[str, Any]] = {}
        for spec in args.record_link:
            task_id, link = parse_record_link(spec)
            links[task_id] = link
        recorded = record_remote_links(root, links)
        if args.json:
            print(json.dumps({"recorded": recorded}, indent=2, sort_keys=True))
        else:
            for task_id in recorded:
                print(f"Recorded remote link for {task_id}")
        return 0

    if args.remote_json:
        snapshot = load_remote_json(args.remote_json)
    else:
        if not args.project_owner or not args.project_number:
            parser.error("--project-owner and --project-number are required without --remote-json")
        snapshot = fetch_project_snapshot(args.project_owner, args.project_number, args.repo)

    mode = "sync" if args.mode == "inspect" else args.mode
    result = plan_sync(root, snapshot, mode=mode, scope_id=args.scope_id)

    if args.apply_local:
        if args.mode not in {"pull", "sync"}:
            parser.error("--apply-local is only valid for pull or sync")
        applied_operations = list(result["operations"])
        apply_local_operations(root, result)
        # Re-plan to confirm convergence, but report what was actually done:
        # reporting only the post-apply plan would always say "no operations".
        result = plan_sync(root, snapshot, mode=mode, scope_id=args.scope_id)
        result["appliedOperations"] = applied_operations

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    else:
        print_text_plan(result)
    return 1 if result["conflicts"] else 0


def cli() -> int:
    """Report refusals as errors, not as an uncaught traceback.

    The refusals this script raises are safety decisions — a traversal-shaped
    task id, an unresolved conflict — and a stack trace buries the reason.
    """
    try:
        return main()
    except RuntimeError as error:
        print(f"wdd-sync-github-project: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
