---
name: wave-driven-development
description: Prepare large architecture, refactor, migration, feature, or spike work with local-first WDD artifacts: constitution, epic markdown, YAML-frontmatter tickets, validation, dependency waves, and controller state before implementation agents start.
---

# Wave-Driven Development

Use this before implementation when work is too broad, risky, or interconnected for one agent to safely code directly.

## Core Rule

The main agent plans and manages. It does not implement wave tickets. Implementation starts only after the epic, tickets, validation result, and wave plan exist under `.wdd/`.

Local markdown is the default source of truth. GitHub, Jira, Linear, or other trackers are adapters recorded in frontmatter, not required storage.

## Required Local Artifacts

- `.wdd/constitution.md`: project boundaries, prerequisites, verification rules, non-goals, safety rules.
- `.wdd/epics/<epic-id>-<slug>/epic.md`: parent epic with mandatory YAML frontmatter.
- `.wdd/epics/<epic-id>-<slug>/tickets/<ticket-id>-<slug>.md`: one bounded ticket per file with mandatory YAML frontmatter.
- `.wdd/epics/<epic-id>-<slug>/wave-plan.yaml`: dependency-aware execution waves.
- `.wdd/epics/<epic-id>-<slug>/controller-state.yaml`: active controller state for resumability.
- `.wdd/epics/<epic-id>-<slug>/briefs/*.md`: implementation briefs for subagents.

## Workflow

1. Initialize or inspect WDD:
   - Run `wdd init --agent codex` when `.wdd/` does not exist.
   - Read `.wdd/constitution.md` before proposing the target shape.
   - Run `wdd status --json` to understand active epics.

2. Shape the epic:
   - Create with `wdd new feature <slug>` or `wdd new spike <slug>`.
   - Fill `epic.md`, `prd.md`, and `design.md` enough for tickets to be self-contained.
   - Keep external tracker links in frontmatter `adapter_links`.

3. Write tickets:
   - Use `wdd ticket create <epic> <slug> --title "<title>" --verify "<cmd>"` for scaffolding.
   - Edit each ticket body so it has clear context, end goal, scope, RED/GREEN TDD guidance, acceptance criteria, verification, review handoff, and out-of-scope sections.
   - Use frontmatter for machine-readable state: `id`, `kind`, `epic`, `status`, `wave`, `depends_on`, `conflict_domains`, `branch`, `verification`, and `adapter_links`.

4. Validate:
   - Run `wdd validate <epic> --json`.
   - Do not plan waves until validation passes.
   - Fix missing dependencies, weak verification, ambiguous deliverables, or missing required sections.

5. Plan waves:
   - Run `wdd waves plan <epic>`.
   - Inspect `wave-plan.yaml`.
   - Prefer fewer high-quality parallel tickets over conflict-heavy parallelism.
   - Conflict domains should name shared files, packages, schemas, config, path aliases, migrations, and shared tests.

6. Start execution:
   - Run `wdd start-wave <epic> --json`.
   - This writes controller state and subagent briefs.
   - Hand off to `subagent-pr-orchestration`.

7. Reconcile after each wave:
   - Inspect merged work, changed files, verification, review findings, and architecture drift.
   - Update later tickets before starting the next wave.
   - Run `wdd reconcile <epic> --wave <n> --done` only after merge/review gates are complete.

## Ticket Quality Gate

Each ticket must be pick-up-ready for one implementation agent:

- The deliverable is explicit and observable.
- Dependencies are frontmatter IDs, not prose.
- Conflict domains are named.
- Verification commands are concrete.
- RED/GREEN TDD guidance says what failure should be proven first.
- Out-of-scope prevents opportunistic expansion.
- Review handoff says what the high-rigor reviewer should verify.

## Output To User

When reporting a prepared plan, include the epic path, ticket paths, wave breakdown, known conflict domains, and the next safe wave. If external tracker sync exists, include adapter links as secondary references.

