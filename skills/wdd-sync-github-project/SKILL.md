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

Emits a dry-run operation plan for updating remote status/fields from
`.wdd/state.json` and `.wdd/plan.json`. Never mutates GitHub itself.

## Next steps

After a pull, run `wdd-plan` to review conflict domains and dependencies,
then `wdd-run` to execute the scope.
