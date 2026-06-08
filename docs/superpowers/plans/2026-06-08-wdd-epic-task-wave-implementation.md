# WDD Epic-Task-Wave Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update the WDD skill pack so it implements the approved epic → ticket → task → wave workflow as a portable text-only framework.

**Architecture:** Keep `.wdd/epics/...` as the durable artifact root. Tickets become containers, tasks become executable kanban files, waves activate batches of eligible tasks, and `orchestration.json` becomes the schema-versioned resume surface. Skills and templates are the runtime; scripts and CLI scaffolding are removed.

**Tech Stack:** Markdown skills, Markdown templates, hand-editable JSON templates, Git, static text inspection.

---

## File Structure

- Modify `README.md` to describe the new text-only workflow and skill list.
- Modify `docs/artifact-schema.md` to define epics, tickets, tasks, shared context, orchestration state, validation, and final PR artifacts.
- Modify `docs/spec-kit-mini-spec-findings.md` only where it implies helper scripts are part of the runtime model.
- Modify existing skills in `skills/*/SKILL.md` to make tasks the executable unit and preserve controller/worker/reviewer separation.
- Create `skills/wdd-plan-epic/SKILL.md` for integrated planning: shared context, tickets, tasks, waves, orchestration, and controller state.
- Create `skills/wdd-epic-validation/SKILL.md` for final branch validation after all waves.
- Create `skills/wdd-final-pr/SKILL.md` for final epic PR preparation.
- Modify legacy planning skills `skills/wdd-write-tickets/SKILL.md`, `skills/wdd-validate-tickets/SKILL.md`, and `skills/wdd-plan-waves/SKILL.md` into compatibility wrappers.
- Modify templates in `templates/*.md` to match the new artifact model.
- Create `templates/task.md`.
- Create `templates/shared-context-index.md`.
- Create `templates/shared-context-resource.md`.
- Create `templates/orchestration.json`.
- Create `templates/epic-validation.md`.
- Create `templates/final-pr.md`.
- Delete `templates/implementation-brief.md`.
- Delete `scripts/validate-skill-pack.mjs`.
- Delete `package.json`.

## Task 1: Documentation Contract

**Files:**
- Modify: `README.md`
- Modify: `docs/artifact-schema.md`
- Modify: `docs/spec-kit-mini-spec-findings.md`

- [ ] **Step 1: Read current docs**

Run:

```bash
rtk sed -n '1,260p' README.md
rtk sed -n '1,320p' docs/artifact-schema.md
rtk sed -n '1,240p' docs/spec-kit-mini-spec-findings.md
```

Expected: docs still describe ticket execution, implementation briefs, and dev-only script validation.

- [ ] **Step 2: Update README**

Replace the README with a concise text-only overview covering:

- WDD is a portable Markdown skill pack.
- `.wdd/epics/...` is the durable source of truth.
- Tickets contain task kanban folders.
- Waves activate concurrently eligible tasks.
- Controller merges or marks task PRs merge-ready.
- No CLI, scripts, Node.js, or npm are required to operate the framework.

- [ ] **Step 3: Update artifact schema**

Replace `docs/artifact-schema.md` with the new schema sections:

- Constitution
- Epic
- Ticket Folder
- Task
- Shared Context
- Wave Plan
- Orchestration JSON with `schemaVersion: 1`
- Controller State
- Epic Validation
- Final PR

- [ ] **Step 4: Update findings note**

Adjust `docs/spec-kit-mini-spec-findings.md` so helper scripts are not described as part of the WDD runtime. The final direction must say the workflow is Markdown/JSON artifact driven and text-only.

- [ ] **Step 5: Verify docs**

Run:

```bash
rtk rg -n "implementation brief|briefs/|runtime CLI|npm test|validate-skill-pack|helper script" README.md docs
```

Expected: no stale runtime claims. References may remain only when explicitly saying the old model was retired.

## Task 2: Templates

**Files:**
- Modify: `templates/constitution.md`
- Modify: `templates/epic.md`
- Modify: `templates/ticket.md`
- Modify: `templates/wave-plan.md`
- Modify: `templates/controller-state.md`
- Modify: `templates/validation-checklist.md`
- Create: `templates/task.md`
- Create: `templates/shared-context-index.md`
- Create: `templates/shared-context-resource.md`
- Create: `templates/orchestration.json`
- Create: `templates/epic-validation.md`
- Create: `templates/final-pr.md`
- Delete: `templates/implementation-brief.md`

- [ ] **Step 1: Inspect current templates**

Run:

```bash
rtk rg -n "ticket|task|brief|wave|orchestration|shared-context|branch|review" templates
```

Expected: old templates still center on tickets and implementation briefs.

- [ ] **Step 2: Update existing templates**

Rewrite the existing templates so:

