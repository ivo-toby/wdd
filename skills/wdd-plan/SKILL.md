---
name: wdd-plan
description: Intake flow from a spec (or other source documents) to an applied, approved WDD scope — ingest, clarify, agree on .wdd/spec.md, decompose into plan.json, present for approval, apply. Use when the user brings work ("let's build X", a spec, an epic doc), before running wddctl plan apply, or when reshaping an existing scope's task graph.
---

# WDD Plan

You run every `wddctl` command in this skill yourself. Presenting a command
to the user instead of executing it is a protocol violation.

Turn a body of work into an agreed spec, a `.wdd/plan.json` plus one brief
per task under `.wdd/tasks/`, and an applied, approved scope. This is the
only planning input; `wddctl plan apply` creates or updates the scope from
it.

## Ingest

Read the documents and context the user brings — a spec, an epic doc, a
design doc, a linked issue. If no spec exists, say so and stop. WDD assumes
specced work; writing the spec is the engineer's job, not this skill's.

## Challenge and clarify

Push back on gaps, contradictions, and scope ambiguity before agreeing to
anything. Ask the user in compact rounds — batch every open question into
one message, never trickle them one at a time.

## Agree: `.wdd/spec.md`

Once the gaps are closed, write the agreed understanding to `.wdd/spec.md`
with exactly these four sections: Goal, In scope, Out of scope, Acceptance
criteria. The finalize phase (phase 5, upcoming) will review the epic
branch against this file, so acceptance criteria must be checkable — not
"works well," but a condition a reviewer can confirm true or false from the
diff.

Skeleton:

```markdown
# Spec: <short name>

## Goal

One paragraph: what this delivers and why.

## In scope

- Bullet the concrete surface this scope covers.

## Out of scope

- Bullet what a reader might assume is included but isn't.

## Acceptance criteria

- [ ] Checkable condition a reviewer can verify from the diff.
- [ ] Another.
```

## Decompose

### Splitting into tasks

A task is one independently executable unit: one worker, one branch, one
diff, one merge. Split along natural seams (a module, a layer, a contract)
rather than by line count. A task that can't be described with a single
clear objective and a bounded file set is too big — split it. A task that
only makes sense alongside another is too small — merge them.

When several tasks produce components that a shared entrypoint, registry,
or wiring file must consume (a server registering tools, a router mounting
handlers, an index exporting modules), add an explicit **integration task**
that `dependsOn` all of the producers and owns that file's conflict domain.
Without it, the isolation that prevents merge conflicts also guarantees the
wiring never happens: an early task writes the registry once, later tasks
can't touch it, and the scope ships components that exist but are never
reachable. A plan whose graph has no such sink for its shared surfaces is
structurally incomplete, whatever lint says.

### Conflict domains — the single most important judgment

`conflictDomains` are the paths/globs a task touches. Two tasks sharing a
domain are never admitted concurrently — this is enforced at task start, not
just advised. Get the granularity wrong in either direction and the system
fails differently:

- **Too coarse** (e.g. `src/**` for every task) serializes everything; you
  paid for parallel workers and got a queue.
- **Too narrow** (omitting a file a task actually touches) lets two workers
  write the same file at once, and one's work silently disappears at merge.

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
overriding whatever the plan file said. This is upward-only: it can raise a
task's risk, never lower it, so writing `"risk": "high"` in the plan for a
task you know is dangerous is still meaningful even when riskRules exist —
you just don't need to write `"risk": "high"` for every task under a path a
rule already covers. In practice this means: once a scope's `riskRules`
exist, stop hand-assigning `"risk": "high"` to every auth/persistence/API
task by inspection — let the rule do it, and reserve an explicit `"high"` in
the plan for a task you believe is risky for reasons no path-based rule would
catch. If the scope has no `riskRules` yet, mark a task `"risk": "high"`
directly when it touches auth, security, data persistence, migrations, a
public API/contract, or generated code — anything where a bad merge is
expensive to unwind — and consider proposing a `riskRules` entry for the
scope's config so future plans don't have to repeat the judgment per task.

