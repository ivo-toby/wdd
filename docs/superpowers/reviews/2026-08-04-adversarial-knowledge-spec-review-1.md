# Spec review round 1 — adversarial-review-and-knowledge

Provenance: authored by Claude (Fable 5); adversarially reviewed by Codex
(codex-cli 0.146.0, gpt-5.6-luna default), dispatched by the controller
with the human's explicit per-round approval. 16 findings (6 P1, 10 P2).
Reviewer findings preserved verbatim in the session record; dispositions
below.

| # | Sev | Finding (compressed) | Disposition |
|---|-----|----------------------|-------------|
| 1 | P2 | Runner dispatch has no pre-plan packet | closed — channel is direct CLI exec or human-carried prompt; wddctl dispatch explicitly excluded |
| 2 | P2 | Same-model-across-sessions collision | closed — "different mind" = model identity; provenance line shown at every offer |
| 3 | P2 | Declined round unrecorded/ambiguous | closed — append-only spec-review-log.md; declines are one line there |
| 4 | P2 | Author can rewrite reviewer findings | closed — findings verbatim + immutable; dispositions in separate section |
| 5 | P2 | "Fresh pass over what changed" contradiction | closed — every round is a full pass |
| 6 | P2 | No reopen/upheld; convergence over live P1 | closed — upheld/reopened states; convergence requires P1s closed or human-accepted |
| 7 | P1 | Review artifacts in research record trip cascade | closed — deliberately NOT recorded as rung artifacts; cited by round number |
| 8 | P2 | Independent oracle unprovable from provenance | closed — brief must NAME the independent check; lint out of scope, enforced by reading |
| 9 | P1 | wddctl note doesn't carry decisions to shared-context | closed — decisions pipeline is the living knowledge draft, not note; note stays reconcile-scoped |
| 10 | P1 | Non-notable decisions unciteable at retrospective | closed — ALL Decisions entries copied to the draft at capture; triage only at distillation |
| 11 | P1 | Direct scope archive bypasses "mandatory" offer | closed — offer downgraded to standing guidance surfaced via next judgment; bypass legal; living draft makes it safe |
| 12 | P1 | Citations unstable across archive rename | closed — citations are record coordinates (task/round/AC/event), paths forbidden |
| 13 | P2 | Knowledge file uncommitted/undurable | closed — distillation ends by committing the file |
| 14 | P2 | Sign-off bytes unrecorded | closed — Signed-off-by trailer with content digest (convention, not gate) |
| 15 | P1 | Nothing instructs future epics to read knowledge/ | closed — wdd-intake research rung reads knowledge/ first, cites rows |
| 16 | P2 | "One page" vs "every row" contradiction | closed — completeness wins; summary block when large |
