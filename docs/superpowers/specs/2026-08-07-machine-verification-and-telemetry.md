# Machine-Executed Verification and Dispatch Observability

Status: draft for review.
Baseline: main after the adversarial-review phase (v0.3.x).

## Problem

1. **Verification evidence is still agent-reported.** The controller runs
   the verification commands and then *tells* `wddctl` what happened. The
   behavioral contract forbids recording unobserved results — but that is
   a rule, and we have watched an agent break exactly this rule in the
   wild (review/verification records stamped `passed` for runs that never
   happened). Every benchmark learning points the same way: move the
   mechanical step into the machine and the fabrication class dies.
2. **Half our dispatches are unauditable.** Runner dispatches persist
   their exact prompt packet (`.wdd/dispatch/*.prompt`); harness-native
   subagent dispatches leave nothing on disk. When a worker or reviewer
   misbehaves, there is no record of what it was actually told.

## 1. `--run`: wddctl executes verification itself

### Task verification

```
wddctl verify record --task ID --run
```

With `--run`, `wddctl` resolves the effective verification commands from
the **admission snapshot** (resolve-once doctrine, unchanged), executes
them itself in the task's worktree, and records what it observed. The
agent never supplies a status. Mutually exclusive with `--status`/
`--results`/`--command` — supplying both is a refusal, not a merge.

Execution semantics, explicit:

- Commands run sequentially via the platform shell (`sh -c`) in the
  task's worktree, environment inherited. Shell execution is safe by
  construction, not by sanitization: the commands are ratified,
  fingerprint-bound config bytes (governance + configure approvals) — an
  attacker who can edit them trips drift before `--run` will execute
  anything.
- Per-command timeout: `verification.timeoutSeconds` (new global +
  epic-overridable config key, default 600; joins the overlay allowlist
  and the `taskVerification`/`finalVerification` projections — a timeout
  change IS a verification-relevant change).
- First failure stops the sequence (remaining commands recorded as
  `skipped`, overall `failed`) — matching the existing all-must-pass
  doctrine.
- Captured per command: `{command, status, exitCode, durationMs,
  outputSha256, tail}` — tail bounded to 4KB (the dispatch idiom), full
  interleaved output written to `.wdd/dispatch/verify-<task>-<n>.log`
  (transient scratch, 0600, attempt-numbered, gitignored — logs are not
  durable state; the digest in evidence is).

### Final verification

```
wddctl finalize verify --run
```

Same semantics at scope level: executes the ratified global
`verification.commands` **plus** the intake-recorded deliverable
command, in that exact order, from the repository root on the current
epic-branch head. Records the ordered results array itself; refuses
`--results` alongside `--run`.

### Provenance on the evidence

Every verification record (task and final) gains
`execution: "wddctl" | "reported"`:

- `--run` records `"wddctl"`.
- The existing agent-reported paths remain legal (some verifications
  genuinely cannot run on this machine — a device farm, a staging
  deploy) and record `"reported"`.
- Gates treat both as valid — this phase changes provenance visibility,
  not gate outcomes. `status`/`render` surface the field; the skills
  make `--run` the default obligation ("`--run` unless the command
  cannot run here, and say why in the conversation when it can't"), and
  the reviewer packet for final review names any `"reported"` evidence
  as a review target.
- Schema: the field is required on new records, absent-means-`"reported"`
  for records predating this phase (no migration; validation accepts
  both shapes, pinned by test).

## 2. Dispatch packet parity and telemetry

### Packet capture (prose + one convention)

The controller writes every harness-native dispatch packet it composes —
worker and reviewer both — to the task's attempt snapshot dir as
`packet-<role>-<n>.md` before dispatching. This mirrors what runner
dispatch already persists automatically; after this phase, *every*
dispatch's exact instructions are on disk. Skills obligation
(`wdd-run`'s dispatch-packet section) plus documentation in
`artifact-schema.md`; no CLI change. The attempt dir's read-only file
policy applies after write (0400, like the snapshot copies).

### Telemetry (optional, additive)

Review and verification records accept an optional `telemetry` object:
`{model, durationMs, tokens}` — all fields optional, recorded when the
controller knows them (runner dispatches know duration and model; some
harnesses expose token counts; many don't). No gate reads telemetry; it
exists for `render`/`status` and post-hoc analysis. Absent stays legal
forever — telemetry is observability, not evidence, and must never
become a gate input (stated as doctrine so nobody "improves" it into
one later).

## Non-goals

- No UI/viewer, no cost aggregation, no dashboards — state, logs, and
  `render` carry the data; presentation is out of scope.
- No JSON inter-phase envelopes for workers — prose briefs stay (a
  deliberate, benchmark-backed choice).
- No sandboxing of verification commands beyond what the shell/user
  already provides: commands are ratified config, and the operator's
  machine is the operator's business (runner-command precedent).
- No retry/flake handling in `--run` — one sequential pass, observed
  results, rerun by running again.
