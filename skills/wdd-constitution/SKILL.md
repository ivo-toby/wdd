---
name: wdd-constitution
description: Create, amend, or ratify a WDD project constitution — target/base branch naming, verification commands, review policy, model aliases, and merge policy — using wddctl constitution probe/ratify/status. Use before any WDD execution can begin, or when governance decisions need to change.
---

# WDD Constitution

`.wdd/constitution.md` records the handful of decisions that change how
`wddctl` behaves for this repo. Execution is blocked until it's ratified —
this is deliberate: a controller should never infer branch naming, review
policy, or verification commands on its own.

## Workflow

1. If `.wdd/constitution.md` doesn't exist, create it from
   `templates/constitution.md`.
2. Gather repo evidence before asking the user anything: run
   `wddctl constitution probe --root .` to collect what's inferable
   (existing branch conventions, verification commands, project type).
3. Present the probed and proposed decisions to the user compactly and get
   an explicit decision on each — target branch, base branch naming
   convention, verification command(s), review policy default
   (`always` / `risk_based` / `none`), model aliases if the user has
   preferences, and merge policy (controller merges automatically vs.
   requires human approval). Never silently ratify from inference alone.
4. Fill the constitution's sections with the ratified decisions and check
   `wddctl constitution status` reflects them.
5. Ratify with `wddctl constitution ratify --by NAME --decision-fingerprint SHA`
   once the user has explicitly signed off on the current content — the
   fingerprint ties ratification to the exact text that was approved, so
   don't ratify text the user hasn't actually seen.
6. To amend later, edit the file, get explicit re-approval of what changed,
   then run `wddctl constitution amend --by NAME --decision-fingerprint SHA`.
   `ratify` is the initial act only and is refused once ratified; `amend`
   records the fingerprint it superseded, so governance history stays
   auditable. Treat an amendment as changing behavior immediately for any
   scope not yet started.

## Done when

- Every decision in `templates/constitution.md`'s sections is filled with a
  real value, not a placeholder.
- `wddctl constitution status` reports ratified.
- Any open question the user still needs to answer is stated explicitly.
