---
name: wdd-spec-review
description: Adversarial review of a spec, design, plan, or task brief — a different model (or the human) tries to break the document before work builds on it. Use when the user says "review this spec", "poke holes in this", asks for a security review of a document, or accepts the offer wdd-intake makes at agree_spec/agree_design sign-off.
---

# WDD Spec Review

You run every `wddctl` command in this skill yourself. Presenting a command
to the user instead of executing it is a protocol violation.

The engineering rigor that used to live in code review now lives in the
spec. This skill formalizes the highest-leverage hour in the workflow:
before approval, someone whose job is to *break* the document makes a
full pass over it, and every finding carries a concrete failure scenario.

## The hard rule: never review your own work

The adversarial reviewer must be a **different model identity** than the
document's author. A fresh session of the same model is still self-review
— it converges on the same blind spots — and presenting self-review as
adversarial review is fabricated evidence. If you authored (or
substantially co-wrote) the document, you may coordinate the round; you
may never be its reviewer.

## Choose the reviewer — with the human, every time

Never assume the channel from a previous round, a previous session, or
config. State the document's authoring model (from the round log's
provenance line; if unknown, say "unknown — treat as this model") and
offer the concrete options you detect, then wait for an explicit answer:

- `wddctl config get --epic models.specReview` names the configured
  default candidate, if any. Present it first — it is a pre-filled
  suggestion, never an autopilot: "Config names <X> for spec reviews —
  use it for this round?"
- **A second coding agent** on this machine (an agent CLI, a registered
  runner's command, a model the harness can spawn). You compose a
  self-contained prompt — charter, checklist, document path, grounding
  paths — and either run the CLI yourself (show the exact invocation
  before running) or hand the prompt to the human to paste elsewhere.
  `wddctl dispatch` is NOT the channel: it is task-scoped and needs a
  snapshot that does not exist pre-plan.
- **The human as reviewer** — when no second agent exists, when running
  one isn't allowed here, or when they prefer it. Your job flips to
  assistant: walk them through the checklist section by section, record
  THEIR findings verbatim, and never substitute your judgment for
  theirs.

Also ask which **profile**: general, security, or both (both = one
round, both checklists).

If the human declines review entirely, that is their call: one line in
the round log, move on. Never nag; never silently self-review as a
fallback.

## The reviewer charter (embed verbatim in the prompt)

- Your job is to **break this document**, not improve it. Suggestions
  are out of scope; failures are the deliverable.
- Every finding carries a **concrete failure scenario**: the inputs,
  state, or sequence under which the text produces the wrong outcome.
- Findings are **P1** (implementing as written produces a defect, or it
  cannot be implemented as written) or **P2** (a reader can reasonably
  build the wrong thing; underspecified boundary; untestable
  criterion). No style notes, no praise.
- If you cannot break a section, say what you tried. An empty report
  with no attack log is a non-review.

## Checklist — general profile

1. **Ambiguity lexicon**: every "should", "handle", "appropriate",
   "properly", "as needed", "etc." is a finding unless the sentence
   survives a hostile reading.
2. **Testability per acceptance criterion**: can a reviewer decide
   true/false from the diff or a command's output? Needs-interpretation
   is a P2.
3. **Unstated assumptions**: scale, ordering, idempotency, permissions,
   encoding, time zones, concurrency — what must be true that the text
   never says?
4. **Abuse and scale probes**: anything that sends, spends, deletes, or
   loops gets a bounded-by-what question.
5. **Stateful behavior demands a state table**: more than two states or
   any transition rules in prose → demand a state machine or decision
   table. Models fill unspecified transitions with guesses.
6. **Contract citations**: claims about external systems must cite
   something on disk.
7. **Failure-path parity**: every happy path has a failure path, or the
   error handling was just delegated to the implementing model.

## Checklist — security profile

1. **AuthN/AuthZ per operation**: who may call each endpoint/verb/tool,
   and where does the text say that is checked? No stated caller
   boundary is a P1.
2. **Injection surfaces**: anything concatenated into a query, shell
   command, path, URL, or prompt — escaped/parameterized how?
3. **Secrets and logging**: where credentials live at rest and in
   transit; what may never appear in logs, errors, or evidence.
4. **Isolation boundaries**: tenant/user/workspace separation — enforced
   by what, and what happens when the boundary fails?
5. **Unsafe input handling**: uploads, deserialization, redirects, path
   traversal, SSRF-shaped fetches — bounded by what?
6. **Dependencies**: anything new — pinned how, vetted how, what runs at
   install time?

## Round protocol

- Every round is a **full pass** over the whole document (round one's
  misses live in unchanged sections), reviewing the previous round's
  dispositions first.
- Findings are recorded **verbatim and immutably** — the round file's
  Findings section is the reviewer's words; never edit, renumber, or
  re-grade them. Dispositions live in a separate section: closed /
  partially closed / open / rejected-with-reason; later rounds may mark
  one upheld or reopened.
- Convergence is the human's call, guided by yield — recommend stopping
  when findings shift from doctrine holes to bookkeeping. But never
  recommend stopping while a P1 is open unless the human has explicitly
  accepted it, with that acceptance recorded in the disposition.

## Artifacts

In the active epic's directory (they travel into the archive with it):

- `research/spec-review-log.md` — append-only, one line per offer:
  round, document, authoring model, chosen channel and profile (or the
  decline).
- `research/spec-review-<n>.md` — per round: provenance header
  (document + sha at review time, author, reviewer, profile), verbatim
  findings, dispositions.

Do **not** record these as intake research artifacts (`intake research
--artifacts`) — that would re-open the research rung and cascade design
and plan approval for bookkeeping. They are cited by round number, not
by rung fingerprint.

## Done when

- The round's findings all carry dispositions, P1s closed or explicitly
  human-accepted.
- The document's approval (spec, design, or plan) proceeds through the
  normal `wdd-intake`/`wdd-plan` sign-off — this skill never records
  approvals itself.

Close with the handoff: name the rung or approval the document now feeds
— "Review's converged. Ready to record the spec approval?"
