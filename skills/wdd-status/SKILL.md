---
name: wdd-status
description: Report current Wave-Driven Development state read-only via wddctl status, next, and render. Use when the user asks for progress, current state, active tasks, blockers, or what happens next in a WDD scope.
---

# WDD Status

You run every `wddctl` command in this skill yourself. Presenting a command
to the user instead of executing it is a protocol violation.

Read-only reporting. Never modify `.wdd/` artifacts or task code here.

## Workflow

1. If `.wdd/` is missing, say WDD isn't initialized and point at
   `wave-driven-development` to get started.
2. If `.wdd/constitution.md` exists but isn't ratified, report that and
   point at `wdd-setup` — execution is blocked until then.
3. Otherwise, run:
   - `wddctl status --json` for a concise summary of the scope and its
     tasks.
   - `wddctl next` for the current action queue — what's blocking, what
     needs dispatching, what's ready to merge.
   - `wddctl render --output .wdd/state.md` if a durable markdown snapshot
     is useful to the user.
4. Report each task's gate — `not_started`, `no_pr`, `reviewing`,
   `needs_review`, `needs_verification`, `needs_fixes`, `needs_freshness`,
   `ready_to_merge`, `merge_ready`, or a terminal `done`/`blocked`/
   `cancelled` — plus open P1/P2 findings and any reconciliation due.
   `wddctl next` reports the matching *action* for each gate; don't confuse
   the two vocabularies when summarizing.
5. Name the next concrete action and which skill handles it: `wdd-run` for
   controller work, `wdd-plan` if the plan needs reshaping.

Never hand-repair `state.json` or task file paths — if something looks
wrong, that's a bug to report, not to patch.
