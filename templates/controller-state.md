---
id: WDD-0001-CONTROLLER
kind: controller_state
epic: WDD-0001
active_wave: 1
status: in_progress
updated_at: YYYY-MM-DD
---

# Controller State: WDD-0001

## Controller Rule

The controller manages waves, subagents, PRs, review feedback, verification evidence, and reconciliation. The controller does not implement code.

## Active Wave

Wave: 1

| Ticket | Brief | Branch | PR | Gate | Verification |
|--------|-------|--------|----|------|--------------|
| WDD-0001-T001 | briefs/WDD-0001-T001-example-ticket.md | codex/wdd-0001-t001-example-ticket | None | no_pr | Not run |

## Gate Definitions

- no_pr: implementation has not produced a PR or equivalent patch.
- needs_review: PR exists and high-rigor review is not complete.
- reviewing: review is active.
- needs_fixes: P1/P2 feedback exists.
- merge_ready: verification and P1/P2 gates are clear.
- merged: ticket is merged and cleaned up.
- blocked: controller cannot progress without user or external input.

## Event Log

- YYYY-MM-DD: Wave 1 started.

