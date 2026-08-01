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

Every approval is **bound to the approved bytes** — the same doctrine as
the governance fingerprint. Each record carries a SHA-256 of the artifact
at approval time. Drift is enforced in three places:

- **Before apply**: `next` re-hashes; a mismatched rung is re-emitted for
  re-approval, and `plan apply` refuses until it happens.
- **After apply**: intake joins the existing execution gate. The
  governance check that already guards every governed verb (`start`,
  `submit`, `review`/`verify record`, `merge`, `dispatch --task`, …)
  extends to intake fingerprints for non-legacy states — editing spec.md
  mid-execution refuses admission and merges, not just planning. The
  execute-phase `next` surfaces an `intake_drift` blocker (actions
  emptied, the stale rung named with its recording command), exactly like
  `governance_drift`. Intake verbs stay legal in the execute phase for
  precisely this re-approval.
- **Downstream cascade**: the ladder is ordered; re-approving a rung
  invalidates everything after it. A new spec approval clears the research
  and design records; a new research record clears design. And because an
  applied plan was approved against the old upstream, any rung
  re-approval also requires a fresh `plan apply --approved-by` before
  execution resumes — with an unchanged plan file that is a pure
  re-stamp, so the cost is one command, not a re-plan.

An approval of text that has since changed approves nothing, and neither
does an approval whose foundations changed underneath it.

- `wddctl intake spec --approved-by NAME` — refuses unless `.wdd/spec.md`
  exists with the four agreed sections and **numbered acceptance criteria**
  (`AC-1`, `AC-2`, …). Records `{by, at, criteria: N, sha256}`.
- `wddctl intake research --done --by NAME --artifacts <path>...` records
  completed research: approver, and per artifact its path and `sha256`.
  Artifacts must exist, be regular files under `.wdd/`, and be non-empty —
  validated at record time. `--skip --by NAME --reason "..."` records an
  explicit, attributed skip. One of the two is required — silence is not
  an option, and neither form is anonymous. The canonical artifact is a
  **contract inventory**: operation → method/path/shape → reference
  citation, built by actually reading the named reference.
- `wddctl intake design --approved-by NAME` — refuses unless
  `.wdd/design.md` exists. Records `{by, at, sha256}` plus the scope's
  **deliverable command** (see §2, Epic deliverable).
- `plan apply` refuses while the ladder is incomplete or any recorded
  fingerprint no longer matches its file.

There is no path around the ladder. The legacy `plan apply` bootstrap
(creating state when none exists) is **removed**: with no `state.json`,
`plan apply` refuses and names `wddctl init`. State schema bumps to v5, in
which the `intake` section always exists; `migrate` converts v4 states by
adding `intake: {"legacy": true}`, which exempts them from the ladder.
Exemption is therefore an explicit migration artifact, never an inference
from absence — a post-release scope cannot masquerade as grandfathered.

Clarification is not a rung; it is what happens at each approval. Every
rung ends in explicit, attributed user sign-off.

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
     listed one per line as ``- `path/glob` — owned by: <responsibility>``
     (a named responsibility, since task IDs do not exist yet). At plan
     time, lint closes the loop: `unowned_surface` warns for any listed
     surface whose path is not covered by some task's `conflictDomains` —
     a surface with producers and no owning task is a design error caught
     mechanically, not at review.
  4. **Epic deliverable** — what observably runs when the scope is done,
     and the command that proves it. That command is recorded **on the
     scope** at design approval (`intake design --deliverable-command
     "..."`), fingerprinted with the design: finalize's
     `final_verification` runs the ratified global `verification.commands`
     **plus** this scope command. Global config is never mutated
     mid-intake — no governance drift, and an epic-specific smoke check
     never leaks into unrelated future scopes.
  Size discipline is normative: a page of load-bearing structure. If it
  reads like a narrative, it is wrong.

## 3. Plan schema: handover, deliverables, routing

`plan.json` task entries gain optional fields:

- `"context": ["shared-context/contract-inventory.md#orders", "spec.md#AC-3", ...]`
  — machine-carried handover. Ref syntax is `<path>[#<anchor>]`: the path
  is `.wdd`-relative and is **validated at plan apply** (must resolve to a
  regular file inside `.wdd/`, no traversal escapes); the anchor is
  advisory reading guidance, not resolved mechanically. At dispatch time
  refs are resolved to **absolute paths in the controller checkout** —
  the same rule the worker contract already uses for briefs, because task
  branches are cut from a base that may predate the intake artifacts and
  external runners execute in the worktree. Payloads and dispatch packets
  carry the resolved absolute paths; artifact integrity is already
  guaranteed by the fingerprint-bound approvals (§1). Lint warns
  (`missing_context`) when a scope has intake artifacts and a task carries
  no refs.
- `"model": "..."` — per-task override of the risk-tiered implementation
  model. Precedence in `next` payload decoration: task override →
  risk-tiered config → absent. This enables heterogeneous routing: the
  mechanical majority on cheap models, the judgment minority on strong
  ones, inside one scope.
