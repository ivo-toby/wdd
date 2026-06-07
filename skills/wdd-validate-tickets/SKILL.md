---
name: wdd-validate-tickets
description: Validate WDD epic and ticket artifacts for schema correctness, dependency soundness, semantic readiness, conflict-domain quality, verification evidence, and subagent pick-up readiness.
---

# WDD Validate Tickets

Use this before wave planning and after any ticket edits.

## User Input

If the user names an epic, validate that epic. If not, identify the active or most recent non-complete epic under `.wdd/epics/`.

## Preconditions

- Epic folder exists.
- Ticket files exist under `tickets/`.
- Do not write implementation code.

## Workflow

1. Load artifacts:
   - `.wdd/constitution.md`
   - `epic.md`
   - `prd.md`
   - `design.md`
   - all `tickets/*.md`
   - `validation-checklist.md` if present

2. Validate epic frontmatter:
   - `id`
   - `kind: epic`
   - `type`
   - `slug`
   - `title`
   - `status`
   - `created_at`
   - `updated_at`
   - `constitution_version`
   - `adapter_links`

3. Validate every ticket frontmatter:
   - `id`
   - `kind: ticket`
   - `epic`
   - `slug`
   - `title`
   - `status`
   - `wave`
   - `depends_on`
   - `conflict_domains`
   - `branch`
   - `verification`
   - `adapter_links`

4. Validate ticket body sections:
   - Context
   - End Goal / Deliverable
   - Scope
   - RED/GREEN TDD
   - Acceptance Criteria
   - Verification
   - Review Handoff
   - Out of Scope

5. Validate dependency graph:
   - Every dependency ID exists.
   - No ticket depends on itself.
   - No dependency cycle exists.
   - Foundation work precedes consumers.

6. Validate semantic readiness:
   - Deliverable is observable.
   - Acceptance criteria can be evaluated.
   - Verification commands are concrete.
   - TDD guidance names the first failing check or expected failure.
   - Out-of-scope prevents predictable overreach.
   - Review handoff names real risk areas.

7. Validate conflict domains:
   - Domains are not empty.
   - Shared files and broad directories are named.
   - Tickets likely to touch the same file/domain share a conflict domain.
   - Config, manifests, schemas, migrations, generated code, and shared tests are explicitly called out.

8. Update `validation-checklist.md`:
   - Mark passing checks with `[x]`.
   - Leave failing checks unchecked.
   - Add notes with exact ticket IDs and file paths.

9. If failures exist:
   - Fix artifacts directly when the fix is unambiguous.
   - Ask the user only when a product/design decision is required.
   - Re-run the validation pass after edits.

## Done When

- `validation-checklist.md` reflects current state.
- All blocking findings are fixed or recorded with explicit user-needed questions.
- The next phase is `wdd-plan-waves` only when validation passes.

