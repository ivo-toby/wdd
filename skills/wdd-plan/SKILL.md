---
name: wdd-plan
description: Decompose an agreed WDD scope into plan.json plus per-task briefs, present for approval, lint, and apply. Use once the intake ladder is complete — wddctl next stops emitting agree_spec/research/agree_design and emits a plan action instead — before running wddctl plan apply, or when reshaping an existing scope's task graph. If spec.md or design.md aren't agreed yet, use wdd-intake first.
---

# WDD Plan

You run every `wddctl` command in this skill yourself. Presenting a command
to the user instead of executing it is a protocol violation.

Turn an agreed epic — its `spec.md`, and `design.md` when the ladder
called for one — into a plan.json plus one brief per task under the
epic's `tasks/`, and an applied, approved scope. The scope id is always
`SCOPE-<epic slug>` (derived; a plan naming anything else is refused),
and `plan apply` mirrors the applied plan into the epic's directory so
the archive stays self-contained. Brief and context refs stay
namespace-relative (`tasks/T1.md`, `shared-context/...`) — never write
an `epics/` prefix.

## Entry precondition

This skill decomposes; it does not agree anything. Check `wddctl next`
first: once the intake ladder is complete (or the scope predates the ladder
and is legacy-exempt), `next` emits a `plan` action naming this skill. If it
instead emits `agree_spec`, `research`, or `agree_design`, the ladder isn't
done — hand off to `wdd-intake` and do not improvise a spec or design here.
Read `.wdd/spec.md`, `.wdd/design.md`, and the contract inventory (whichever
exist) before decomposing; they are the only source of truth for what the
plan must cover, not memory.

## Decompose

### Splitting into tasks

A task is one independently executable unit: one worker, one branch, one
diff, one merge. Split along natural seams (a module, a layer, a contract)
rather than by line count. A task that can't be described with a single
clear objective and a bounded file set is too big — split it. A task that
only makes sense alongside another is too small — merge them.

When several tasks produce components that a shared entrypoint, registry, or
wiring file must consume (a server registering tools, a router mounting
handlers, an index exporting modules), add an explicit **integration task**
that `dependsOn` all of the producers and owns that file's conflict domain.
Without it, the isolation that prevents merge conflicts also guarantees the
wiring never happens: an early task writes the registry once, later tasks
can't touch it, and the scope ships components that exist but are never
reachable. A plan graph with no such sink for its shared surfaces is
structurally incomplete, whatever lint says.

### Conflict domains — the single most important judgment

`conflictDomains` are the paths/globs a task touches. Two tasks sharing a
domain are never admitted concurrently — this is enforced at task start, not
just advised. Get the granularity wrong in either direction and the system
fails differently: too coarse (e.g. `src/**` for every task) serializes
everything, so you paid for parallel workers and got a queue; too narrow
(omitting a file a task actually touches) lets two workers write the same
file at once, and one's work silently disappears at merge.

List every file or glob a task will plausibly write, not just its primary
target. When two tasks must touch a shared file (a schema, a shared type),
put that path in both tasks' domains and accept the serialization — that's
correct, not a bug to route around.

### Dependencies

Set `dependsOn` for genuine sequencing needs (task B needs a type task A
defines), not for vague relatedness. Cycles are rejected at plan time. Fewer
dependencies means more admitted concurrently, bounded by `maxConcurrent` and
conflict domains.

### Risk and review

Check `wddctl config get riskRules` before hand-assigning anything. When the
scope has `riskRules` configured — `{"pattern": "<glob>", "risk": "high"}`
entries matched against each task's `conflictDomains` — `plan apply` derives
`risk: high` for any task whose domains overlap a high-risk pattern,
overriding the plan file. This is upward-only: it raises risk, never lowers
it, so an explicit `"risk": "high"` still matters for a task you believe is
dangerous — you just don't need it for every task a rule already covers.
Once `riskRules` exist, stop hand-assigning high risk to every auth/
persistence/API task by inspection; let the rule do it, and reserve
explicit `"high"` for risk no path-based rule would catch. Without
`riskRules` yet, mark a task `"risk": "high"` directly when it touches
auth, security, data persistence, migrations, a public API/contract, or
generated code, and consider proposing a `riskRules` entry so future plans
don't repeat the judgment per task.

