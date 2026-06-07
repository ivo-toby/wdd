---
name: wdd-start-epic
description: Start a WDD feature or spike by creating a local epic folder with detailed PRD and design artifacts that can later produce self-contained implementation tickets.
---

# WDD Start Epic

Use this when the user wants to start a feature, spike, migration, refactor, or other broad work item.

## User Input

Treat the user request as the epic seed. Extract feature type, desired outcome, constraints, and non-goals. Ask only for missing information that affects ticket generation or architecture.

## Preconditions

- `.wdd/constitution.md` exists and has no blocking placeholders.
- Work from the repository root.
- Read relevant existing code/docs before choosing an epic shape.
- Do not create implementation tickets yet unless the user explicitly asks and the epic is ready.

## Workflow

1. Determine epic identity:
   - Type: `feature`, `spike`, `migration`, `refactor`, or `hardening`.
   - Slug: 2-5 words, lowercase kebab-case.
   - ID: next sequential `WDD-000N` by scanning `.wdd/epics/`.
   - Folder: `.wdd/epics/<id>-<slug>/`.

2. Create folder structure:

   ```text
   .wdd/epics/<id>-<slug>/
     epic.md
     prd.md
     design.md
     tickets/
     briefs/
     decisions/
     archive/
   ```

3. Write `epic.md` with YAML frontmatter and sections:
   - Product Brief / PRD
   - Design Direction
   - Acceptance Strategy
   - Ticket Strategy
   - Wave Strategy
   - Open Questions

4. Write `prd.md`:
   - Problem statement.
   - Users or consumers.
   - Goals and non-goals.
   - User stories or operational scenarios.
   - Success criteria.
   - Assumptions and dependencies.

5. Write `design.md`:
   - Current repo evidence.
   - Recommended architecture or change shape.
   - Alternatives considered.
   - Data/API/interface contracts if relevant.
   - Verification approach.
   - Risk areas and conflict domains.

6. Create decision records when design choices are material:
   - Path: `decisions/YYYY-MM-DD-<slug>.md`
   - Include status, context, decision, rationale, alternatives, consequences.

7. Validate epic readiness:
   - Outcome is clear.
   - Non-goals are explicit.
   - Design direction can produce bounded tickets.
   - Verification expectations are known.
   - Open questions do not block ticket generation.

## Done When

- Epic folder exists.
- `epic.md`, `prd.md`, and `design.md` are written.
- Material decisions are recorded.
- The next phase is `wdd-write-tickets`, or the user is asked for specific missing context.

