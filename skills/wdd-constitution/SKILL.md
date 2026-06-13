---
name: wdd-constitution
description: Create or amend a text-only WDD constitution with model aliases, storage mode, branching, review policy, verification policy, agent roles, task rules, wave rules, shared-context rules, and governance.
---

# WDD Constitution

Use this when creating, reviewing, or amending `.wdd/constitution.md`.

## User Input

Consider user-provided principles, boundaries, model availability, branch
preferences, WDD profile defaults, review gates, storage preferences,
verification commands, and workflow non-goals.

## Preconditions

- `.wdd/` must exist. If missing, use `wdd-init-project`.
- Read existing `.wdd/constitution.md`. If `.wdd/` exists but
  `.wdd/constitution.md` is missing, create it from this skill folder's
  `templates/constitution.md`.
- Read relevant repo docs before asking the user for information.
- Do not require scripts or CLI commands to create or validate the constitution.

## Workflow

1. Load current constitution:
   - Identify missing setup decisions, stale values, and contradictions.
   - Capture current version, ratified date, and last amended date.

2. Gather repo evidence:
   - Project type and primary languages.
   - Existing optional verification commands.
   - Deployment, data, auth, persistence, security, or CI constraints.
   - Existing agent instructions.

3. Ask for required setup decisions when not inferable:
   - Available model aliases.
   - Model usage for epic definition, planning, simple implementation, complex
     implementation, review, feedback-fix, epic validation, and PR description.
   - Storage mode: local Markdown, GitHub Projects, or both when supported.
   - WDD profile default, usually `standard`.
   - Allowed profiles, usually `micro`, `lite`, `standard`, and `full`.
   - Review mode default, usually `risk_based`.
   - Monitoring mode default, usually `adaptive`.
   - Target branch, defaulting to `main` only if repo evidence does not name
     another branch.
   - Epic branch convention, defaulting to `epic/[epic-slug]`.
   - Task branch convention, defaulting to `task/[task-id]-[task-slug]`.
   - Whether PRs are required for every task.
   - Whether local patches are allowed when PRs are unavailable.
   - Whether P2 findings block merge.
   - Whether P3 findings become follow-up tasks.
   - Whether review comments go to PRs or local files.
   - Whether feedback fixes prefer the original worker or a fresh worker.

4. Fill or amend required sections:
   - Project Scope.
   - Setup Configuration.
   - Model Usage.
   - WDD Profile Defaults.
   - Branching Policy.
   - Review Policy.
   - Verification Policy.
   - Agent Roles.
   - Planning Rules.
   - Task Rules.
   - Wave Rules.
   - Shared Context Rules.
   - Governance.

5. Apply default review policy unless the user overrides it:
   - P1 blocks merge.
   - P2 blocks merge.
   - P3 does not block merge.
   - Feedback processing may use the original worker or a fresh worker,
     whichever is safer.

6. Decide version bump:
   - MAJOR: role, artifact, or gate changes that break existing epics.
   - MINOR: new required sections, checks, or gates.
   - PATCH: wording or clarification only.

7. Validate by inspection:
   - No unexplained placeholders remain in actual constitution artifacts.
   - Rules are testable and use clear MUST or SHOULD language where needed.
   - Optional repo-native verification commands are concrete when named.
   - WDD itself remains text-only.

## Done When

- `.wdd/constitution.md` records setup, branch, profile, review, verification,
  role, task, wave, shared-context, and governance rules.
- Version and dates are updated correctly.
- Open user-needed questions are explicit.
- The next phase is `wdd-start-epic`.
