# WDD Orchestrator Monitoring Design

## Context

WDD active waves dispatch worker and reviewer agents, then the controller must
keep monitoring their output until each task is merged, blocked, cancelled, or
ready for wave reconciliation.

The previous workflow described a heartbeat loop but did not define how the
heartbeat is scheduled or what happens when a coding agent cannot schedule
recurring work.

## Research Summary

Codex supports automations that can run skills on a schedule. Its thread
automations are heartbeat-style wakeups attached to the current thread, support
minute-based intervals, and are explicitly useful for polling connected sources,
continuing review loops, and running skill-driven workflows. Codex also supports
standalone automations for independent scheduled runs. Source:
https://developers.openai.com/codex/app/automations

Claude Code supports `/loop` and scheduled tasks for session-scoped polling. A
loop can run a prompt or command repeatedly, choose a dynamic interval, use a
project `.claude/loop.md`, and stop when the task is complete. Its limitations
matter for WDD: session-scoped scheduled tasks require Claude Code to be running
and idle, expire after seven days, and missed fires do not catch up. Source:
https://code.claude.com/docs/en/scheduled-tasks

Claude Code agent view can dispatch and monitor multiple background sessions,
and background sessions keep running without a terminal attached. This is useful
for watching independent Claude worker sessions, but it is still a Claude
adapter rather than a portable WDD requirement. Source:
https://code.claude.com/docs/en/agent-view

Claude Code subagents can run in foreground or background, but scheduling tools
are not generally available inside subagents. Source:
https://code.claude.com/docs/en/sub-agents

## Decision

Model monitoring as a portable WDD artifact contract plus optional scheduler
adapters.

The controller heartbeat is an idempotent tick:

1. Read `orchestration.json`, `controller-state.md`, active task files, and PR
   or patch state.
2. Inspect each worker and reviewer reference.
3. Advance gates for `no_pr`, `needs_review`, `reviewing`, `needs_fixes`,
   `merge_ready`, `merged`, `blocked`, or `cancelled`.
4. Update artifacts after every meaningful event.
5. Reschedule, stop, or downgrade to manual fallback.
6. Stop when the active wave is ready for `wdd-reconcile-wave`.

Scheduler selection order:

1. `codex_thread_heartbeat` when Codex thread automation is available and the
   same conversation should continue.
2. `claude_loop` when Claude Code `/loop` or scheduled tasks are available in
   the active local session.
3. `external_scheduler` when a project adapter, desktop scheduled task, cloud
   routine, GitHub Actions schedule, or equivalent runner is available.
4. `manual` when no scheduler exists.

Manual fallback is mandatory. It records the exact prompt and next due time
needed for a human or fresh agent to run the next tick. This keeps WDD portable
when the agent runtime has no scheduler, when a scheduler tool is unavailable,
or when scheduled monitoring fails.

## Artifact Contract

`orchestration.json` contains:

```json
{
  "monitoring": {
    "mode": "manual",
    "cadence": "5m",
    "status": "inactive",
    "lastCheckedAt": null,
    "nextCheckDueAt": null,
    "schedulerRef": null,
    "fallbackPrompt": "Run subagent-pr-orchestration for EPIC-example-feature WAVE-001. Read orchestration.json and controller-state.md, inspect every active worker and reviewer reference, update task gates, and stop when all active tasks are merged, blocked, cancelled, or ready for wdd-reconcile-wave."
  }
}
```

`controller-state.md` contains a human-readable `## Monitoring` section with
the same mode, cadence, status, scheduler reference, fallback prompt, and stop
condition.

## Consequences

- WDD can use Codex automations when available without making Codex mandatory.
- Claude Code users can use `/loop` naturally without making Claude mandatory.
- Agents without scheduling support remain usable because the next monitor tick
  is recorded as a durable prompt.
- Monitoring state is resumable after context compression or a fresh controller
  session because it lives in `.wdd`.
- The controller must keep ticks bounded. A tick should make progress and
  reschedule or stop; it should not become an unbounded implementation session.
