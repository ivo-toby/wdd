---
id: WDD-CONSTITUTION
kind: constitution
version: 1.0.0
status: draft
ratified: null
last_amended: YYYY-MM-DD
---

# Project Constitution

## Branching

- Target branch: `main`
- Base branch naming: `wdd/[scope-slug]` (a scope's `baseRef`)
- Task branch naming: `task/[TASK-ID]` (assigned by `wddctl start`)
- Merges into the base happen only through `wddctl merge`; nothing merges
  directly to the target branch except the scope's own integration.

## Verification

- Primary command: `<project-specific, e.g. "pytest -q">`
- Additional commands, if more than one check is required: `<command 2>`
- No meaningful automated check: record
  `verify record --status unavailable` with justification.

## Review policy

- Default `reviewPolicy`: `risk_based` (only `"risk": "high"` tasks get a
  separate reviewer; other allowed values: `always`, `none` — state which
  this repo uses if not the default).
- P1 and P2 findings block merge. P3 does not.
- High-risk categories here: `<e.g. auth, persistence, migrations, public
  APIs, generated code>`

## Model aliases

User-configured aliases only; do not hardcode provider model names unless
the user chose them explicitly.

```json
{"models": {"planning": "configured-model-key", "implementation": "configured-model-key", "review": "configured-model-key"}}
```

## Merge policy

- Merge mode: `<controller-merges-automatically | requires-human-approval>`
- `reconcileEveryNMerges` default: `3`
- `maxConcurrent` default for new scopes: `<e.g. 3>`

## Governance

- Amend before changing any of the above; re-ratify after any edit with
  `wddctl constitution amend --by NAME --decision-fingerprint SHA`
  (the initial ratification uses `ratify`; every later change uses `amend`,
  which records the fingerprint it superseded).
