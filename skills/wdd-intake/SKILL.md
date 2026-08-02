---
name: wdd-intake
description: Intake ladder from a spec through research to an agreed design — the front-half conversation before wdd-plan decomposes anything. Use when the user brings a feature or spec ("let's build X", a spec/epic doc, a linked issue), when `wddctl next` emits `agree_spec`, `research`, or `agree_design`, or when rungs are pending before plan apply can run.
---

# WDD Intake

You run every `wddctl` command in this skill yourself. Presenting a command
to the user instead of executing it is a protocol violation.

This skill owns the three rungs `wddctl next` walks between ratification and
`plan apply` — spec, research, design — as ONE conversation. `next` emits
one rung at a time (`agree_spec`, `research`, `agree_design`), names the
recording command, and points at this skill for judgment; you supply the
judgment, `wddctl` owns the bookkeeping. Clarification is not a fourth rung
— it happens inline at each approval. Batch every open question into one
compact round per rung; never trickle questions one at a time.

## Ingest

Read whatever the user brings — a spec, an epic doc, a design doc, a linked
issue. If nothing exists yet, say so and stop: WDD assumes specced work,
and writing the first draft is the engineer's job, not this skill's. Push
back on gaps, contradictions, and scope ambiguity before agreeing to
anything — don't write `spec.md` from a vague ask.

## Rung 1: Spec (`wddctl next` → `agree_spec`)

Once the gaps are closed, write the agreed understanding to `.wdd/spec.md`
with exactly these four sections: Goal, In scope, Out of scope, Acceptance
criteria. Finalize's `final_review` walks the criteria by number, so each
one must be checkable from the diff — not "works well."

Skeleton:

```markdown
# Spec: <short name>

## Goal

One paragraph: what this delivers and why.

## In scope

- Bullet the concrete surface this scope covers.

## Out of scope

- Bullet what a reader might assume is included but isn't.

## Acceptance criteria

- [ ] AC-1: Checkable condition a reviewer can verify from the diff.
- [ ] AC-2: Another.
```

Numbering is not decoration: `wddctl intake spec --approved-by NAME` refuses
the whole record if any checklist line under "Acceptance criteria" doesn't
match `- [ ] AC-<n>: ...`, or if the numbers aren't unique and contiguous
from 1. Get the user's explicit sign-off on the text, then record:

```sh
wddctl intake spec --approved-by NAME
```

Close the rung with sign-off and the offer to continue: "Spec's agreed.
Should I start research, or go straight to design?"

## Rung 2: Research (`wddctl next` → `research`)

Research applies when the scope depends on something you'd otherwise
fabricate from memory: an external contract, an unfamiliar API, a named
reference implementation, protocol docs to transcribe. Skip it for scopes
that only touch code and conventions already in this repo.

When it applies, the canonical artifact is the contract inventory,
conventionally `.wdd/shared-context/contract-inventory.md`: one row per
operation, built by actually READING the reference (source file, generated
client, protocol doc) — never by recalling what an API "probably" looks
like.

```markdown
| Operation      | Method/Path or shape       | Citation                  |
| -------------- | --------------------------- | -------------------------- |
| Create order   | `POST /v1/orders`           | `reference/api.md:142`     |
| Order status   | `status: "pending"\|"paid"` | `reference/models.py:88`   |
```

Record completed research, one `--artifacts` path per file you built or
relied on (paths are `.wdd`-relative; each must exist, be a regular file,
and be non-empty — validated at record time):

```sh
wddctl intake research --done --by NAME --artifacts shared-context/contract-inventory.md
```

Or, when no external contract applies, record an explicit, attributed skip
— silence is not an option, and neither form is anonymous:

```sh
wddctl intake research --skip --by NAME --reason "no external contract; pure internal refactor"
```

Close the rung with sign-off and the offer to continue: "Research is done
[or: skipped, because <reason>]. Ready to agree the design?"

## Rung 3: Design (`wddctl next` → `agree_design`)

Write `.wdd/design.md` — one page with teeth, four sections. If it reads
like a narrative, it is wrong; every line should be structure a planner or
reviewer can cite.

Skeleton:

```markdown
# Design: <short name>

## Components

- The units this scope produces.

## Interfaces

- `<Component>` — Consumes: ... / Produces: ...

## Integration surfaces

- `path/glob` — owned by: <responsibility>

## Epic deliverable

What observably runs when the scope is done, and the command that proves
it.
```

Every integration surface you list here is what `wdd-plan`'s lint later
checks tasks actually cover (`unowned_surface`) — name a real responsibility
per surface, not a task ID (tasks don't exist yet).

The epic deliverable needs a command that proves it, and it isn't optional:
`wddctl intake design` refuses without one. Get sign-off on the text, then
record both the approval and the deliverable command together:

```sh
wddctl intake design --approved-by NAME --deliverable-command "npm test && npm start -- --smoke"
```

That command is fingerprinted with the design record and later runs inside
finalize's `final_verification`, alongside the ratified global
`verification.commands` — never mutate global config to smuggle it in.

Close the rung with sign-off, then hand off to decomposition: "Design's
approved. Want me to decompose it into tasks?" — that's `wdd-plan`.

## Fingerprint and cascade doctrine

Every rung approval binds to the approved bytes: recording a rung hashes
the artifact at that moment. Edit the file afterward and the approval no
longer covers what's on disk — an approval of text that has since changed
approves nothing.

This shows up in two places:

- **Before `plan apply`**: `next` re-hashes each recorded rung. A mismatch
  re-emits that rung (marked stale) for re-approval, and `plan apply`
  refuses until it's clean.
- **During execution**: the same edit surfaces as an `intake_drift`
  blocker — `next`'s actions go empty, and the blocker names the stale rung
  and the exact commands to fix it: re-run `wddctl intake <rung> ...` to
  re-approve, then `wddctl plan apply --approved-by NAME` to re-stamp
  (a pure re-stamp when the plan file itself is unchanged — one command,
  not a re-plan).

The ladder also cascades downstream: re-approving a rung invalidates every
rung after it, because those were approved against upstream bytes that just
moved underneath them. A new spec approval clears research, design, and the
plan's approval; a new research record clears design and the plan's
approval; a new design approval clears the plan's approval. Walk the
cleared rungs again in order — that's the whole remedy.

Never treat the cascade or the drift blocker as an error to route around.
It's the system refusing to run on foundations nobody re-approved; the fix
is always sign-off, not a workaround.

## Scope rollover

Once a scope reaches `delivered`, the ladder is closed — intake verbs
refuse until you retire the scope:

```sh
wddctl scope archive --repo .
```

This moves the delivered scope's records (scope, tasks, intake, finalize)
into `.wdd/archive/<scope-id>.json` and resets state for the next one:
governance stays ratified, but the intake ladder restarts from `agree_spec`
and nothing scope-specific — including the deliverable command — carries
forward. Each scope earns its own spec, research, and design from scratch.

## Done when

- `wddctl next` no longer emits `agree_spec`, `research`, or `agree_design`
  (it emits `plan` instead).
- `wddctl intake status` shows all applicable rungs recorded and no drift.

Close with the handoff named above: design approval hands to `wdd-plan`
("Design's approved. Want me to decompose it into tasks?"). Don't make the
user ask what comes next.
