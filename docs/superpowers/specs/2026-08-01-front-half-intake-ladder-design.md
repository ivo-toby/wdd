# The Front Half: Intake Ladder, Handover, and the Granularity Lever

- Date: 2026-08-01
- Status: approved design, pre-implementation
- Scope: wdd-public (phase 6 of the redesign)

## Problem

The redesign hardened the back half — setup, execution mechanics, finalize —
and the small-model trials promptly exposed the front half. The strongbad
scope (Qwen worker, Opus post-review) failed for process reasons, not model
reasons: a rich source spec was lossily compressed into a one-page spec.md
and 40-line briefs; nobody inventoried the named reference implementation,
so the API surface was fabricated; no design step meant the shared registry
had five producers and no owner; tasks had no declared outcome to review
against; and handover to workers and reviewers was prose convention, which
weak models ignore. Six jobs — spec, clarify, research, clarify, plan,
tasks — were folded into one prose skill.

## Design principle

Same as every prior phase: choreography in the state machine, judgment in
skills. The front half becomes a `next`-driven ladder like setup and
finalize. Depth is added only through artifacts with multiple consumers —
never through narrative ceremony (the July lesson stands).

## 1. The intake ladder

Between `ratify` and `plan apply`, `next` walks new rungs, one at a time:

```
agree_spec -> research -> agree_design -> plan
```

Evidence lives in a new optional `state["intake"]` section (finalize's
twin), written through new verbs:

- `wddctl intake spec --approved-by NAME` — refuses unless `.wdd/spec.md`
  exists with the four agreed sections and **numbered acceptance criteria**
  (`AC-1`, `AC-2`, …). Records `{by, at, criteria: N}`.
- `wddctl intake research --done --artifacts <path>...` records completed
  research and its artifact paths (under `.wdd/shared-context/`);
  `--skip --reason "..."` records an explicit skip. One of the two is
  required — silence is not an option. The canonical artifact is a
  **contract inventory**: operation → method/path/shape → reference
  citation, built by actually reading the named reference.
- `wddctl intake design --approved-by NAME` — refuses unless
  `.wdd/design.md` exists. Records `{by, at}`.
- `plan apply` refuses while the ladder is incomplete — for states whose
  `init` created the intake scaffold. Pre-existing states (no `intake`
  key) are grandfathered: legacy scopes keep working unchanged.

Clarification is not a rung; it is what happens at each approval. Every
rung ends in explicit user sign-off.

Phase derivation: `setup` extends through the intake rungs (scope is still
null); `derived_phase` itself is unchanged. `setup_next_actions` gains the
rung logic, each action carrying the recording command and a `judgment`
pointing at the owning skill stage.

## 2. Artifacts

- **`.wdd/spec.md`** — unchanged four sections, plus acceptance criteria
  are numbered (`- [ ] AC-1: ...`). Finalize's `final_review` walks them by
  number.
- **`.wdd/shared-context/contract-inventory.md`** (when research applies) —
  the fabrication killer. Every row cites the reference (file:line or doc
  anchor). Client-task briefs and reviewers cite rows, not memories.
- **`.wdd/design.md`** — one page with teeth, four sections:
  1. **Components** — the units the scope produces.
  2. **Interfaces** — per component, Consumes / Produces (exact names and
     types where known).
  3. **Integration surfaces** — every file/registry multiple tasks feed,
     and **which task owns each**. A shared surface with producers and no
     owning integration task is a design error, caught here, not at
     review.
  4. **Epic deliverable** — what observably runs when the scope is done,
     and the command that proves it. That command belongs in
     `verification.commands` (propose the amendment at design approval).
  Size discipline is normative: a page of load-bearing structure. If it
  reads like a narrative, it is wrong.

## 3. Plan schema: handover, deliverables, routing

`plan.json` task entries gain optional fields:

- `"context": ["shared-context/contract-inventory.md#orders", "spec.md#AC-3", ...]`
  — machine-carried handover. `next`/`start` payloads include it; the
  dispatch contract (below) forwards it verbatim. Lint warns
  (`missing_context`) when a scope has intake artifacts and a task carries
  no refs.
- `"model": "..."` — per-task override of the risk-tiered implementation
  model. Precedence in `next` payload decoration: task override →
  risk-tiered config → absent. This enables heterogeneous routing: the
  mechanical majority on cheap models, the judgment minority on strong
  ones, inside one scope.

Brief template gains two required sections, both linted:

- **Deliverable** (`missing_deliverable`) — the observable outcome after
  merge: what exists, runs, or answers. The reviewer's first question is
  whether the diff produces it.
- **Interfaces** — Consumes / Produces for this task, consistent with
  design.md.

Optional, minimal instrumentation (cuttable if the implementation plan
finds it dead weight): `submit --tokens N` records the worker's reported
token usage on the task (`task.cost`), surfaced by `status --json` and
`render`. No analysis machinery — just the raw numbers that let granularity
be tuned from data instead of vibes.

## 4. The granularity lever

The lever is judgment content, not size. Normative rules in `wdd-plan`:

