---
id: WDD-CONSTITUTION
kind: constitution
version: 1.0.0
status: active
ratified: YYYY-MM-DD
last_amended: YYYY-MM-DD
---

# Project Constitution

## Boundaries

- This project owns: [PROJECT_OWNERSHIP_BOUNDARIES]
- This project must not change: [OUT_OF_SCOPE_BOUNDARIES]

## Prerequisites

- Required runtimes and package managers: [RUNTIMES]
- Required services, secrets, or data: [SERVICES_SECRETS_DATA]
- Required local verification commands: [VERIFICATION_COMMANDS]

## Agent Roles

- The WDD controller plans, validates, schedules waves, monitors state, and reconciles drift.
- Implementation agents work only from assigned implementation briefs.
- The controller must not implement wave tickets.

## Ticket Rules

- Tickets must be self-contained and independently understandable.
- Tickets must declare dependencies in YAML frontmatter.
- Tickets must declare conflict domains for files, schemas, config, migrations, or tests likely to collide.
- Tickets must include concrete verification evidence.

## Wave Rules

- A wave may contain multiple tickets only when dependencies are satisfied and conflict risk is acceptable.
- Prefer smaller safe waves over parallel work that will create merge conflict cleanup.
- After each wave, reconcile actual merged work against planned architecture before starting the next wave.

## Governance

- Amend this constitution before changing the workflow contract.
- Version changes use semantic versioning:
  - MAJOR: role, artifact, or gate changes that break existing epics.
  - MINOR: new required sections, checks, or gates.
  - PATCH: clarifications that do not change behavior.