Destructiveness is not the only risk axis. A task that transcribes an
external contract — an API client against a named reference implementation,
a protocol encoder, a schema mirroring someone else's — is high-risk even
though it destroys nothing: fabricated endpoints and invented field names
look exactly like real ones, pass type-checking, and sail through unit tests
that only exercise the fabrication. Mark contract-transcription tasks
`"risk": "high"` (or add a riskRules pattern for the client/adapter paths) so
a reviewer compares the code against the contract inventory. When the
implementation model is small or cheap, say so at approval time and
recommend `reviewPolicy: always` — risk-based review assumes the worker's
unreviewed output is usually right, which a small model does not guarantee.

Under `reviewPolicy: risk_based` (the default), only high-risk tasks — plan-
declared or rule-derived — get a separate reviewer pass; `always` reviews
everything, `none` reviews nothing. Set this per scope based on how much you
trust the worker model and how expensive mistakes are here.

### maxConcurrent

Bounds how many tasks are active at once — this is what actually limits
rebase churn, not task count. Set it low (2-3) when tasks share nearby code
and rebases are likely painful even without a hard conflict-domain overlap;
raise it when tasks are genuinely isolated.

### Brief anatomy: Deliverable + Interfaces

Every brief carries two required, linted sections beyond objective/scope/
verification. **Deliverable** — the observable outcome after merge: what
exists, runs, or answers, testable from the diff. This is the reviewer's
first question: not "does the code look reasonable" but "does the diff
produce this." **Interfaces** — Consumes / Produces for this task,
consistent with `design.md`; cite contract-inventory rows for anything
crossing an external or shared surface, don't restate a shape from memory.
An absent or effectively empty section is a lint finding
(`missing_deliverable`, `missing_interfaces`) — see Present for approval.

### Context refs: machine-carried handover

Set each task's `context` field to the `.wdd`-relative files the worker
needs, e.g. `["shared-context/contract-inventory.md#orders", "spec.md#AC-3"]`.
Ref syntax is `<path>[#<anchor>]`: the path must resolve to a regular file
inside `.wdd/` (checked at `plan apply`); the anchor is advisory reading
guidance, not resolved mechanically. List what the worker needs, nothing
more — refs resolve to absolute paths materialized into the worker's
snapshot at dispatch, not restated in the brief prose. A ref shaped
`spec.md#AC-N` is the authoritative mapping from task to numbered
acceptance criterion; a task that discharges none is advisory-flagged
(`missing_criteria` — internal tasks legitimately have none). A scope with
recorded intake artifacts but a task carrying no `context` at all loses
machine-carried handover entirely (`missing_context`).

### The granularity lever

Normative, not a style preference: decompose until each task's residual
judgment fits the configured worker tier.

- A frontier worker gets few, intent-level tasks. A small or cheap worker
  gets many tasks whose briefs approach pseudo-code, with every contract
  cited from the inventory. Judgment doesn't disappear when tasks shrink —
  it moves into planning, where the strongest model pays for it once
  instead of N workers paying at execution (or failing at the capability
  cliff, which costs more than either).
- Classify each task's nature — **mechanical/transcription** vs
  **integration/judgment** — and route per task instead of sizing the whole
  scope for one tier: `"model"` overrides the risk-tiered implementation
  model for that task, `"reviewModel"` overrides its reviewer (precedence:
  task override → risk-tiered config → absent), so the mechanical majority
  runs cheap while the judgment minority gets the strong model, in one
  scope. Review tiers follow the diff's nature, not habit: a mechanical
  diff takes a mid reviewer, a contract or integration diff takes the
  strongest configured (`reviewModel`, or the high tier of `config.models.review`).

Two artifacts, two formats — do not mix them up. `plan.json` is JSON for
the machine. Each task's brief (the file at its `specPath`, under
`.wdd/tasks/`) is **Markdown prose for the worker** — objective,
deliverable, interfaces, scope, non-scope, verification — never JSON. A
worker exercises judgment against sentences, not a data blob.

The exact shapes come from the CLI — there is no `plan init`, and no
template files to hunt for on disk:

```sh
wddctl plan template > plan.json                       # skeleton plan
wddctl plan template --brief > .wdd/tasks/TASK-001.md  # skeleton brief
```

Emit one brief skeleton per task, then replace every placeholder with the
scope's real content. The skeletons are structurally valid v5 documents;
what you add is the judgment.

## Present for approval

Show the user, together:

