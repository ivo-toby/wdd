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
| `WDD ID` / `wdd_id` | Force a stable `TASK-<n>-<slug>` id instead of a generated one (must match `^TASK-[A-Za-z0-9][A-Za-z0-9._-]*$`) |
| `Depends On` / `Blocked By` | Comma/newline-separated issue numbers, item titles, or task ids |
| `Conflict Domains` / `Area` / `Paths` | Comma/newline-separated paths/globs the task writes |
| `Risk` / `WDD Risk` | Extra free-text signal fed into the risk heuristic |
| `Status` | Mapped to/from the WDD task status on push |
| `Labels` | Also scanned by the risk heuristic |

## WDD ID validation -- security note

A GitHub Project's `WDD ID`/`wdd_id` field is remote, untrusted data, and it
feeds directly into a filesystem path (`.wdd/tasks/<TASK-ID>.md`). A value
like `TASK-/../../../escaped` would otherwise resolve outside `.wdd/`. To
prevent that:

1. The field is validated against `^TASK-[A-Za-z0-9][A-Za-z0-9._-]*$` --
   no `/`, no `..`, no other separators. A value that starts with `TASK-`
   but fails this pattern is never used as an id; instead it is reported as
   an `invalid_wdd_id` conflict, which blocks the whole sync (nothing is
   written) until the field is fixed on GitHub and the sync is rerun.
2. Independently of that check, every path this adapter is about to write
   (task brief, `plan.json`, the manifest) is re-resolved and verified to
   stay inside the expected directory before the write happens. This is a
   second, unconditional line of defense in case the id-format check above
   is ever bypassed or buggy.

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

**If `.wdd/state.json` does not exist at all** (e.g. immediately after
`pull --apply-local`, which never writes it), push does not know any task's
real status. It will not invent `todo` and push it -- doing so would
silently regress an already-in-progress remote item back to "Todo" the
first time push ran. Instead, for each affected task push:

- skips the status-changing `update_project_fields` operation entirely,
  leaving the remote `Status` exactly as it was, and
- reports a `status_skipped_no_controller_state` warning naming the task
  and reason (surfaced in both text and `--json` output).

Non-status operations remain safe to emit even with no controller state:
`create_remote_issue`/`add_issue_to_project` for a genuinely new local-only
task, and `update_project_fields` limited to `WDD ID`/`Risk` (no `Status`,
so the project's own default column applies). Run `wddctl start`/`wddctl
next` to establish controller state, then re-push to get accurate statuses.

## Adopting tasks created by push

`push` never mutates GitHub -- `create_remote_issue`, `add_issue_to_project`,
and `update_project_fields` are always a dry-run plan for a human (or the
GitHub CLI/connector) to apply. Once applied, the new issue number / project
item id has to be written back into the manifest, or the *next* sync will
not recognize the task as already-linked and will report an `id_collision`.

Close that loop with `record-link`:

```bash
python3 scripts/wdd_github_project_sync.py record-link --root . \
  --record-link TASK-001-example=123 \
  --record-link TASK-002-other=PVTI_abc123
```

- Repeatable; the value is a bare issue number (digits) or a GitHub
  Projects item id (anything else, typically `PVTI_...`).
- Each `TASK-ID` must already exist in `.wdd/plan.json`.
- This only ever edits `.wdd/adapters/github-project.json`: it merges the
  given link into the task's `github` entry and recomputes the task's
  `local` fingerprint (from the current plan entry + brief) so the very
  next sync does not misreport a spurious `task_conflict`. The `remote`
  fingerprint is left to be filled in naturally by the next real pull/push
  against a snapshot.

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

`invalid_wdd_id` is handled differently: there is no "decide which side
wins" step. Fix the offending `WDD ID`/`wdd_id` field on the GitHub item
itself so it matches `^TASK-[A-Za-z0-9][A-Za-z0-9._-]*$`, then rerun.

Do not delete manifest fingerprints to bypass conflict detection unless the
user explicitly chooses to re-baseline sync state.
