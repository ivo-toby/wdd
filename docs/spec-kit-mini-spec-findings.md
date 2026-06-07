# Spec Kit and MiniSpec Findings

This note records why WDD moved from a CLI-centered implementation to a skill-driven workflow.

## Spec Kit

Spec Kit installs detailed agent command files. The CLI scaffolds and installs, but the workflow authority lives in markdown instructions that agents execute.

Observed patterns:

- Commands are phase-specific: constitution, specify, clarify, plan, tasks, analyze, implement.
- Each command has user input handling, pre-execution checks, workflow outline, validation, completion report, and handoff metadata.
- Artifacts are local markdown files under a spec directory.
- Scripts are helpers for setup and path discovery, not the conceptual source of truth.
- Codex integration installs commands as skills under `.agents/skills`.
- Constitution is a governance artifact that propagates into templates and command behavior.

Implication for WDD:

- WDD should be a skill pack first.
- Any helper script must be optional.
- Runtime work must be possible by reading and writing `.wdd/` artifacts directly.

## MiniSpec

MiniSpec is more conversational and pairing-oriented than Spec Kit.

Observed patterns:

- Constitution captures both project principles and collaboration preferences.
- Design is interactive and records decisions as artifacts.
- Task breakdown is interactive, review-sized, and dependency-aware.
- Status and next-task commands are read-only dashboards over markdown state.
- Documentation freshness is a first-class workflow.
- Knowledge files and decision records keep agent context portable.

Implication for WDD:

- WDD skills should guide reasoning, not only scaffold files.
- The agent should validate semantic readiness, not only schema shape.
- Status/reconcile skills matter for resumability.
- The controller/implementation separation should be explicit in every execution-related skill.

## WDD Direction

WDD keeps its distinct feature:

- validated tickets,
- dependency grid,
- conflict-domain grid,
- safe parallel waves,
- controller-managed subagent execution,
- reconciliation after each wave.

But the runtime model is now skill-driven:

- `skills/` contains the executable agent workflow.
- `templates/` contains optional starting points.
- `.wdd/` is the durable project state.
- No WDD phase depends on a runtime CLI.

