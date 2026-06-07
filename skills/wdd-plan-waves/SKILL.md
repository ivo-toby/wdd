---
name: wdd-plan-waves
description: Create and review WDD dependency waves from validated tickets, using dependencies and conflict domains to decide which tickets can be safely worked in parallel.
---

# WDD Plan Waves

Use this after tickets validate and before any implementation subagents start.

## Workflow

1. Run `wdd validate <epic> --json`.
2. Run `wdd waves plan <epic> --json`.
3. Inspect `.wdd/epics/<epic>/wave-plan.yaml`.
4. Check whether parallel tickets share likely conflict files despite different conflict-domain strings.
5. Reduce parallelism manually if needed.
6. Report the next safe wave and known conflict risks.

## Rules

- Prefer fewer high-quality parallel agents over avoidable merge conflict cleanup.
- Foundation, schemas, config, and shared abstractions usually belong in earlier waves.
- UI/API consumers usually follow contracts and data model tickets.