- `constitution.md` asks for model aliases, storage mode, branch rules, review policy, and merge policy.
- `epic.md` has implementation-ready epic sections and branch metadata.
- `ticket.md` is a ticket container, not an executable task.
- `wave-plan.md` lists tasks by wave and states wave activation rules.
- `controller-state.md` tracks active task gates independently.
- `validation-checklist.md` validates epic, ticket, task, shared-context, orchestration, branch freshness, and text-only rules.

- [ ] **Step 3: Add new task and context templates**

Create:

- `templates/task.md` with the required task file sections from the design.
- `templates/shared-context-index.md` with overview, resource index, decisions, warnings, constraints, and recent durable memory.
- `templates/shared-context-resource.md` with focused resource and durable memory item structure.

- [ ] **Step 4: Add orchestration and closure templates**

Create:

- `templates/orchestration.json` with `schemaVersion: 1`, epic config, model config, waves, tasks, gates, branch freshness, and verification fields.
- `templates/epic-validation.md` with final validation checklist and report sections.
- `templates/final-pr.md` with the final epic PR description structure.

- [ ] **Step 5: Remove implementation brief template**

Delete `templates/implementation-brief.md`. Task files are the canonical implementation briefs.

- [ ] **Step 6: Verify templates**

Run:

```bash
rtk rg -n "implementation_brief|briefs/|Copy the ticket|command proving" templates
rtk rg -n "schemaVersion|shared-context|TASK-|epic/\\[epic-slug\\]|merge_ready|branch freshness" templates
```

Expected: first command has no stale template language; second command finds the new contract terms.

## Task 3: Core Skills

**Files:**
- Modify: `skills/wave-driven-development/SKILL.md`
- Modify: `skills/wdd-init-project/SKILL.md`
- Modify: `skills/wdd-constitution/SKILL.md`
- Modify: `skills/wdd-start-epic/SKILL.md`
- Modify: `skills/wdd-start-wave/SKILL.md`
- Modify: `skills/subagent-pr-orchestration/SKILL.md`
- Modify: `skills/wdd-reconcile-wave/SKILL.md`
- Modify: `skills/wdd-status/SKILL.md`

- [ ] **Step 1: Inspect current skill terminology**

Run:

```bash
rtk rg -n "ticket|task|brief|orchestration|main|merge|review|wave|CLI|script" skills
```

Expected: old skills describe ticket-level execution and implementation briefs.

- [ ] **Step 2: Update overview skill**

Rewrite `skills/wave-driven-development/SKILL.md` to route through:

```text
wdd-init-project
wdd-constitution
wdd-start-epic
wdd-plan-epic
wdd-start-wave
subagent-pr-orchestration
wdd-reconcile-wave
wdd-epic-validation
wdd-final-pr
wdd-status
```

It must state that tasks are executable, waves activate concurrent eligible tasks, and scripts/CLI are not required.

- [ ] **Step 3: Update setup and epic definition skills**

Update `wdd-init-project`, `wdd-constitution`, and `wdd-start-epic` so they create the new artifact layout, ask for required setup preferences, and produce implementation-ready `epic.md` plus `shared-context/` scaffolding.

- [ ] **Step 4: Update wave start and orchestration skills**

Update `wdd-start-wave` and `subagent-pr-orchestration` so active waves dispatch all eligible tasks concurrently, track task gates independently, enforce stale-branch freshness before merge, and treat task files as the implementation briefs.

- [ ] **Step 5: Update reconciliation and status skills**

Update `wdd-reconcile-wave` and `wdd-status` so they read task kanban folders, `orchestration.json`, shared context, task PR state, branch freshness, and final validation state.

- [ ] **Step 6: Verify core skills**

Run:

```bash
rtk rg -n "activates the next pending wave|schemaVersion|stale|branch freshness|shared-context|Task files are the implementation briefs|worker agents may run at the same time" skills
rtk rg -n "briefs/|implementation_brief|runtime CLI|npm|validate-skill-pack|merge directly to main" skills
```

Expected: first command finds new requirements; second command has no stale runtime or old brief assumptions except where legacy wrappers explain the old phases are retired.

## Task 4: Planning And Closure Skills

**Files:**
- Create: `skills/wdd-plan-epic/SKILL.md`
- Create: `skills/wdd-epic-validation/SKILL.md`
- Create: `skills/wdd-final-pr/SKILL.md`
- Modify: `skills/wdd-write-tickets/SKILL.md`
- Modify: `skills/wdd-validate-tickets/SKILL.md`
- Modify: `skills/wdd-plan-waves/SKILL.md`

- [ ] **Step 1: Create `wdd-plan-epic`**

Write a full skill with frontmatter and required sections:

- `## User Input`
- `## Preconditions`
- `## Workflow`
- `## Done When`

It must create shared context, ticket folders, task files, waves, `orchestration.json`, and `controller-state.md`.

