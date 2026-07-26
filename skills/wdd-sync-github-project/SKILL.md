---
name: wdd-sync-github-project
description: Sync Wave-Driven Development's local plan with a GitHub Project as an optional adapter. Use when Codex needs to import a GitHub Project into .wdd/plan.json and .wdd/tasks/, push local task status/fields toward a GitHub Project, compare local and remote planning state, generate dry-run sync plans, or report conflicts, without making GitHub Projects a required WDD backend.
---

# WDD Sync GitHub Project

GitHub Projects is a planning mirror. `.wdd/` -- `plan.json`, `tasks/*.md`,
and `state.json` -- remains the execution source of truth. `state.json` is
owned exclusively by `wddctl`; this adapter only ever reads it, never writes
it.

## Core rules

- Default to dry-run. Nothing is written locally until `--apply-local`.
- Never auto-resolve conflicts. If a task changed both locally and on
  GitHub since the last sync, report it and write nothing for that task.
- One GitHub Project maps to one WDD scope (`SCOPE-<project-slug>`).
- A remote "WDD ID" must be a bare `TASK-<slug>` (no `/`, no `..`);
  anything else blocks the sync as a conflict instead of touching disk.

## Pull (GitHub -> local)

```bash
python3 scripts/wdd_github_project_sync.py pull --root . \
  --project-owner OWNER --project-number 4 --repo OWNER/REPO
```

Produces `.wdd/plan.json` and one brief per task at `.wdd/tasks/<TASK-ID>.md`.
`risk` is inferred from labels/title (auth, security, migration,
persistence, public API, breaking change); everything else is `normal`.
`dependsOn` resolves a "Depends On"/"Blocked By" field when present.
**Imported tasks with no "Conflict Domains" field come back with an empty
list and a loud dry-run warning** -- empty domains give no collision
protection, so review and fill them in (`wdd-plan`) before running
`wddctl plan apply`. Rerun with `--apply-local` to write.

## Push (local -> GitHub)

```bash
python3 scripts/wdd_github_project_sync.py push --root . \
  --project-owner OWNER --project-number 4 --repo OWNER/REPO
```

Emits a dry-run operation plan from `.wdd/state.json` and `.wdd/plan.json`.
Never mutates GitHub itself. **Without `.wdd/state.json`** (e.g. right after
`pull --apply-local`, which never writes it), push does not guess a status
and will not push a task back down to "Todo" -- it skips status changes,
reports why, and still emits safe non-status operations (issue creation,
WDD ID/Risk fields).

After applying `create_remote_issue`/`add_issue_to_project` by hand, record
the created id so the next sync matches instead of an `id_collision`:

```bash
python3 scripts/wdd_github_project_sync.py record-link --root . \
  --record-link TASK-001-example=123
```

Repeatable; the value is an issue number or a project item id (`PVTI_...`).

## Next steps

After a pull, run `wdd-plan` to review conflict domains and dependencies,
then `wdd-run` to execute the scope.
