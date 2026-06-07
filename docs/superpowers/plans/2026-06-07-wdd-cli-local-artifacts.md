# WDD CLI Local Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a lightweight local-first WDD CLI that stores epics, tickets, wave plans, and controller state in markdown/YAML files.

**Architecture:** Use a small Node.js ESM package with a `wdd` binary and focused modules for frontmatter, local store operations, validation, wave planning, and command handling. GitHub is not part of the core data model; future sync adapters can map local IDs to external issue links through frontmatter.

**Tech Stack:** Node.js 20+, plain ESM JavaScript, `yaml` for YAML/frontmatter parsing, Vitest for behavior tests.

---

### Task 1: Define the Artifact Contract With Tests

**Files:**
- Create: `tests/local-store.test.js`
- Create: `tests/waves.test.js`
- Create: `tests/cli.test.js`
- Create: `package.json`

- [x] **Step 1: Write failing tests**

Tests cover mandatory YAML frontmatter for epics/tickets, deterministic IDs and paths, validation failures for weak tickets, dependency-aware wave planning, conflict-domain separation, and `start-wave` writing controller state and briefs.

- [x] **Step 2: Run tests to verify RED**

Run: `npm test`

Expected: FAIL because `src/wdd.js` and `bin/wdd.js` do not exist yet.

### Task 2: Implement Local Store and Frontmatter

**Files:**
- Create: `src/frontmatter.js`
- Create: `src/local-store.js`
- Create: `src/wdd.js`

- [x] **Step 1: Implement markdown/frontmatter parsing and writing**

Use `yaml` to parse and stringify frontmatter. Reject markdown artifacts without frontmatter when validation expects metadata.

- [x] **Step 2: Implement `.wdd` initialization, epic creation, ticket creation, and artifact reads**

Create `.wdd/config.yaml`, `.wdd/constitution.md`, and one folder per epic under `.wdd/epics/<id>-<slug>/`.

- [x] **Step 3: Run local-store tests**

Run: `npm test -- tests/local-store.test.js`

Expected: PASS.

### Task 3: Implement Validation and Wave Planning

**Files:**
- Create: `src/validation.js`
- Create: `src/waves.js`

- [x] **Step 1: Implement ticket quality validation**

Require clear metadata, deliverable, RED/GREEN section, acceptance criteria, verification, and out-of-scope body sections.

- [x] **Step 2: Implement topological wave grouping**

Group dependency-ready tickets together only when conflict domains do not overlap. Detect missing dependencies and cycles.

- [x] **Step 3: Run wave tests**

Run: `npm test -- tests/waves.test.js`

Expected: PASS.

### Task 4: Implement CLI Commands

**Files:**
- Create: `bin/wdd.js`
- Create: `src/cli.js`

- [x] **Step 1: Implement command parser**

Support `init`, `constitution init`, `new feature|spike`, `ticket create`, `validate`, `waves plan`, `start-wave`, `reconcile`, `status`, `doctor`, and `schema`.

- [x] **Step 2: Keep output agent-friendly**

Support `--json` for commands that report structured data. Keep human output concise.

- [x] **Step 3: Run CLI tests**

Run: `npm test -- tests/cli.test.js`

Expected: PASS.

### Task 5: Package Skills and Documentation

**Files:**
- Modify: `README.md`
- Create: `skills/wave-driven-development/SKILL.md`
- Create: `skills/subagent-pr-orchestration/SKILL.md`
- Create: `docs/artifact-schema.md`

- [x] **Step 1: Document install/init/start flow**

Explain local-first storage, frontmatter metadata, optional adapters, and the controller/worker boundary.

- [x] **Step 2: Update packaged skills**

Make WDD read/write `.wdd` artifacts, make subagent orchestration accept local ticket briefs as the source of truth, and include step-level skills for init, constitution, epic start, ticket writing, validation, wave planning, wave start, and reconciliation.

- [x] **Step 3: Verify complete project**

Run: `npm test`, `npm run lint`, and inspect `git diff`.
