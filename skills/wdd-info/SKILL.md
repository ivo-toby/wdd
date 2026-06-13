---
name: wdd-info
description: Explain Wave-Driven Development modes and route users to the right next step. Use when the user asks whether to use WDD, how WDD works, which mode to choose, how to reduce ceremony or token use, how to resume existing WDD work, or what skill to run next for micro, lite, standard, or full workflows.
---

# WDD Info

Use this as the read-only front door for WDD.

## Preconditions

- Do not modify files.
- Do not start or plan work unless the user explicitly asks after receiving the
  recommendation.
- Prefer actual `.wdd/` state over guesses.

## Workflow

1. Inspect the request:
   - Scope: one small edit, chunky ticket, epic, migration, refactor, bug
     cluster, or resume/status request.
   - Risk: auth, persistence, data migration, public API, security, generated
     code, CI/deployment, or broad shared architecture.
   - Parallelism: whether 2-5 independent tasks can run safely.
   - User preference for speed, cost, review depth, or ceremony.

2. Inspect local state when useful:
   - If `.wdd/` is missing, say WDD is not initialized and recommend
     `wdd-init-project` only when WDD is appropriate.
   - If `.wdd/work/*/state.json` exists, report active micro-wave work.
   - If `.wdd/epics/*/orchestration.json` exists, report active epic work.
   - If the user asks for full status, hand off to `wdd-status`.

3. Recommend one lane:
   - No WDD: one small edit with little coordination value.
   - `micro`: one bounded ticket or request that can split into 2-5 parallel
     tasks.
   - `lite`: small or medium epic with limited risk.
   - `standard`: normal multi-ticket feature, migration, or refactor.
   - `full`: auth, persistence, public API, data migration, security, risky
     parallel work, or work where auditability matters more than speed.
   - Resume: existing active WDD artifacts already identify the next phase.

4. Explain the tradeoff briefly:
   - Name why the recommended lane fits.
   - Name the ceremony it avoids or preserves.
   - Mention likely token/speed impact when relevant.

5. Give the exact next prompt or skill:
   - No WDD: "Proceed without WDD; make the edit directly."
   - `micro`: `wdd-start-work`.
   - `lite`, `standard`, `full`: `wdd-start-epic`.
   - Existing active wave: `subagent-pr-orchestration`.
   - Completed wave: `wdd-reconcile-wave`.
   - All waves complete: `wdd-epic-validation`.

## Mode Guide

| Mode | Use For | Default Ceremony |
|---|---|---|
| No WDD | One small edit | No WDD artifacts |
| `micro` | Chunky ticket, 2-5 tasks | `brief.md`, task briefs, `state.json` |
| `lite` | Small/medium epic | Compact artifacts, adaptive monitoring, risk-based review |
| `standard` | Normal epic | Full epic shape with token-conscious defaults |
| `full` | High-risk work | Strict review, validation, and monitoring |

## Output

Keep the answer concise. Include:

- Recommendation.
- Why.
- Next command or prompt.
- Existing artifact path when resuming.
