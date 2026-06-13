---
name: wdd-finish-work
description: Finish a WDD micro-wave by reconciling task evidence, review state, branch freshness, verification, and final handoff for a .wdd/work packet without epic validation ceremony.
---

# WDD Finish Work

Close a micro-wave work packet.

## Preconditions

- `.wdd/work/<work-id>/brief.md` exists.
- `.wdd/work/<work-id>/state.json` exists.
- All tasks are merged, merge-ready, blocked, cancelled, or explicitly closed.
- Required verification and review evidence is recorded or explicitly
  documented as non-blocking.

## Workflow

1. Load:
   - Work brief.
   - `state.json`.
   - Task files.
   - PRs, patches, commits, review notes, and verification evidence when
     available.

2. Verify finish gates:
   - Scope completed or intentionally reduced.
   - No task silently skipped.
   - No unresolved P1/P2 feedback unless accepted by the user.
   - Branch freshness checked before merge or merge-ready.
   - Verification evidence is present or non-blocking failures are documented.

3. Update artifacts:
   - Mark completed tasks `done`.
   - Mark blocked or cancelled tasks accurately.
   - Set work brief status to `done`, `blocked`, or `cancelled`.
   - Set monitoring inactive, stopped, blocked, or manual with a durable reason.
   - Add concise finish notes with PR or patch references and test evidence.

4. Handoff:
   - If work merged into the target branch or accepted branch, summarize result.
   - If repository policy requires a human merge, mark `merge_ready`.
   - If the micro-wave became broader than expected, recommend upgrading the
     remaining work into a `lite`, `standard`, or `full` epic.

## Done When

- `brief.md`, `state.json`, and task files reflect final state.
- Verification and review evidence are summarized.
- Monitoring is no longer active unless explicitly blocked with a next action.
- The final response names the result and any follow-up.
