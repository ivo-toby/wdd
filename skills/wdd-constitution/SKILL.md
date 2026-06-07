---
name: wdd-constitution
description: Create or amend a WDD project constitution through repo-aware agent reasoning, defining boundaries, prerequisites, verification, agent roles, ticket rules, wave rules, and governance.
---

# WDD Constitution

Use this when creating, reviewing, or amending `.wdd/constitution.md`.

## User Input

Consider any user-provided principles, boundaries, testing rules, non-goals, or agent behavior preferences.

## Preconditions

- `.wdd/` must exist. If missing, use `wdd-init-project`.
- Read existing `.wdd/constitution.md`.
- Read relevant repo docs before asking the user for information.

## Workflow

1. Load current constitution:
   - Identify placeholders, stale values, and missing sections.
   - Capture current version, ratified date, and last amended date.

2. Gather repo evidence:
   - Project type and primary languages.
   - Existing verification commands.
   - Deployment or runtime constraints.
   - Security, data, auth, persistence, or CI constraints.
   - Existing agent instructions.

3. Fill or amend required sections:
   - Boundaries: owned areas and prohibited changes.
   - Prerequisites: runtimes, services, secrets, data, setup.
   - Verification: test, lint, build, smoke, review, audit commands.
   - Agent Roles: controller, implementation subagent, reviewer, human.
   - Ticket Rules: frontmatter, deliverable, scope, TDD, acceptance, verification, out-of-scope.
   - Wave Rules: dependency, conflict-domain, pause, reconciliation, start conditions.
   - Governance: amendment process and semantic versioning.

4. Decide version bump:
   - MAJOR: role or artifact contract changes that break existing epics.
   - MINOR: new required sections, gates, or workflow rules.
   - PATCH: wording or clarification only.

5. Write `.wdd/constitution.md`:
   - Preserve meaningful existing content.
   - Remove unexplained placeholders.
   - Use ISO dates.
   - Add a sync impact note near the top if this was an amendment.

6. Validate:
   - No unexplained `TODO` or bracket placeholders remain.
   - Rules are testable and use clear MUST/SHOULD language.
   - Verification commands are concrete or explicitly marked unavailable.

## Done When

- `.wdd/constitution.md` has the required sections.
- Version and dates are updated correctly.
- Follow-up TODOs are explicit and justified.
- The next phase is `wdd-start-epic`.

