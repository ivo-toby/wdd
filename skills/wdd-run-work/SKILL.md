---
name: wdd-run-work
description: Run or resume a WDD micro-wave by dispatching compact task workers from .wdd/work state, tracking gates, applying adaptive monitoring, and preparing tasks for wdd-finish-work.
---

# WDD Run Work

Coordinate active micro-wave tasks without full epic ceremony.

## Preconditions

- `.wdd/work/<work-id>/state.json` exists with `schemaVersion: 1`.
- Each worker receives exactly one micro task file.
- The controller does not implement task code.
- Repository-writing workers use isolated task worktrees when available.
- Workers do not merge their own PRs or patches.

## Workflow

1. Load current micro-wave state:
   - `brief.md`.
   - `state.json`.
   - Active task files.
   - PR, patch, worker, or verification references when present.
   - Strategy, including execution mode and bundle groups.

2. Dispatch eligible tasks:
   - Create or verify the work base branch before worker dispatch.
   - For `bundled`, create or verify one bundle branch and isolated worktree for
     all micro tasks.
   - For `hybrid`, create or verify one branch and isolated worktree per bundle
     group.
   - For `parallel`, create or verify one task branch and isolated worktree per
     repository-writing task.
   - Tell each worker the exact worktree path, branch, and task files.
   - Require named-file inspection before broad discovery.
   - Require verification evidence and final status token:
     `DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, or `BLOCKED`.

3. Apply risk-based review:
   - Require separate review for high-risk code, public APIs, persistence,
     auth, security, migrations, generated code, or broad shared contracts.
   - Allow controller checklist review for low-risk docs, tests, isolated
     cleanup, or narrowly scoped changes when the constitution allows it.
   - P1 and P2 findings block finish unless explicitly accepted by the user.

4. Apply adaptive monitoring:
   - Use slower cadence, usually 15-30 minutes, while workers have no PR or
     patch.
   - Use faster cadence, usually 5 minutes, during review, fixes, or merge-ready
     gates.
   - After repeated no-change ticks, downgrade to manual fallback when safe.
   - Do not record `codex_thread_heartbeat` unless the scheduler reference was
     verified in the current tick.

5. Update `state.json` and task files after meaningful events:
   - Worker started.
   - PR or patch produced.
   - Review required, started, passed, or blocked.
   - Verification passed, failed, unavailable, or documented as non-blocking.
   - Task merged, merge-ready, blocked, cancelled, or ready to finish.
   - Bundle gate changed when using `bundled` or `hybrid`.

## Done When

- Every active task has current state.
- Every active bundle has current state when execution mode is `bundled` or
  `hybrid`.
- Required feedback is routed.
- Verification evidence is recorded or blockers are explicit.
- Tasks are merged, merge-ready, blocked, cancelled, or ready for
  `wdd-finish-work`.
