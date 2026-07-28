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