- The plan summary (tasks, dependencies, conflict domains, risk).
- `wddctl plan preview --plan plan.json` — the projected round-by-round
  admission schedule. Read it in both directions: a long thin schedule
  (many rounds of one task) means conflict domains are too coarse and the
  work is serialized; a single fat round means domains are too narrow or
  missing, and workers will collide on files nobody declared. Caveat:
  unlike lint and apply, `preview --plan` reads the plan file as-is — no
  `config.json` defaults or risk rules overlaid — so its projection can
  differ from what actually lands after a real apply.
- `wddctl plan lint --plan plan.json` findings — it overlays the same
  config defaults and riskRules `plan apply` will, so it sees exactly what
  apply would see. Codes to expect: `serialized_plan`, `uniform_risk`,
  `enumerated_domains`, `coarse_domain` (conflict-domain/risk shape);
  `missing_spec`, `missing_brief`, `nonprose_brief` (file existence and
  prose-not-JSON); `missing_deliverable`, `missing_interfaces` (the brief
  sections above); `missing_context`, `missing_criteria` (context refs and
  the acceptance-criteria mapping); `unowned_surface` (a `design.md`
  "Integration surfaces" entry no task's `conflictDomains` covers); and
  `base_is_target` — `plan lint`-only, surfaced as a warning there, while
  `plan apply` hard-refuses the same condition outright rather than
  emitting it as a finding — `scope.baseRef` must differ from the
  configured `branching.targetBranch`. Lint is advisory — it never blocks
  `plan apply` unless you pass `--strict` — so address every warning, or,
  if a warning is a deliberate choice (two tasks genuinely must share a
  file; a scope really is all high-risk), say so explicitly to the user
  rather than silently applying over it.

Run `wddctl plan apply --plan plan.json --repo . --dry-run` first to check
for structural errors without writing state. Note `plan apply` is
re-runnable: it adds/removes/updates tasks, but refuses to edit or remove a
task that has already started.

On explicit user approval, apply and record it:

```sh
wddctl plan apply --plan plan.json --repo . --approved-by <name>
```

A nonempty diff (tasks or scope changed) requires `--approved-by`; it then
records a composite SHA-256 over the normalized plan, every task's brief
file, and every file named in a `context` ref — the same approved-bytes
doctrine as the intake ladder, said once, plainly. An unchanged re-apply
preserves the recorded approval. Any later edit to a brief or a context
file surfaces as `plan_drift` during execution; the remedy is a fresh
`plan apply --approved-by` — with the plan file itself unchanged, that's a
pure re-stamp, one command, not a re-plan. Never apply an unapproved plan;
`<name>` is the human who approved it, not the agent.

The **independent-oracle rule**: every task's verification must include
at least one check whose assertion content the implementing worker did
not author — a startup smoke, a golden fixture frozen from a
human-confirmed run, a conformance test pinned to a cited contract. A
gate made solely of worker-authored tests is the cheating-agent loop:
broken code validated by broken tests. Name the independent check
explicitly in each brief's Verification section.

While presenting, reconcile the spec's acceptance criteria with the scope's
`verification.commands` (`wddctl config get verification.commands`): any
criterion a command can check — the build passes, the binary starts, a
smoke invocation answers — belongs in the verification gate, not just in
prose. A gate that only runs unit tests will happily pass a package that
does not start. Propose the amendment before applying; config changes after
ratification go through `wddctl constitution amend`.

Flows that can only be proven against a live third party — OAuth against a
real identity provider, payments, rate-limited external APIs — are where
workers drift, precisely because no test can fail there. Those tasks still
get gates: contract-conformance tests that pin every observable artifact
byte-for-byte to the cited contract — the exact URL the code builds, the
exact rewrite a proxy applies to a canned upstream response, the exact
headers sent — each asserting against the inventory row, not against what
the implementation happens to produce. A flow with no live test and no
conformance test is ungated, and ungated is where fabrication survives
review. Name these tests in the brief's Verification section explicitly.

Report the outcome the way the router's "Talking to the user" section
demands: what was agreed, how many tasks, which starts first — not
revisions, scope IDs, or the commands you ran.

## Done when

- The scope is applied (`wddctl plan apply` succeeded).
- Approval is recorded — `scope.approval` is present in state (`by` the
  approving human, `at` a timestamp).
- `wddctl next` shows `start_task` actions.

Close with the handoff: offer to start the work — "Plan's applied. Want me
to run it?" Running the scope is `wdd-run`.
