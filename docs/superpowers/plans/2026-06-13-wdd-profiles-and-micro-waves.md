# WDD Profiles And Micro-Waves Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add profile-aware WDD guidance, a read-only `wdd-info` routing skill, and a micro-wave workflow for single chunky tickets that need limited parallelism.

**Architecture:** Keep existing epic skills canonical and make them profile-aware instead of multiplying lite/full variants. Add micro-wave as a smaller artifact family under `.wdd/work/` with compact state and task briefs. Use README and `wdd-info` as the front door so users can choose no WDD, micro, lite, standard, or full quickly.

**Tech Stack:** Markdown skill pack, local text artifacts, hand-editable JSON, static shell verification.

---

### Task 1: Add Failing Static Checks

**Files:**
- Test by command only: no repo test harness exists.

- [ ] **Step 1: Verify profile guidance is currently missing**

Run: `grep -q "Which WDD Mode Should I Use" README.md`

Expected: command exits non-zero before README changes.

- [ ] **Step 2: Verify `wdd-info` skill is currently missing**

Run: `test -f skills/wdd-info/SKILL.md`

Expected: command exits non-zero before the skill is created.

- [ ] **Step 3: Verify micro-wave schema is currently missing**

Run: `grep -q ".wdd/work/" docs/artifact-schema.md`

Expected: command exits non-zero before schema changes.

### Task 2: Document Profiles And Micro-Waves

**Files:**
- Modify: `README.md`
- Modify: `docs/artifact-schema.md`

- [ ] **Step 1: Add README mode chooser**

Add a concise section near the top that explains when to skip WDD, when to use `micro`, `lite`, `standard`, and `full`, plus copy-paste prompts.

- [ ] **Step 2: Add schema guidance**

Document constitution defaults, epic-level profile overrides, and `.wdd/work/<work-id>/` micro-wave artifacts.

- [ ] **Step 3: Keep existing epic flow intact**

Do not remove the current epic workflow. Make profile guidance additive.

### Task 3: Add `wdd-info`

**Files:**
- Create: `skills/wdd-info/SKILL.md`

- [ ] **Step 1: Create frontmatter**

Use `name: wdd-info` and a description that triggers for WDD orientation, mode choice, ceremony/cost questions, resumes, and “should I use WDD?” requests.

- [ ] **Step 2: Add read-only routing workflow**

Tell agents to inspect the request and local `.wdd/` state, recommend no WDD, `micro`, `lite`, `standard`, `full`, or resume, and provide the exact next prompt.

- [ ] **Step 3: Add mode table and artifact expectations**

Keep the skill concise; it should be a front door, not a second README.

### Task 4: Add Micro-Wave Skills And Templates

**Files:**
- Create: `skills/wdd-start-work/SKILL.md`
- Create: `skills/wdd-plan-work/SKILL.md`
- Create: `skills/wdd-run-work/SKILL.md`
- Create: `skills/wdd-finish-work/SKILL.md`
- Create templates under those skill folders as needed.

- [ ] **Step 1: Add `wdd-start-work`**

Create a compact work brief under `.wdd/work/<work-id>/brief.md` for a single chunky ticket.

- [ ] **Step 2: Add `wdd-plan-work`**

Split the brief into 2-5 compact task files only when parallelism helps, plus `state.json`.

- [ ] **Step 3: Add `wdd-run-work`**

Dispatch or resume micro-wave tasks with branch/worktree isolation and adaptive monitoring.

- [ ] **Step 4: Add `wdd-finish-work`**

Reconcile tasks, verification, review, and final handoff without epic validation ceremony.

### Task 5: Update Existing Skill Routing

**Files:**
- Modify: `skills/wave-driven-development/SKILL.md`
- Modify: `skills/wdd-start-epic/SKILL.md`
- Modify: `skills/wdd-plan-epic/SKILL.md`
- Modify: `skills/wdd-start-wave/SKILL.md`
- Modify: `skills/subagent-pr-orchestration/SKILL.md`
- Modify: `skills/wdd-status/SKILL.md`

- [ ] **Step 1: Add profile fields**

Teach epic skills to honor constitution defaults and epic frontmatter overrides.

- [ ] **Step 2: Add adaptive monitoring and risk-based review language**

Preserve heartbeat reliability while allowing lower-cost cadence and review choices by profile.

- [ ] **Step 3: Add status visibility**

Include profile and micro-work status in `wdd-status`.

### Task 6: Verify

**Files:**
- All changed files.

- [ ] **Step 1: Rerun static checks**

Run the three failing checks from Task 1 and expect success.

- [ ] **Step 2: Validate JSON templates**

Run `find skills -path '*/templates/*.json' -print0 | xargs -0 -n1 jq empty`.

- [ ] **Step 3: Check required skill metadata**

Run a shell check that every `skills/*/SKILL.md` has `name:` and `description:`.

- [ ] **Step 4: Check markdown hygiene**

Run `git diff --check`.
