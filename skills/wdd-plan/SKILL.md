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

Mark a task `"risk": "high"` when it touches auth, security, data
persistence, migrations, a public API/contract, or generated code — anything
where a bad merge is expensive to unwind. Under `reviewPolicy: risk_based`
(the default), only high-risk tasks get a separate reviewer pass; `always`
reviews everything, `none` reviews nothing. Set this per scope based on how
much you trust the worker model and how expensive mistakes are here.

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

See `templates/plan.json` and `templates/task.md` for the exact shapes.
