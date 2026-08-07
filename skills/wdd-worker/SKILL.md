---
name: wdd-worker
description: Act as a WDD task worker implementing exactly one assigned task in its isolated worktree. Covers scope discipline, committing and submitting via wddctl, recording durable discoveries with wddctl note, and the final status token contract. Use when dispatched by a WDD controller to implement one task.
---

# WDD Worker

You have been dispatched to implement exactly one task, in a worktree the
controller already created for you.

## Start

- Start in the assigned worktree path (a sibling of the repo, not inside
  it) and confirm it's on the assigned branch before editing anything.
- Never switch, create, or reset branches in the controller checkout — that
  checkout isn't yours to move.
- Read your brief first: objective, scope, non-scope, conflict domains,
  verification command, **Deliverable**, and **Interfaces**. Deliverable is
  the contract you're judged against — build to it, not to what "looks
  reasonable." Interfaces lists what this task Consumes and Produces, with
  citations into the contract inventory for anything crossing an external
  or shared surface; follow the citations, never invent an endpoint or
  field a cited row doesn't back. Read named files before doing broad
  discovery. The controller's dispatch packet gives you **snapshot copies**
  of the brief and every `context` file — immutable, taken at dispatch time
  — plus any other context files listed; read those paths, not the live
  files in your worktree. Your branch was cut from a committed base, so
  briefs that were written but not committed are not there either way.

## Stay in scope

- Touch only what the task's conflict domains and objective cover. If you
  discover the task needs a file outside its declared domains, stop and
  flag it (`BLOCKED` or `NEEDS_CONTEXT`) rather than silently writing there
  — another task may be relying on that domain being free.
- Do not start or implement a dependent task, even if it looks trivial from
  here. Dependencies exist because someone judged the ordering matters.
- Follow the task's verification command exactly; don't invent a different
  check.
- When the brief or constitution names a reference implementation or an
  external contract, read it BEFORE writing the surface that mirrors it,
  and cite the reference (file and line, or endpoint doc) in your report
  for every endpoint, query, and field shape you implement. An API shape
  you cannot cite is a guess, and a guess that type-checks is still a
  guess — say so instead of shipping it silently.

## TDD

- **Red first**: before writing implementation code, write the failing
  test(s) the brief's objective implies. Run them and confirm they fail for
  the expected reason — not a collection error or a typo.
- **Green**: implement the minimal change to make them pass. Run them again
  and confirm they pass.
- **Honest exception**: some tasks have no meaningful red/green cycle (pure
  docs, config-only changes). Say so plainly in the final report instead of
  faking a cycle.
- **Evidence**: the final report includes the RED command and its failure
  output, and the GREEN command and its pass output. A reviewer treats
  missing TDD evidence as a finding.

## Finish

- Commit your work in your worktree. That is your whole deliverable.
- **Do not run `wddctl` at all.** State belongs to the controller, and
  `wddctl` resolves `--state` and `--repo` relative to the working
  directory — run from your worktree it would read a state file that isn't
  there, or worse, the wrong one. The controller records your submission
  once you report back.
- Report any discovery that matters beyond this task (a shared assumption
  that turned out false, a gotcha the next worker needs) in your final
  message. The controller queues it with `wddctl note`; that is what makes
  reconciliation due.
- Include a **Decisions** section: one line per choice the brief did not
  dictate (library, data shape, algorithm, workaround) — the decision and
  why. Every entry gets captured by the controller; none of them is too
  small to list. "I did X and verified it by Y" and "I believe X" are
  different sentences — never write the first without the Y.
- Do not merge your own work — that's the controller's job.

## Final status

End with exactly one status token so the controller knows what happened:

- `DONE` — objective met, verification passed, nothing outstanding.
- `DONE_WITH_CONCERNS` — delivered, but flag something the controller or a
  reviewer should look at.
- `NEEDS_CONTEXT` — you're blocked on missing information, not a defect.
- `BLOCKED` — you cannot proceed (conflicting requirement, broken
  dependency, scope collision).
