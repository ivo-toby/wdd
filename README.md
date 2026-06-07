# WDD

Wave-Driven Development is a skill-driven workflow for coding agents.

The runtime is the agent skill pack, not a project-management CLI. Agents create
and update local markdown artifacts directly under `.wdd/`, using YAML
frontmatter for machine-readable state and Markdown bodies for human and agent
context.

This makes the workflow portable across local agents, cloud agents, and hosted
coding environments where installing or running a custom CLI is not practical.

## What WDD Provides

- A project constitution for boundaries, prerequisites, verification, and agent roles.
- Local epics with PRD/design context.
- Local tickets with YAML frontmatter, dependencies, conflict domains, verification, and review handoff.
- Ticket validation as an agent skill.
- Dependency and conflict-domain wave planning as an agent skill.
- Controller state and implementation briefs for subagent orchestration.
- Reconciliation after every wave before the next wave starts.

## Skill Pack

Install or copy the directories in `skills/` into the agent's skill directory.

For Codex-style local skills, that means:

```text
~/.agents/skills/
  wave-driven-development/
  wdd-init-project/
  wdd-constitution/
  wdd-start-epic/
  wdd-write-tickets/
  wdd-validate-tickets/
  wdd-plan-waves/
  wdd-start-wave/
  subagent-pr-orchestration/
  wdd-reconcile-wave/
  wdd-status/
```

Each skill is self-contained enough to run from repository files. The root
`templates/` folder provides canonical artifact templates, but the skills also
describe the required artifact shapes.

## Workflow

1. `wdd-init-project`
   - Creates `.wdd/`, `.wdd/templates/`, and `.wdd/constitution.md`.

2. `wdd-constitution`
   - Defines project boundaries, prerequisites, verification rules, agent roles, ticket rules, wave rules, and governance.

3. `wdd-start-epic`
   - Creates `.wdd/epics/<epic-id>-<slug>/` with `epic.md`, `prd.md`, `design.md`, and support folders.

4. `wdd-write-tickets`
   - Creates self-contained ticket files under `tickets/`.

5. `wdd-validate-tickets`
   - Validates frontmatter, dependencies, ticket body sections, semantic readiness, verification, and conflict domains.

6. `wdd-plan-waves`
   - Writes `wave-plan.md` with a dependency grid, conflict grid, and safe parallel waves.

7. `wdd-start-wave`
   - Writes `controller-state.md` and implementation briefs.

8. `subagent-pr-orchestration`
   - Dispatches one implementation subagent per active-wave brief and manages review/merge gates.

9. `wdd-reconcile-wave`
   - Marks the wave done only after merge/review/verification gates pass and later tickets are updated for drift.

10. `wdd-status`
    - Read-only dashboard over `.wdd/` artifacts.

## Artifact Layout

```text
.wdd/
  constitution.md
  templates/
  epics/
    WDD-0001-auth-refresh/
      epic.md
      prd.md
      design.md
      validation-checklist.md
      wave-plan.md
      controller-state.md
      tickets/
        WDD-0001-T001-token-contract.md
      briefs/
        WDD-0001-T001-token-contract.md
      decisions/
      archive/
```

## Development

The repository contains one development-only validation script. It checks that
the skill pack and templates are structurally complete. It is not part of the
runtime workflow.

```bash
npm test
```
