---
name: wdd-sync-github-project
description: Sync Wave-Driven Development local epics with GitHub Projects as an optional adapter. Use when Codex needs to import or pull GitHub Project epics, tickets, or tasks into .wdd/epics, push local WDD tickets or tasks toward GitHub Issues/Projects, compare local and remote planning state, generate dry-run sync plans, write adapter manifests, or report GitHub/WDD conflicts without making GitHub Projects a required WDD backend.
---

# WDD Sync GitHub Project

Use this skill to mirror a GitHub Project and a local WDD epic while preserving
WDD's rule that `.wdd/` artifacts are the execution source of truth.

## Core Rules

- Treat GitHub Projects as an adapter, not canonical WDD storage.
- Default to dry-run. Do not apply local or remote changes until the plan has
  been inspected.
- Never auto-resolve conflicts. If local and GitHub changed since the last
  manifest fingerprint, report the conflict and stop.
- Do not mark local tasks `done` from GitHub `Done` unless the user explicitly
  trusts remote done state. Default import maps remote `Done` tasks to
  `review` so local verification can be recorded.
- Prefer one GitHub Project per WDD epic. For larger boards, first export or
  filter the Project items for one epic, then sync that subset.

## Resources

- Use `scripts/wdd_github_project_sync.py` for inspection, planning, pull
  imports, local writes, and remote operation plans.
- Read `references/github-projects.md` before live GitHub work, nonstandard
  field mappings, larger-board imports, or conflict resolution.

## Workflow

1. Inspect local and remote state:

   ```bash
   python3 skills/wdd-sync-github-project/scripts/wdd_github_project_sync.py inspect \
     --root . \
     --project-owner OWNER \
     --project-number 4 \
     --repo OWNER/REPO
   ```

   Use `--remote-json snapshot.json` instead of live `gh` access when working
   from an exported Project snapshot or fixture.

2. Pull GitHub Project items into a local epic:

   ```bash
   python3 skills/wdd-sync-github-project/scripts/wdd_github_project_sync.py pull \
     --root . \
     --project-owner OWNER \
     --project-number 4 \
     --repo OWNER/REPO
   ```

   Inspect the dry-run. If it is correct:

   ```bash
   python3 skills/wdd-sync-github-project/scripts/wdd_github_project_sync.py pull \
     --root . \
     --project-owner OWNER \
     --project-number 4 \
     --repo OWNER/REPO \
     --apply-local
   ```

   This creates or updates `.wdd/epics/<EPIC-ID>/` artifacts and writes
   `.wdd/epics/<EPIC-ID>/adapters/github-project.json`.

3. Push local WDD planning state toward GitHub:

   ```bash
   python3 skills/wdd-sync-github-project/scripts/wdd_github_project_sync.py push \
     --root . \
     --epic-id EPIC-example \
     --project-owner OWNER \
     --project-number 4 \
     --repo OWNER/REPO
   ```

   Treat the output as an operation plan. Create missing GitHub Issues, add
   them to the Project, and set fields only after inspecting the plan and
   confirming repository/project permissions.

4. Run bidirectional sync:

   ```bash
   python3 skills/wdd-sync-github-project/scripts/wdd_github_project_sync.py sync \
     --root . \
     --epic-id EPIC-example \
     --project-owner OWNER \
     --project-number 4 \
     --repo OWNER/REPO
   ```

   If conflicts are reported, inspect both the local artifact and the GitHub
   issue before applying either side. Update one side deliberately, then rerun
   the dry-run.

## Mapping

- GitHub Project title or `project.wdd_id` maps to the local WDD epic ID.
- Project items with `WDD Kind = ticket` or `TICKET-*` IDs map to ticket
  folders.
- Project items with `WDD Kind = task`, `TASK-*` IDs, or a `Ticket` field map
  to task files.
- `WDD ID`, `WDD Kind`, `Ticket`, `Wave`, and `Status` fields are preferred.
- Missing WDD IDs are generated from titles during pull import.
- GitHub issue links are written under `adapter_links.github_issue` and the
  adapter manifest.

## Done When

- The dry-run plan has been inspected.
- Any local changes are written only with `--apply-local`.
- `.wdd/epics/<EPIC-ID>/adapters/github-project.json` records project links,
  item links, and fingerprints.
- Conflicts, if any, are reported instead of overwritten.
- The next WDD phase remains explicit, usually `wdd-plan-epic`, `wdd-status`,
  or `wdd-start-wave`.
