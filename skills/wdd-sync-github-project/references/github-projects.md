# GitHub Projects Adapter Reference

Read this before live GitHub sync work, nonstandard field mappings, or
conflict resolution.

## Model

WDD remains local-first. GitHub Projects are planning mirrors.

```text
one GitHub Project  = one WDD scope (SCOPE-<project-slug>)
GitHub Project item = one WDD task
GitHub custom fields = WDD task metadata (risk, dependsOn, conflictDomains)
```

There are no epics or tickets in the current model, and no kanban status
folders. `.wdd/plan.json` is the single planning input; each task also gets
one brief at `.wdd/tasks/<TASK-ID>.md`. Task execution state lives in
`.wdd/state.json`, owned exclusively by `wddctl` -- this adapter reads it,
never writes it.

## Preferred GitHub Project fields

None of these are required; the adapter degrades gracefully when a field is
missing.

| Field | Purpose |
|-------|---------|
| `WDD ID` / `wdd_id` | Force a stable `TASK-<n>-<slug>` id instead of a generated one |
| `Depends On` / `Blocked By` | Comma/newline-separated issue numbers, item titles, or task ids |
| `Conflict Domains` / `Area` / `Paths` | Comma/newline-separated paths/globs the task writes |
| `Risk` / `WDD Risk` | Extra free-text signal fed into the risk heuristic |
| `Status` | Mapped to/from the WDD task status on push |
| `Labels` | Also scanned by the risk heuristic |

## Risk heuristic

A task is `"risk": "high"` iff its title, labels, or an explicit
`Risk`/`WDD Risk` field mentions: `auth`, `security`, `migrat*` (migration,
migrating), `persist*` (persistence, persisted), `public api`, or
`breaking change`. Everything else is `"normal"`. This is a small,
best-effort heuristic -- always sanity-check imported risk levels by hand.

## Conflict domains -- read this before trusting an import

If a GitHub item has no `Conflict Domains`/`Area`/`Paths` field, the
imported task gets `"conflictDomains": []` and the dry-run output prints an
explicit warning. **An empty list means the WDD engine provides no collision
protection for that task** -- it can be admitted concurrently with another
task that writes the same files. Always fill in conflict domains (directly
in `plan.json`, or via the `wdd-plan` skill) before running
`wddctl plan apply`.

## Status mapping (push only; pull does not touch status)

`plan.json` and task briefs carry no status field -- only `.wdd/state.json`
does, and only `wddctl` writes it. Pull therefore never needs a remote ->
local status mapping. Push maps the other direction, local -> remote, using
the task's current status from `.wdd/state.json`:

| WDD status | GitHub `Status` field |
|------------|------------------------|
| `todo` | `Todo` |
| `in_progress` | `In Progress` |
| `review` | `Review` |
| `merge_ready` | `Merge Ready` |
| `done` | `Done` |
| `blocked` | `Blocked` |
| `cancelled` | `Cancelled` |

## Manifest

Path (repo-wide, not per-scope):

```text
.wdd/adapters/github-project.json
```

Shape:

```json
{
  "schemaVersion": 1,
  "updatedAt": "2026-06-12T10:00:00Z",
  "scope": {"id": "SCOPE-example"},
  "project": {
    "owner": "OWNER",
    "number": 4,
    "title": "Example",
    "url": "https://github.com/orgs/OWNER/projects/4",
    "repo": "OWNER/REPO"
  },
  "items": {
    "TASK-001-example": {
      "localPath": "tasks/TASK-001-example.md",
      "github": {
        "itemId": "PVTI_...",
        "issueNumber": 123,
        "url": "https://github.com/OWNER/REPO/issues/123"
      },
      "fingerprints": {
        "local": "sha256:...",
        "remote": "sha256:..."
      }
    }
  }
}
```

Fingerprints let the adapter detect three cases per task:

- Local changed, remote unchanged: local wins, nothing is written.
- Remote changed, local unchanged: plan a local update.
- Both changed: report a conflict and write neither side for that task.

They also protect hand-filled `conflictDomains`/`dependsOn` edits: a later
pull only overwrites a task when the remote side changed and the local side
did not.

## Snapshot JSON

For offline runs, pass `--remote-json snapshot.json`. The script accepts
this compact shape:

```json
{
  "project": {
    "owner": "OWNER",
    "number": 4,
    "title": "Example",
    "url": "https://github.com/orgs/OWNER/projects/4",
    "repo": "OWNER/REPO"
  },
  "items": [
    {
      "item_id": "PVTI_...",
      "issue_number": 123,
      "url": "https://github.com/OWNER/REPO/issues/123",
      "title": "Token type contract",
      "body": "Item description",
      "status": "Todo",
      "labels": ["auth"],
      "depends_on": "#124, Some other item title",
      "conflict_domains": "src/auth/**, src/schema.ts"
    }
  ]
}
```

## Live GitHub access

The script uses GitHub CLI when no `--remote-json` is provided:

```bash
gh project view <number> --owner <owner> --format json
gh project item-list <number> --owner <owner> --format json --limit 1000
```

For remote writes (push mode), inspect the generated operation plan, then
apply it deliberately with the GitHub CLI or connector. This script never
mutates GitHub itself -- it only produces the plan.

## Conflict handling

When conflicts are reported:

1. Open the local brief named by `localPath` (or the task in `plan.json`).
2. Open the GitHub item's URL.
3. Decide which side wins, or manually merge both.
4. Update one side.
5. Rerun the dry-run.
6. Apply local writes (`--apply-local`) only once the conflict list is empty.

Do not delete manifest fingerprints to bypass conflict detection unless the
user explicitly chooses to re-baseline sync state.
