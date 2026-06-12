# GitHub Projects Adapter Reference

Read this before live GitHub sync work, nonstandard field mappings, larger-board
imports, or conflict resolution.

## Model

WDD remains local-first. GitHub Projects are planning mirrors.

Recommended MVP mapping:

```text
one GitHub Project = one WDD epic
GitHub Project item issue = WDD ticket or task
GitHub custom fields = WDD metadata
```

Larger-board mapping is possible by first exporting or filtering only the items
for one epic. Use `--epic-id` to force the local epic ID for that filtered
subset.

## Preferred GitHub Project Fields

Use these fields when available:

| Field | Purpose |
|-------|---------|
| `WDD ID` | `EPIC-*`, `TICKET-*`, or `TASK-*` stable ID |
| `WDD Kind` | `epic`, `ticket`, or `task` |
| `Ticket` | Parent ticket ID for tasks |
| `Wave` | Planned WDD wave for tasks |
| `Status` | Project status mapped to WDD status |

The sync script also infers kind from IDs, title prefixes, or parent ticket
metadata when fields are missing.

## Status Mapping

Remote to local:

| GitHub status | WDD status |
|---------------|------------|
| `Backlog`, `Todo`, `To Do`, `Ready` | `todo` |
| `In Progress`, `Doing` | `in-progress` |
| `Review`, `In Review` | `review` |
| `Blocked` | `blocked` |
| `Cancelled`, `Canceled` | `cancelled` |
| `Done`, `Closed` | `review` for tasks by default |

Use `--trust-remote-done` only when the user explicitly wants GitHub `Done` to
be imported as WDD `done`. Without that flag, remote done tasks import as
`review` so local verification evidence can be recorded before completion.

Local to remote:

| WDD status | GitHub status |
|------------|---------------|
| `todo`, `planned` | `Todo` |
| `in-progress` | `In Progress` |
| `review` | `Review` |
| `done` | `Done` |
| `blocked` | `Blocked` |
| `cancelled` | `Cancelled` |

## Manifest

Path:

```text
.wdd/epics/<EPIC-ID>/adapters/github-project.json
```

Shape:

```json
{
  "schemaVersion": 1,
  "updatedAt": "2026-06-12T10:00:00Z",
  "epic": {
    "id": "EPIC-example"
  },
  "project": {
    "owner": "OWNER",
    "number": 4,
    "id": "PVT_...",
    "title": "Example",
    "url": "https://github.com/orgs/OWNER/projects/4",
    "repo": "OWNER/REPO",
    "wdd_id": "EPIC-example"
  },
  "items": {
    "TICKET-001-example": {
      "kind": "ticket",
      "localPath": "TICKET-001-example/ticket.md",
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

Fingerprints let the adapter detect three cases:

- Local changed, remote unchanged: plan a GitHub update.
- Remote changed, local unchanged: plan a local update.
- Both changed: report a conflict and do not write either side.

## Snapshot JSON

For offline runs or larger-board filtering, pass `--remote-json snapshot.json`.
The script accepts this compact shape:

```json
{
  "project": {
    "owner": "OWNER",
    "number": 4,
    "id": "PVT_...",
    "title": "Example Epic",
    "url": "https://github.com/orgs/OWNER/projects/4",
    "repo": "OWNER/REPO",
    "wdd_id": "EPIC-example"
  },
  "items": [
    {
      "kind": "ticket",
      "wdd_id": "TICKET-001-api",
      "item_id": "PVTI_...",
      "issue_number": 123,
      "url": "https://github.com/OWNER/REPO/issues/123",
      "title": "API",
      "body": "Ticket body",
      "status": "Todo"
    },
    {
      "kind": "task",
      "wdd_id": "TASK-001-contract",
      "ticket": "TICKET-001-api",
      "wave": "WAVE-001",
      "item_id": "PVTI_...",
      "issue_number": 124,
      "url": "https://github.com/OWNER/REPO/issues/124",
      "title": "Contract",
      "body": "Task body",
      "status": "In Progress"
    }
  ]
}
```

## Live GitHub Access

The script uses GitHub CLI when no `--remote-json` is provided:

```bash
gh project view <number> --owner <owner> --format json
gh project item-list <number> --owner <owner> --format json --limit 1000
```

For remote writes, inspect the generated operation plan and then use the
GitHub CLI or connector deliberately. GitHub Projects usually require separate
steps to create an issue, add it to the Project, and update Project fields.

## Conflict Handling

When conflicts are reported:

1. Open the local file named by `local_path`.
2. Open the GitHub issue URL.
3. Decide which fields win, or manually merge both.
4. Update one side.
5. Rerun dry-run sync.
6. Apply local writes only after the conflict list is empty.

Do not delete manifest fingerprints to bypass conflict detection unless the
user explicitly chooses to re-baseline sync state.
