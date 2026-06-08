# WDD Orchestrator Monitoring Fallbacks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add a portable monitoring contract so WDD controllers can keep watching worker and reviewer agents through Codex heartbeats, Claude `/loop`, external schedulers, or manual fallback.

**Architecture:** Treat monitoring as an idempotent controller tick recorded in `.wdd` artifacts. `orchestration.json` stores machine-readable scheduler mode and prompt data; `controller-state.md` stores the human-readable monitor status and resume command. Skills choose the best available scheduler but always leave a manual fallback.

**Tech Stack:** Markdown skills, Markdown templates, hand-editable JSON templates, static verification with `rg`, `jq`, and `git diff --check`.

---

### Task 1: Record The Monitoring Contract

**Files:**
- Modify: `docs/artifact-schema.md`
- Modify: `skills/wdd-init-project/templates/orchestration.json`
- Modify: `skills/wdd-plan-epic/templates/orchestration.json`
- Modify: `skills/wdd-init-project/templates/controller-state.md`
- Modify: `skills/wdd-plan-epic/templates/controller-state.md`
- Modify: `skills/wdd-start-wave/templates/controller-state.md`
- Modify: `skills/wdd-reconcile-wave/templates/controller-state.md`
- Modify: `skills/wdd-init-project/templates/validation-checklist.md`
- Modify: `skills/wdd-plan-epic/templates/validation-checklist.md`

- [x] **Step 1: Add `monitoring` to orchestration JSON templates**

Add a top-level object after `sharedContext`:

```json
"monitoring": {
  "mode": "manual",
  "cadence": "5m",
  "status": "inactive",
  "lastCheckedAt": null,
  "nextCheckDueAt": null,
  "schedulerRef": null,
  "fallbackPrompt": "Run subagent-pr-orchestration for EPIC-example-feature WAVE-001. Read orchestration.json and controller-state.md, inspect every active worker and reviewer reference, update task gates, and stop when all active tasks are merged, blocked, cancelled, or ready for wdd-reconcile-wave."
}
```

- [x] **Step 2: Add `Monitoring` to controller-state templates**

Insert a `## Monitoring` section after `## Active Wave` with scheduler mode, cadence, status, last check, next check, scheduler reference, fallback prompt, and stop condition.

- [x] **Step 3: Update artifact schema**

Document the same `monitoring` object, allowed modes (`codex_thread_heartbeat`, `claude_loop`, `external_scheduler`, `manual`), required controller-state section, and the rule that every monitor tick must be idempotent.

- [x] **Step 4: Update validation checklist templates**

Add checklist items requiring monitoring mode, fallback prompt, stop condition, and next check recording.

### Task 2: Teach Skills To Select And Run Monitoring

**Files:**
- Modify: `skills/wdd-start-wave/SKILL.md`
- Modify: `skills/subagent-pr-orchestration/SKILL.md`
- Modify: `skills/wdd-reconcile-wave/SKILL.md`
- Modify: `skills/wdd-status/SKILL.md`
- Modify: `skills/wave-driven-development/SKILL.md`

- [x] **Step 1: Update `wdd-start-wave`**

After dispatch, require the controller to select the best available monitor in this order: Codex thread heartbeat, Claude `/loop`, external scheduler, manual fallback. Record the selected mode and fallback prompt in both artifacts.

- [x] **Step 2: Update `subagent-pr-orchestration`**

Define one heartbeat tick as bounded/idempotent: load state, poll workers and reviewers, advance gates, update artifacts, decide next cadence, stop when wave reconciliation is ready, and never depend on hidden conversation state.

- [x] **Step 3: Update `wdd-reconcile-wave`**

Require monitors to be stopped, expired, or marked inactive before wave reconciliation is complete.

- [x] **Step 4: Update status/overview skills**

Surface monitoring mode, next check, stale monitors, and manual resume prompt in `wdd-status`; mention monitoring as part of active-wave tracking in `wave-driven-development`.

### Task 3: Update User-Facing Docs

**Files:**
- Modify: `README.md`
- Create: `docs/superpowers/specs/2026-06-08-wdd-orchestrator-monitoring-design.md`

- [x] **Step 1: Add README monitoring summary**

Explain the scheduler ladder and why manual fallback is mandatory.

- [x] **Step 2: Add design note with sources**

Capture the approved recommendation and cite current Codex automations, Claude scheduled tasks, and Claude agent view/subagent documentation.

### Task 4: Verify

**Files:**
- No file edits.

- [x] **Step 1: Parse JSON templates**

Run:

```bash
rtk jq empty skills/wdd-init-project/templates/orchestration.json skills/wdd-plan-epic/templates/orchestration.json
```

Expected: exit 0.

- [x] **Step 2: Check monitoring terms across active artifacts**

Run:

```bash
rtk rg -n "codex_thread_heartbeat|claude_loop|external_scheduler|fallbackPrompt|## Monitoring|monitoring" README.md docs/artifact-schema.md skills
```

Expected: finds README, schema, templates, and orchestration skills.

- [x] **Step 3: Check whitespace**

Run:

```bash
rtk git diff --check
```

Expected: exit 0.
