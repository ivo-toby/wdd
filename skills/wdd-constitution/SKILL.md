---
name: wdd-constitution
description: Draft or update a WDD project constitution covering boundaries, prerequisites, verification rules, agent rules, non-goals, and safety constraints for coding agents.
---

# WDD Constitution

Use this when setting or revising the project boundaries before WDD epics and tickets are created.

## Required Sections

- Boundaries: what this project owns and must not change.
- Prerequisites: install, env, secrets, services, data, and tooling assumptions.
- Verification: test, lint, build, audit, smoke, and review commands.
- Agent rules: what controller agents may do, what implementation agents may do, and handoff rules.
- Non-goals: work that should not be included in tickets without explicit approval.
- External adapters: GitHub/Jira/Linear conventions if used.

## Workflow

1. Read existing repo docs and `.wdd/constitution.md`.
2. Propose concrete constitution edits.
3. Apply edits only after the user confirms the boundaries or when the request is explicit.
4. Run `wdd status --json` after edits to show current WDD state.

