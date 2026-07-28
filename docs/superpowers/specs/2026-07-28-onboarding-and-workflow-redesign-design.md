# WDD onboarding and workflow redesign

- Date: 2026-07-28
- Status: approved design, pre-implementation
- Scope: wdd-public, provider-aware (enterprise port follows separately)

## Problem

A trial run in a fresh project (`appie-mcp`, 2026-07-27) exposed the weak
half of WDD: setup. Execution is governed by a state machine, but setup —
the phase where the agent knows least about the project — is governed only
by skill prose and a template. Observed failures:

- `state.json` did not exist until `plan apply`, so `wddctl next` could not
  drive setup; the agent improvised the choreography from the constitution
  template.
- The ratified constitution retained template boilerplate verbatim,
  including meta-instructions ("state which this repo uses if not the
  default").
- Machine-consumed config (model aliases) was embedded as a JSON block
  inside markdown and got dubious values.
- All 10 tasks were marked `risk: high`, collapsing `risk_based` review
  into review-everything.
- Tasks formed a near-pure dependency chain, so `maxConcurrent: 3` bought
  nothing; conflict domains were exhaustive per-file lists.
- The agent presented `wddctl` commands to the user instead of running
  them.
- Execution never started: `state.json` recorded exactly one event.

Separately, the day-to-day flow is missing its front and back: there is no
spec-intake phase with an agreement gate, and nothing happens after the
last task merges — no scope-level review, no handoff.

## Design principle

WDD's own stated principle, applied to its weakest phases: choreography
lives in the state machine (cannot be ignored), judgment lives in skills
(prose). Setup and finalization become state-machine phases driven by
`wddctl next`, exactly like execution.

## 1. Setup as a state-machine phase

New verb: `wddctl init`. It is the first command in any repo, and the
router skill's first line becomes: no `.wdd/state.json` → run
`wddctl init`.

`init` deterministically:

1. Runs the repository probe (absorbing today's `constitution probe`,
   which remains as an alias for a deprecation window): existing branch
   conventions, detectable verification commands, project type.
2. Writes `.wdd/config.json` with probed defaults filled and unresolvable
   knobs listed in an `openQuestions` array. `mergeSurface` is always an
   open question — it is a deliberate decision, never silently defaulted
   on first setup.
3. Writes a prose-only `.wdd/constitution.md` draft from a short template
   that contains no JSON blocks and no meta-instructions — the template
   text must be shippable as-is.
4. Creates `.wdd/tasks/`, `.wdd/shared-context/`, and `state.json` with
   `scope: null`. Phase is derived, not stored: `setup` (unratified or no
   scope) → `execute` (scope active) → `finalize` (all tasks merged) →
   `delivered`.
5. Prints `wddctl next` output.

`next` gains pre-scope actions with the same shape as execution actions
(literal command included):

- open config questions exist → `resolve_config`: ask the user the listed
  questions in one compact round, then record answers via
  `wddctl config set`.
- config resolved but unratified → `ratify`.
- ratified but no plan → `plan`: run the intake flow (§3), then
  `wddctl plan apply`.

`plan apply` updates the init-created state instead of creating it.
`init` on an initialized repo is a no-op that reports state; it never
overwrites.

## 2. config.json

All machine-consumed knobs move out of `constitution.md` into
`.wdd/config.json`, schema-validated (JSON Schema ships in the package;
`doctor` and every state-loading command validate it):

```json
{
  "schemaVersion": 1,
  "kind": "wdd_config",
  "branching": {
    "targetBranch": "main",
    "basePattern": "wdd/{scope-slug}",
    "taskPattern": "task/{task-id}"
  },
  "verification": {
    "commands": ["npm test"],
    "unavailableJustification": null
  },
  "review": {
    "policy": "risk_based",
    "blockingSeverities": ["P1", "P2"]
  },
  "merge": {
    "surface": "pr",
    "mode": "controller",
    "reconcileEveryNMerges": 3
  },
  "concurrency": { "maxConcurrent": 3 },
  "models": {
    "planning": null,
    "implementation": { "default": null, "highRisk": null },
    "review": null
  },
  "riskRules": [{ "pattern": "src/auth/**", "risk": "high" }],
  "taskProvider": { "type": "local" },
  "openQuestions": []
}
```

Rules:

- `wddctl config set <dotted.path> <value>` / `config get` — agents write
  answers mechanically; no markdown editing. Hand-editing the file remains
  possible; validation catches damage.
- Resolving a question removes it from `openQuestions`. Ratification is
  refused while `openQuestions` is non-empty.
- The ratification fingerprint covers `config.json` and `constitution.md`
  together. `amend` re-fingerprints both. Post-ratification config edits
  without an amend are drift: `doctor` and `next` report it and
  execution-affecting verbs refuse.
- `plan.json` scope fields (`reviewPolicy`, `maxConcurrent`,
  `reconcileEveryNMerges`, and now `mergeSurface`, `mergeMode`) become
  optional per-scope overrides of config defaults rather than required
  fields. The intake flow asks per scope whether to override the surface
  ("this scope: PR or local?") so small epics can run fully local while
  the repo default stays `pr`.
- `taskProvider` is a stub this round: only `"local"` validates. The
  object shape is reserved so wdd-enterprise's Jira provider becomes a
  config value, not a fork.

`constitution.md` becomes pure prose: project intent, what reviewers
should focus on, why the risk categories are what they are, workflow norms
in plain language.

## 3. Spec intake: one entry point, one agreement gate

A reshaped `wdd-plan` skill, invoked when the user brings work ("let's
build X", a spec directory, an epic doc):

1. **Ingest** the provided documents/context. If no spec exists, say so
   and stop — WDD assumes specced work; writing the spec is the
   engineer's job.
2. **Challenge and clarify** — push back on gaps, contradictions, scope
   ambiguity; questions to the user in compact rounds.
3. **Agree** — write the agreed understanding to `.wdd/spec.md` (goal,
   in-scope, out-of-scope, acceptance criteria). The finalize phase (§5)
   reviews the epic branch against this document.
4. **Decompose** into tasks shaped for parallel subagent execution:
   dependency fan-out over chains, glob-level conflict domains, risk
   derived via `riskRules`.
5. **Present for approval** — the plan plus `wddctl plan preview`
   rendered as the wave picture. Approval is recorded:
   `wddctl plan apply --approved-by NAME` stamps it into state.

## 4. Plan quality: lint and derived risk

- `plan apply` computes task risk by matching `riskRules` patterns
  against each task's `conflictDomains`. Risk levels stay the existing
  two (`normal`, `high`). A plan may override risk only upward
  (`normal` → `high`), never downward.
- New verb `wddctl plan lint`, also auto-run during `apply` (warnings by
  default; `--strict` fails). Checks:
  - full or near-full serialization (dependency chain where
    `maxConcurrent` is unused);
  - degenerate risk distribution (every task the same risk);
  - per-file conflict-domain lists that a glob should cover;
  - domains so coarse the preview collapses to one task per round;
  - tasks with no `specPath` brief or an empty brief.

## 5. Execution changes

- **TDD in `wdd-worker`** becomes a hard rule: red test first, then
  implementation. The brief template gains a "verification: tests that
  must exist" slot. Tasks with no meaningful red/green (docs, config)
  are declared as such by the worker, not faked.
- **Models are consumed mechanically.** `next`'s `start_task` and
  `run_review` payloads include the resolved model: implementation from
  the task's derived risk (`models.implementation.default` vs
  `.highRisk`), review always `models.review`. The controller passes the
  model into the subagent dispatch.
- **Merge surface.** `merge.surface: "pr"` (repo default, chosen at
  init): worker branches are pushed, one PR per task targets the epic
  branch, reviewer findings are recorded in `state.json` and mirrored as
  PR comments, the fix loop is PR pushes, `wddctl merge` operates via
  `gh`. `"local"`: today's offline loop — findings in state only, local
  merges. The enforced review logic (P1/P2 block, SHA-pinned evidence,
  re-review after any commit) is identical on both surfaces; the PR is a
  projection of state, never the source of truth.
- **Human-merge mode is first-class.** With `merge.mode: "human"`
  (enterprise, where branch protection forbids agent merges): the agent
  review loop runs before human review is requested — the reviewer
  subagent cleans the PR until no P1/P2 remains, then the task reaches
  `merge_ready` and the PR is marked ready for review. `wddctl monitor`
  ticks observe the human merge and record it; `next` reports "awaiting
  human merge: TASK-NNN (PR #NN)". Agent findings and human PR approvals
  are tracked separately; merge detection reconciles both.

Scheduling is unchanged: dynamic per-task admission (dependencies +
conflict domains + `maxConcurrent`), no wave barriers. `plan preview`
remains the wave-shaped projection used at approval time.

## 6. Finalize phase

When the last task merges, `next` emits scope-level actions instead of
going empty:

1. `final_review` — a reviewer (review model) assesses the epic branch as
   a whole against `.wdd/spec.md`: acceptance criteria met, integration
   coherence, no orphaned partial work. P1/P2/P3 with the same blocking
   semantics.
2. `final_verification` — full verification command on the epic branch.
3. `prepare_handoff` — push the epic branch and open the epic→target PR
   with a generated summary (scope, tasks, evidence). The final merge is
   human-owned; `wddctl` refuses to perform it. The scope reaches
   `delivered` when the merge is observed.

## 7. Skills and docs

- Every skill opens with the hard rule: the agent runs `wddctl` itself;
  presenting a command to the user instead of executing it is a protocol
  violation. Exceptions: the human-owned final merge and anything
  `merge.mode: human` reserves for people.
- Skill set: `wave-driven-development` (router; first line routes fresh
  repos to `wddctl init`), `wdd-setup` (init + one-round questions +
  ratify; replaces `wdd-constitution`), `wdd-plan` (the intake flow, §3),
  `wdd-run`, `wdd-worker` (with TDD), `wdd-review`, `wdd-status`.
- `docs/workflow.md` is rewritten agent-first; the
  human-at-the-terminal path becomes an appendix.

## 8. Testing

- Unit tests in the existing suite for: setup-phase `next` actions,
  config loading/validation/drift detection, derived risk, `plan lint`
  rules, finalize actions, merge-surface dispatch payloads.
- End-to-end test for `init` on a scratch repo: init → `config set` →
  ratify → `plan apply` → `next` reaches `start_task`.
- PR-surface functions tested against a stub `gh` (no live GitHub in
  CI), mirroring the enterprise `integration/fake-jira` pattern.

## 9. Migration

`wddctl migrate` learns one more step: an existing `.wdd/` with a
JSON-block constitution has its machine values extracted into
`config.json`, the constitution rewritten prose-only, and ratification
invalidated (the fingerprint changed — deliberately, so the user sees the
new split once and re-ratifies).

## Non-goals this round

- No Jira provider implementation (`taskProvider` schema stub only).
- No changes to admission-engine semantics; no wave barriers or
  checkpoint groups (revisit only on demonstrated need).
- No consolidation of the two repositories yet; this design keeps the
  seams (`taskProvider`, `merge.surface`, `merge.mode`) so the enterprise
  port becomes configuration plus a Jira adapter.

## Configuration matrix

| Use case | surface | mode | provider |
| --- | --- | --- | --- |
| Solo, small epic | local | controller | local |
| Solo, default | pr | controller | local |
| Enterprise team | pr | human | jira (later) |
