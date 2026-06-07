---
name: wdd-reconcile-wave
description: Reconcile a completed WDD wave after PRs merge by comparing planned and actual architecture, updating later tickets, and marking the wave done.
---

# WDD Reconcile Wave

Use this after all active wave tickets have passed review and merged.

## Workflow

1. Inspect merged PRs, changed files, verification evidence, and review findings.
2. Compare actual architecture against `epic.md`, `design.md`, and `wave-plan.yaml`.
3. Update later tickets for drift, new dependencies, or new conflict domains.
4. Update `controller-state.yaml` with merge and cleanup state.
5. Run `wdd reconcile <epic> --wave <n> --done`.
6. Report whether the next wave is ready.

## Rules

- Do not mark a wave done before merge/review/verification gates are complete.
- Record drift in ticket updates, decision notes, or the epic body.
- Start the next wave only after reconciliation.

