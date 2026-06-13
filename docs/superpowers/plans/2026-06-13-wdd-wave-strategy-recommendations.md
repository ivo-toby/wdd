# WDD Wave Strategy Recommendations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-wave strategy recommendations so planning proposes profile, execution mode, review mode, and monitoring mode before workers run.

**Architecture:** Keep epic and micro-wave profiles as defaults. Add a wave-level `strategy` object as the execution contract. `wdd-plan-epic` recommends strategy, `wdd-start-wave` verifies confirmation and dispatches according to strategy, and `subagent-pr-orchestration` handles bundled, hybrid, and parallel gates.

**Tech Stack:** Markdown skills, hand-editable JSON templates, static shell verification.

---

### Task 1: Add Failing Static Checks

- [x] Check `skills/wdd-plan-epic/templates/orchestration.json` for `"strategy"`.
- [x] Check `docs/artifact-schema.md` for `Interactive Wave Planning`.
- [x] Check `skills/wdd-start-wave/SKILL.md` for `requiresUserConfirmation`.
- [x] Check `skills/subagent-pr-orchestration/SKILL.md` for `bundled`, `hybrid`, and `parallel`.

### Task 2: Extend Artifacts

- [x] Add wave `strategy` to epic orchestration templates.
- [x] Add micro-wave `strategy` to work state templates.
- [x] Add strategy rows to controller-state templates.
- [x] Update validation checklist templates.

### Task 3: Update Skill Behavior

- [x] Teach `wdd-plan-epic` to recommend strategy per wave.
- [x] Teach `wdd-plan-work` to recommend execution strategy for micro-waves.
- [x] Teach `wdd-start-wave` to verify confirmation and dispatch bundled, hybrid, or parallel.
- [x] Teach `wdd-run-work` to execute micro-wave strategy.
- [x] Teach `subagent-pr-orchestration` bundled/hybrid gate tracking.
- [x] Surface strategy in `wdd-status` and `wdd-info`.

### Task 4: Document

- [x] Add README guidance for the interactive wave-planning checkpoint.
- [x] Add schema docs for `strategy`, `executionMode`, `bundleGroups`, and override history.

### Task 5: Verify

- [x] Re-run static feature checks.
- [x] Parse all JSON templates with `jq`.
- [x] Check every skill has `name:` and `description:`.
- [x] Run `git diff --check`.
