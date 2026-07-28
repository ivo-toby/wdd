---
name: wdd-review
description: Act as a WDD reviewer for a task's diff — what to check, how to classify P1/P2/P3 findings, and how to record them with wddctl review record. Use when a WDD controller dispatches a review for a task (high-risk tasks under risk_based policy, or every task under an always policy).
---

# WDD Review

You run every `wddctl` command in this skill yourself. Presenting a command
to the user instead of executing it is a protocol violation.

You have been dispatched to review one task's diff against its brief. Your
job is to find problems and classify them — not to fix them.

## What to check

- The diff actually delivers the task's stated objective, and nothing more:
  flag scope creep the same as a missing requirement.
- Correctness against the task's brief and any adjacent contracts it must
  honor.
- Test coverage for the behavior the task claims to add or change.
- Security and data-handling issues, proportional to what the task touches
  (weight this heavily on high-risk tasks — auth, persistence, migrations,
  public APIs).
- The diff stays inside the task's declared conflict domains. A change
  outside them is a signal the plan's domains were wrong, not just a style
  note.
- Whether a discovery in this diff belongs in shared context (tell the
  worker to `wddctl note` it if so — you don't queue it yourself).

## Classifying findings

- **P1** — breaks correctness, security, or the stated objective. Blocks
  merge.
- **P2** — real problem short of P1 (missing test, weak error handling,
  meaningful maintainability risk). Blocks merge.
- **P3** — worth mentioning, not worth blocking on (style, minor
  polish, optional follow-up).

Don't inflate severity to force attention, and don't downgrade to avoid
blocking — the controller routes P1/P2 to a fix worker automatically, so an
accurate severity is what keeps that routing meaningful.

## Recording

```
wddctl review record --task ID --reviewer NAME --findings '[...]'
```

Findings are a JSON array:

```json
[{"severity": "P1", "summary": "...", "file": "src/a.py", "line": 42}]
```

`severity` must be `P1`, `P2`, or `P3`; `summary` is required. Pass `[]` or
omit `--findings` entirely for a clean review — don't invent a placeholder
finding to signal "reviewed."

On the `pr` merge surface, the controller mirrors what you just recorded as
a comment on the task's PR — that's the controller's job, not yours; your
contract here is unchanged either way.

## Final review (the whole epic branch)

Once every task in a scope is `done` or `cancelled`, the controller
dispatches a different kind of review under this same skill: not one
task's diff against its brief, but the **whole epic branch** against
`.wdd/spec.md`'s acceptance criteria.

- **What to diff.** The epic branch's head against its merge-base with the
  target branch — everything that would actually land if the epic branch
  merged right now, no more and no less: `git diff $(git merge-base
  <targetBranch> <epicBranch>)...<epicBranch>` (equivalently, `git diff
  <targetBranch>...<epicBranch>`). Not the target branch's current tip —
  if the target has moved since the epic branch was cut, that drift isn't
  this review's concern.
- **What to check it against.** `.wdd/spec.md`'s Acceptance criteria
  section, not a task brief — every criterion the scope was approved
  against at intake (see `docs/workflow.md`'s "Intake" section) should be
  demonstrably met by the diff as a whole. Also watch for what no single
  task's review could catch: integration coherence across tasks, and
  orphaned partial work (a criterion two tasks were each supposed to
  half-satisfy, where neither diff alone would have looked incomplete).
- **Classification.** Same P1/P2/P3 semantics as task-level review above —
  P1 and P2 block, P3 doesn't inflate or downgrade to force or avoid a
  block.
- **Recording.** Same as task-level: you run it yourself, this skill's
  hard rule applies file-wide.

  ```
  wddctl finalize review record --reviewer NAME --findings '[...]'
  ```

  A P1/P2 finding sets the final-review outcome to `blocked`, which routes
  the controller to `assign_final_fixes` instead of `final_verification` —
  the scope-level mirror of `needs_fixes` above. Pass `[]` or omit
  `--findings` for a clean review, exactly as at task level.