Destructiveness is not the only risk axis. A task that transcribes an
external contract — an API client against a named reference implementation,
a protocol encoder, a schema mirroring someone else's — is high-risk even
though it destroys nothing: fabricated endpoints and invented field names
look exactly like real ones, pass type-checking, and sail through unit
tests that only exercise the fabrication. Mark contract-transcription tasks
`"risk": "high"` (or add a riskRules pattern for the client/adapter paths)
so a reviewer compares the code against the reference. And when the
implementation model is small or cheap, say so at approval time and
recommend `reviewPolicy: always` for the scope — risk-based review assumes
the worker's unreviewed output is usually right, which is precisely what a
small model does not guarantee.

Under `reviewPolicy: risk_based` (the default), only high-risk tasks — plan-
declared or rule-derived — get a separate reviewer pass; `always` reviews
everything, `none` reviews nothing. Set this per scope based on how much you
trust the worker model and how expensive mistakes are here.

### maxConcurrent

Bounds how many tasks are active at once — this is what actually limits
rebase churn, not task count. Set it low (2-3) when tasks share nearby code
and rebases are likely painful even without a hard conflict-domain overlap;
raise it when tasks are genuinely isolated.

Two artifacts, two formats — do not mix them up. `plan.json` is JSON for
the machine. Each task's brief (the file at its `specPath`, under
`.wdd/tasks/`) is **Markdown prose for the worker** — objective, scope,
non-scope, verification — never JSON. A worker exercises judgment against
sentences, not a data blob. See `templates/plan.json` and
`templates/task.md` for the exact shapes.

## Present for approval

Show the user, together:

- The plan summary (tasks, dependencies, conflict domains, risk).
- `wddctl plan preview --plan plan.json` — the projected round-by-round
  admission schedule. Read it in both directions: a long thin schedule
  (many rounds of one task) means conflict domains are too coarse and the
  work is serialized; a single fat round means domains are too narrow or
  missing, and workers will collide on files nobody declared. Caveat:
  unlike `plan lint` and `plan apply`, `preview --plan` reads the plan file
  as-is — it does not overlay `config.json` defaults or risk rules, so its
  projection can differ from what actually lands after a real apply.
- `wddctl plan lint --plan plan.json` findings — it overlays the same
  config defaults and riskRules `plan apply` will, so it sees exactly what
  apply would see: a plan that's effectively serialized (`serialized_plan`),
  every task sharing one risk level (`uniform_risk`), a task's
  `conflictDomains` enumerated file-by-file where a glob would do
  (`enumerated_domains`), one domain coarse enough to serialize three or
  more other tasks (`coarse_domain`), and a task whose brief is missing or
  effectively empty (`missing_brief`). Lint is advisory — it never blocks
  `plan apply` unless you pass `--strict` — so address every warning, or, if
  a warning is a deliberate choice (two tasks genuinely must share a file; a
  scope really is all high-risk), say so explicitly to the user rather than
  silently applying over it.

Run `wddctl plan apply --plan plan.json --repo . --dry-run` first to check
for structural errors without writing state. Note `plan apply` is
re-runnable: it adds/removes/updates tasks, but refuses to edit or remove a
task that has already started.

On explicit user approval, apply and record it:

```sh
wddctl plan apply --plan plan.json --repo . --approved-by <name>
```

While presenting, reconcile the spec's acceptance criteria with the scope's
`verification.commands` (`wddctl config get verification.commands`): any
criterion a command can check — the build passes, the binary starts, a
smoke invocation answers — belongs in the verification gate, not just in
prose. A gate that only runs unit tests will happily pass a package that
does not start. Propose the amendment to the user before applying; config
changes after ratification go through `wddctl constitution amend`.

Never apply an unapproved plan. `<name>` is the human who approved the plan,
not the agent.

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
