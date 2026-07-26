# Spec Kit and MiniSpec Findings

This note records comparative research on Spec Kit and MiniSpec done while
shaping WDD's workflow, and how WDD's own runtime model evolved afterward —
first toward text-only, then to the current split between `wddctl` and
skills. See "WDD direction" below for the current conclusion and why it
changed.

## Spec Kit

Spec Kit installs detailed agent command files. The CLI scaffolds and installs, but the workflow authority lives in markdown instructions that agents execute.

Observed patterns:

- Commands are phase-specific: constitution, specify, clarify, plan, tasks, analyze, implement.
- Each command has user input handling, pre-execution checks, workflow outline, validation, completion report, and handoff metadata.
- Artifacts are local markdown files under a spec directory.
- Helper scripts can be useful in other ecosystems, but WDD should not require
  them. The workflow authority lives in portable text instructions.
- Codex integration installs commands as skills under `.agents/skills`.
- Constitution is a governance artifact that propagates into templates and command behavior.

Implication for WDD, as understood at the time (superseded — see "WDD
direction" below):

- WDD should be a skill pack first.
- Any helper script must be optional.
- Runtime work must be possible by reading and writing `.wdd/` artifacts directly.

That third point in particular didn't survive contact with real use: nothing
stopped an agent from reading and writing `.wdd/` artifacts directly *and
incorrectly*, which is exactly the failure mode described below.

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

## WDD direction

WDD kept the feature that mattered from this comparison: a dependency- and
conflict-aware task graph with controller-managed execution and
reconciliation. What changed is the runtime model that carries it.

The conclusion below this heading originally said WDD's runtime should be
text-only — Markdown skills as the sole workflow authority, with "no WDD
phase depends on a runtime CLI, package manager, validator script, or local
binary." That conclusion has been superseded; see the note at the end of
this section for why.

WDD tried the text-only version. It did not hold up, for a specific and
fairly mundane reason: prose can specify a rule, but it cannot enforce one.
A skill instruction can say "never start two tasks that write the same
file" or "never merge without a passing review," and a careful agent will
usually follow it — but an agent under context pressure, or one that skips a
step because it seems obviously fine this once, can also just not. There was
no layer underneath the prose that would refuse the illegal action outright.

The clearest evidence was structural rather than anecdotal. The conflict-domain
check — the rule the whole parallel-execution model rests on — lived only in
`wddctl next`, which is read-only advice. The state machine underneath it would
happily start two tasks writing the same file if asked directly. The rule was
real, documented, and unenforced, and nothing in the artifacts would have shown
that it had been skipped.

The fix was not to write more careful prose. It was to stop asking prose to
do a state machine's job. WDD now splits the two deliberately:

- `wddctl`, a small dependency-free Python controller, owns every mechanical
  action and invariant: state transitions, conflict-domain exclusion between
  concurrently active tasks, evidence pinned to a specific commit SHA,
  Git-verified merges. These are enforced in code an agent cannot talk its
  way around — see [`docs/wddctl.md`](wddctl.md).
- Skills own everything that's actually judgment: what a task should
  contain, whether a diff correctly implements a spec, how to respond to a
  review finding, when work is genuinely done. `wddctl` has no opinion on any
  of that, and shouldn't.

This keeps what both Spec Kit and MiniSpec got right — portable Markdown
artifacts, an explicit constitution, dependency- and review-aware task
breakdown — without asking Markdown to guarantee something only a state
machine can.

**2026-07-26 — superseded.** The original "no CLI" conclusion above was
written before `wddctl` existed, when the working hypothesis was that a
careful enough skill pack could substitute for one. It couldn't, for the
reason described above, and this section now reflects the design that
replaced it.
