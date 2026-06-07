---
name: wdd-validate-tickets
description: Validate WDD tickets for metadata, dependencies, deliverable clarity, RED/GREEN guidance, verification commands, conflict domains, and readiness for agent execution.
---

# WDD Validate Tickets

Use this before wave planning or whenever tickets were edited.

## Workflow

1. Run `wdd validate <epic> --json`.
2. If validation fails, fix ticket frontmatter or body sections.
3. Check semantic readiness beyond CLI validation:
   - deliverable is observable,
   - dependencies are explicit IDs,
   - conflict domains are specific enough,
   - verification commands are concrete,
   - out-of-scope blocks likely overreach,
   - review handoff names what to inspect.
4. Re-run validation after fixes.

## Gate

Do not run `wdd waves plan` until validation passes and the semantic readiness check is clean.