- `"reviewModel": "..."` — the same, for the reviewer of this task.
- `config.models.review` becomes tierable like implementation:
  `{"default": ..., "highRisk": ...}` (a plain string stays valid and
  means both). `run_review` decoration resolves task `reviewModel` →
  risk-tiered `models.review` → absent. Task risk — already derived from
  riskRules, and raised for contract-transcription tasks by §4's rules —
  is the persisted classification that carries "mechanical vs judgment"
  into reviewer routing; no separate nature field is stored.

Brief template gains two required sections, both linted:

- **Deliverable** (`missing_deliverable`) — the observable outcome after
  merge: what exists, runs, or answers. The reviewer's first question is
  whether the diff produces it.
- **Interfaces** — Consumes / Produces for this task, consistent with
  design.md.

Plan approval joins the approved-bytes doctrine: `plan apply
--approved-by` records a composite SHA-256 over the normalized plan, every
task's brief file, and every file named in `context` refs. A nonempty diff
(tasks or scope changed) **requires** `--approved-by` — re-applying a
changed plan silently is refused; an unchanged re-apply preserves the
recorded approval. Post-approval edits to briefs or context files are plan
drift, caught by the same execution gate as intake drift; the remedy is a
fresh `plan apply --approved-by` (empty diff, re-stamp) after the user has
seen the change — which makes reconciliation's brief updates an explicit,
signed-off step rather than a silent mutation. `start`/`dispatch`
revalidate the hashes of the specific brief and context refs they are
about to hand over.

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
  criteria and design.md's epic deliverable statement. `final_verification`
  evidence grows a shape for multiple commands: an ordered list
  `[{command, status}]` covering the global `verification.commands` then
  the scope's deliverable command, plus an overall `status` that is
  `passed` only when every entry passed (any failure or skip → the overall
  is that failure; nothing partial is recorded as passed). The existing
  single-command evidence remains readable (a one-entry list).
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
  `.wdd/dispatch/<task>-<role>-<attempt>.log`, and report per role:
  **worker** dispatches end in the standard status token, read from the
  log tail; **reviewer** dispatches must end with the repository's
  existing SHA-bound `wddctl_review_result` JSON — `dispatch` validates
  it, writes it to a result file beside the log, and the controller
  records it with the existing `review collect` verb. No new evidence
  format is invented; external reviewers speak the same contract internal
  ones already do. The controller reads logs as it would read any
  subagent report; all evidence still flows through git and `wddctl`
  verbs.
  Log policy: `.wdd/dispatch/` is **transient scratch, never committed** —
  `init` (and `migrate`) write a `.wdd/.gitignore` entry for it; the
  directory is `0700`, logs `0600`; filenames use attempt numbering (no
  overwrites) and task IDs sanitized to `[A-Za-z0-9._-]`; the result
  payload carries only a bounded tail of the log (the file on disk holds
  the rest). Raw agent output can contain anything — it gets file
  permissions and a gitignore, not a place in durable state.
- `doctor`'s existing CLI probes (`codex`, `claude`, …) report which
  runners' commands are actually present.
- Probing must not require the runner to already be ratified config
  (that would be a governance cycle), and must not silently execute
  unapproved commands either. So: `wddctl dispatch --probe-command
  '["pi", ...]'` tests an **explicit candidate** the user just typed or
  approved in conversation — exec in a temp directory with a canned
  trivial prompt ("Reply with exactly: DONE"), report exit code, wall
  time, and whether the token came back. Registration order is probe →
  `config set runners ...` → ratify/amend. `dispatch --probe <name>`
  re-verifies an already-ratified runner and is **governed** — it executes
  config-loaded commands, so it refuses under drift like any execution
  verb; the deliberately ungoverned path is only ever `--probe-command`,
  where the command is explicit in the invocation. Task dispatch
  (`dispatch --task`) is likewise governed, so an unratified runner can
  never run a task. The `wdd-runners` skill ends every registration with a passing
  probe — a runner that was never probed is configuration fiction.

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

- Schema v5: `intake` always present. `migrate` converts v4 by adding
  `intake: {"legacy": true}` (ladder-exempt); the exemption is explicit
  migration metadata, never inferred from absence. The no-state
  `plan apply` bootstrap is removed — `init` is the only way to create
  state.
- Existing plans without `context`/`model`/Deliverable: valid; lint warns
  where it applies. `--strict` makes warnings fatal, as today.
- Test helpers that walk init→ratify→apply gain the three intake verbs
  (same churn pattern as the models question; accepted cost).

## Non-goals

- No new required narrative artifacts beyond spec.md additions and the
  one-page design.md. Research is conditional and skippable-with-reason.
- No cost-analysis tooling; recording only (and only if it survives plan
  review).
- No changes to execution or finalize mechanics beyond: payload
  decoration precedence, the intake/plan drift extension of the existing
  execution gate, and the multi-command `final_verification` evidence
  shape defined in §5.
- Jira provider and enterprise overlay remain the separate consolidation
  phase.