- **Decompose until each task's residual judgment fits the configured
  worker tier.** A frontier worker gets few, intent-level tasks; a small
  worker gets many tasks whose briefs approach pseudo-code with every
  contract cited from the inventory. Judgment does not disappear when
  tasks shrink — it moves into planning, where the strongest model pays
  for it once instead of N workers paying at execution (or failing at the
  capability cliff, which costs more than either).
- **Classify each task's nature** — mechanical/transcription vs
  integration/judgment — and route models per task (the `model` field),
  rather than sizing the whole scope for one tier.
- Review tiers with the diff: mechanical diffs take a mid reviewer;
  contract and integration diffs take the strongest configured.

## 5. Skills

- **New `wdd-intake`** — owns the three rungs (spec+clarify, research,
  design), one skill because the stages are one conversation; `next`'s
  judgment names the stage. Carries the artifact contracts above and the
  handoff rule (each rung ends with sign-off and the offer to continue).
- **`wdd-plan`** — slims to pure decomposition: outcome-driven tasks
  (Deliverable + Interfaces), context refs, granularity/routing rules,
  lint, recorded approval. Ingest/clarify/spec/research content moves out.
- **`wdd-run`** — the dispatch packet becomes explicit contract again:
  worker packet = brief path + worktree + branch + context refs (from the
  payload) + deliverable expectation + status-token contract; reviewer
  packet = brief + context refs + diff + the criteria it discharges.
- **`wdd-review`** — reviews against the declared Deliverable first, the
  inventory for contract surfaces, criteria by number.
- **Finalize tie-in** — `final_review`'s judgment references the numbered
  criteria and design.md's epic deliverable statement (prose change only).
- **New `wdd-runners`** — sets up and maintains the runner registry:
  probe what's on the box (`doctor`'s CLI report), help the user author
  each command template (headless flags, cwd handling, model selection),
  prove every runner with `wddctl dispatch --probe <name>` before it is
  recorded, and route the config change through governance (`config set
  runners ...` + ratify/amend — runners are fingerprinted config like
  everything else). Triggers on "add a runner", "use qwen/codex as a
  worker", "set up local workers".

## 6. Runners: generic dispatch beyond the harness

A worker is file-in, file-out: worktree + brief + context in, commits +
status token out. Nothing requires it to be a subagent of the controller's
harness. An optional `runners` map in config.json makes the `model` field
resolvable to an external agent CLI:

```json
"runners": {
  "qwen-local": {"command": ["pi", "--headless", "--model", "qwen3.6",
                              "--cd", "{worktree}", "-p", "{prompt}"]},
  "codex":      {"command": ["codex", "exec", "--cd", "{worktree}", "{prompt}"]}
}
```

- Resolution: a `model` value (per-task override or tier config) naming a
  runner dispatches via that runner; any other value is harness-native,
  exactly as today. Fully backward compatible.
- `wddctl dispatch --task ID --role worker|reviewer` owns the mechanical
  part: assemble the dispatch packet (§5's contract) into the prompt, exec
  the runner in the task's worktree, capture output to
  `.wdd/dispatch/<task>-<role>.log`, report exit code and the trailing
  status token. The controller reads the log as it would read any worker
  report; all evidence still flows through git and `wddctl` verbs.
- `doctor`'s existing CLI probes (`codex`, `claude`, …) report which
  runners' commands are actually present.
- `wddctl dispatch --probe <runner>` proves a runner end-to-end before any
  scope depends on it: exec the command template in a temp directory with a
  canned trivial prompt ("Reply with exactly: DONE"), and report exit code,
  wall time, and whether the expected token came back. The `wdd-runners`
  skill ends every registration with a passing probe — a runner that was
  never probed is configuration fiction.

Documentation ships with the feature: a "Runners" section in
`docs/wddctl.md` (config shape, resolution order, `dispatch`/`--probe`
semantics, real transcripts against a stub runner) and a worked example in
`docs/workflow.md`'s appendix territory for one local runner.

**Hard non-goals, stated to stay out of harness territory:** no streaming,
no interactive sessions, no tool-permission mediation, no supervision or
retries. One-shot headless exec with exit-code semantics; `NEEDS_CONTEXT`
in the output is handled like any worker saying it — re-dispatch with more
context. The child agent's own sandboxing, permissions, and timeouts are
its runner command's business, authored per machine by the operator.

## 7. Compatibility and migration

- Legacy states (no `intake` key): everything keeps working; the ladder is
  init-created-state-only. No state migration needed (the section is
  optional, like `finalize`).
- Existing plans without `context`/`model`/Deliverable: valid; lint warns
  where it applies. `--strict` makes warnings fatal, as today.
- Test helpers that walk init→ratify→apply gain the three intake verbs
  (same churn pattern as the models question; accepted cost).

## Non-goals

- No new required narrative artifacts beyond spec.md additions and the
  one-page design.md. Research is conditional and skippable-with-reason.
- No cost-analysis tooling; recording only (and only if it survives plan
  review).
- No changes to execution or finalize mechanics beyond payload decoration
  precedence and prose tie-ins.
- Jira provider and enterprise overlay remain the separate consolidation
  phase.
