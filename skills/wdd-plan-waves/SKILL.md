---
name: wdd-plan-waves
description: Build a dependency and conflict-domain grid from validated WDD tickets, then write a wave plan that schedules safe parallel implementation groups for subagents.
---

# WDD Plan Waves

Use this after ticket validation passes.

## User Input

Respect user constraints such as maximum parallelism, preferred review cadence, high-risk areas, or required sequencing.

## Preconditions

- Tickets validate.
- `validation-checklist.md` has no blocking unchecked items.
- Do not start implementation agents.

## Workflow

1. Load:
   - Constitution.
   - Epic, PRD, design.
   - All ticket files and frontmatter.
   - Validation checklist.

2. Build ticket inventory:
   - ID.
   - Title.
   - Status.
   - Dependencies.
   - Conflict domains.
   - Verification commands.
   - Likely touched files from body text.

3. Build dependency grid:
   - For each ticket, list `depends_on`.
   - For each ticket, list tickets it blocks.
   - Detect cycles. If cycles exist, stop and fix ticket dependencies.
   - Identify foundation tickets with broad downstream impact.

4. Build conflict grid:
   - Compare every pair of tickets.
   - Treat identical conflict domains as a conflict.
   - Treat parent/child path domains as a likely conflict.
   - Treat package manifests, lockfiles, schema files, migrations, generated types, and shared tests as high-risk even when named differently.

5. Create waves:
   - Start with tickets that have no unmet dependencies.
   - Add a ticket to the current wave only if it does not conflict with selected tickets.
   - Defer conflicting tickets to the next wave.
   - After selecting a wave, mark those tickets as dependency-satisfied for later waves.
   - Prefer smaller waves when conflict risk is uncertain.

6. Write `wave-plan.md`:
   - YAML frontmatter with `kind: wave_plan`, `epic`, `status`, dates.
   - Ticket inventory table.
   - Dependency grid.
   - Waves with ticket lists, grouping rationale, and stop condition.
   - Known conflict risks.
   - Manual adjustments made and why.

7. Update ticket frontmatter:
   - Set `wave` to the assigned wave number.
   - Preserve `status`.
   - Update `updated_at` if present.

8. Update epic status:
   - Set to `waves_planned`.
   - Update `updated_at`.

## Done When

- `wave-plan.md` exists and is internally consistent.
- Every ticket has a wave assignment.
- Known conflict risks are documented.
- The next phase is `wdd-start-wave`.

