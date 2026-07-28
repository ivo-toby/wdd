---
name: wdd-plan
description: Decompose a body of work into a WDD plan.json — splitting tasks, choosing conflict domains, setting dependencies, risk, and maxConcurrent. Use before running wddctl plan apply, or when reshaping an existing scope's task graph.
---

# WDD Plan

Turn a body of work into `.wdd/plan.json` plus one brief per task under
`.wdd/tasks/`. This is the only planning input; `wddctl plan apply` creates
or updates the scope from it.

## Splitting into tasks

A task is one independently executable unit: one worker, one branch, one
diff, one merge. Split along natural seams (a module, a layer, a contract)
rather than by line count. A task that can't be described with a single
clear objective and a bounded file set is too big — split it. A task that
only makes sense alongside another is too small — merge them.

## Conflict domains — the single most important judgment

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

## Dependencies

Set `dependsOn` for genuine sequencing needs (task B needs a type task A
defines), not for vague relatedness. Cycles are rejected at plan time. Fewer
dependencies means more admitted concurrently, bounded by `maxConcurrent` and
conflict domains.

## Risk and review

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

Under `reviewPolicy: risk_based` (the default), only high-risk tasks — plan-
declared or rule-derived — get a separate reviewer pass; `always` reviews
everything, `none` reviews nothing. Set this per scope based on how much you
trust the worker model and how expensive mistakes are here.

## maxConcurrent

Bounds how many tasks are active at once — this is what actually limits
rebase churn, not task count. Set it low (2-3) when tasks share nearby code
and rebases are likely painful even without a hard conflict-domain overlap;
raise it when tasks are genuinely isolated.

## Sanity-check before applying

Run `wddctl plan preview` to see the projected round-by-round admission
schedule — this is a view of what your conflict domains and dependencies
imply, not a gate you need to satisfy. Read it in both directions: a long thin
schedule (many rounds of one task) means your domains are too coarse and you
have serialized the work; a single fat round means domains are too narrow or
missing, and workers will collide on files nobody declared. Run
`wddctl plan apply --dry-run` before
the real apply to check for structural errors. Note `plan apply` is
re-runnable: it adds/removes/updates tasks, but refuses to edit or remove a
task that has already started.

Then run `wddctl plan lint --plan plan.json` — it overlays the same config
defaults and riskRules `plan apply` will, so it sees exactly what apply would
see, and catches what `preview` doesn't: a plan that's effectively serialized
(`serialized_plan`), every task sharing one risk level (`uniform_risk`), a
task's `conflictDomains` enumerated file-by-file where a glob would do
(`enumerated_domains`), one domain coarse enough to serialize three or more
other tasks (`coarse_domain`), and a task whose brief is missing or
effectively empty (`missing_brief`). Lint is advisory — it never blocks
`plan apply` unless you pass `--strict` — so address every warning it
reports, or, if a warning is a deliberate choice (two tasks genuinely must
share a file; a scope really is all high-risk), say so explicitly in the
plan-approval message rather than silently applying over it.

See `templates/plan.json` and `templates/task.md` for the exact shapes.
