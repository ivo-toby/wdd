# Spec review round 1 — epic-park-resume

Provenance: authored by Claude (Fable 5); adversarial review by Codex
(codex-cli, default model), dispatched with the human's standing
approval for this spec ("go for it" incl. the Codex round). 7 findings
(3 P1, 4 P2), verbatim in the session record; dispositions:

| # | Sev | Finding (compressed) | Disposition |
|---|-----|----------------------|-------------|
| 1 | P1 | task/<ID> branches not scope-qualified; parked checkout blocks/leaks into new epic | closed — park releases worktrees (refuses on dirty); branch-exists-without-lease guard at worktree creation names the parked owner |
| 2 | P1 | monitoring omitted from swap set | closed — monitoring travels with the park (archive precedent) |
| 3 | P1 | park mid-dispatch violates "fully inert" | closed — documented as bounded: runner keeps running, recording verbs refuse post-park (unknown task); skill says don't park mid-dispatch |
| 4 | P2 | doctor reports parked dirs as orphans | closed — epic_orphans excludes parked slugs |
| 5 | P2 | config get --epic silently reads global while parked | closed — refuses with parked slugs + resume named |
| 6 | P2 | resume chokepoint evaluates pre-swap state ambiguously | closed — pinned: governance check only; epic gates deliberately fire on next verb post-resume |
| 7 | P2 | v6-additive schema claim impossible under strict validate_state | closed — v7 bump, parked required-defaulting {}, migrate v6→v7 pure bump |