- [ ] **Step 2: Create `wdd-epic-validation`**

Write a full skill that validates the entire epic branch after all waves complete. It must read epic, tickets, tasks, wave plan, orchestration state, controller state, shared context, PRs or patches, verification evidence, and relevant code.

- [ ] **Step 3: Create `wdd-final-pr`**

Write a full skill that prepares the final PR from the epic branch to the target branch only after epic validation passes. It must build a comprehensive PR description for human review.

- [ ] **Step 4: Convert legacy planning skills to wrappers**

Replace `wdd-write-tickets`, `wdd-validate-tickets`, and `wdd-plan-waves` with compatibility wrappers that say these phases are folded into `wdd-plan-epic`. Each wrapper must tell the agent what old user intent means and how to route it.

- [ ] **Step 5: Verify planning skills**

Run:

```bash
rtk rg -n "wdd-plan-epic|wdd-epic-validation|wdd-final-pr|compatibility|folded into" skills
rtk rg -n "ticket files under `tickets/`|validation-checklist.*next phase|Every ticket has a wave assignment" skills
```

Expected: new skills exist and legacy standalone planning language is gone.

## Task 5: Remove Script And Package Scaffolding

**Files:**
- Delete: `scripts/validate-skill-pack.mjs`
- Delete: `package.json`

- [ ] **Step 1: Confirm script/package are runtime-conflicting**

Run:

```bash
rtk rg -n "validate-skill-pack|npm|node scripts|package.json" .
```

Expected: references identify script/package scaffolding and docs that must be removed or rewritten.

- [ ] **Step 2: Delete script and package files**

Remove `scripts/validate-skill-pack.mjs` and `package.json`.

- [ ] **Step 3: Verify no framework dependency remains**

Run:

```bash
rtk rg -n "validate-skill-pack|npm test|node scripts|package.json|runtime CLI" .
```

Expected: no active framework dependency references remain. Historical references are acceptable only if they clearly describe a retired direction.

## Task 6: Static Verification

**Files:**
- Inspect: `README.md`
- Inspect: `docs/*.md`
- Inspect: `docs/superpowers/specs/*.md`
- Inspect: `skills/*/SKILL.md`
- Inspect: `templates/*`

- [ ] **Step 1: Check required skill files**

Run:

```bash
rtk proxy test -f skills/wdd-plan-epic/SKILL.md
rtk proxy test -f skills/wdd-epic-validation/SKILL.md
rtk proxy test -f skills/wdd-final-pr/SKILL.md
```

Expected: all commands exit successfully.

- [ ] **Step 2: Check required template files**

Run:

```bash
rtk proxy test -f templates/task.md
rtk proxy test -f templates/shared-context-index.md
rtk proxy test -f templates/shared-context-resource.md
rtk proxy test -f templates/orchestration.json
rtk proxy test -f templates/epic-validation.md
rtk proxy test -f templates/final-pr.md
```

Expected: all commands exit successfully.

- [ ] **Step 3: Check old artifacts are gone**

Run:

```bash
rtk proxy test ! -f templates/implementation-brief.md
rtk proxy test ! -f scripts/validate-skill-pack.mjs
rtk proxy test ! -f package.json
```

Expected: all commands exit successfully.

- [ ] **Step 4: Scan for required contract language**

Run:

```bash
rtk rg -n "schemaVersion|activates the next pending wave|worker agents may run at the same time|branch freshness|stale|shared-context|epic branch|task branch|P1|P2|P3" README.md docs skills templates
```

Expected: required concepts appear across docs, skills, and templates.

- [ ] **Step 5: Scan for stale requirements**

Run:

```bash
rtk rg -n "implementation_brief|briefs/|npm test|node scripts|validate-skill-pack|runtime CLI|ticket files under `tickets/`" README.md docs skills templates
```

Expected: no stale active requirements. Any historical mention must be clearly marked retired.

- [ ] **Step 6: Whitespace check**

Run:

```bash
rtk git diff --check
```

Expected: no output and exit 0.

## Task 7: Commit

**Files:**
- Stage all intended implementation changes.
- Do not stage `wave-driven-development-codex-spec.md` unless explicitly requested.

- [ ] **Step 1: Review status**

Run:

```bash
rtk git status --short
```

Expected: intended modified/new/deleted files are visible; source brief remains untracked.

- [ ] **Step 2: Review diff summary**

Run:

```bash
rtk git diff --stat
```

Expected: docs, templates, skills, and script/package removal are included.

- [ ] **Step 3: Commit**

Run:

```bash
rtk git add README.md docs skills templates scripts/validate-skill-pack.mjs package.json
rtk git commit -m "feat: pivot WDD to task wave text workflow"
```

Expected: commit succeeds. If `scripts/validate-skill-pack.mjs` or `package.json` are already deleted, Git stages the deletions.
