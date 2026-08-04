# Adversarial Spec Review and Institutional Knowledge

Status: draft for adversarial review (fittingly).
Baseline: main after phase 7 (epic-scoped state, v6, wddctl 0.1.0).

## Problem

Engineering rigor has moved upstream: the spec is now where quality is won
or lost, and writing an unambiguous spec is a skill engineers need training
and help with. WDD's own history proves both halves of this phase:

1. **Spec review is improvised.** The phase-6/7 specs went through three
   adversarial review rounds each and yielded 26+ real defects — several of
   which would have shipped as production bugs. That process (refute-first
   reviewer, P1/P2 findings with failure scenarios, per-round disposition)
   worked, but it lives in one person's chat habits, not in the system.
2. **Tribal knowledge evaporates at delivery.** A delivered epic's archive
   holds everything a future engineer-or-agent needs (root causes found,
   quirks discovered, decisions and their why) — but nobody distills it,
   so every future session re-learns it the expensive way. The "restart
   the server six times" failure mode is a knowledge-capture failure.

## Design principle

Judgment in skills, choreography in the machine — and this phase is almost
entirely judgment: one new skill, prose hardening in four existing skills,
zero new CLI verbs. The one doctrine with teeth: **the author never
adversarially reviews its own work, and the human confirms who does —
every time.**

## 1. New skill: `wdd-spec-review`

Triggers: "review this spec", "poke holes in this", before `agree_spec` /
`agree_design` / `plan apply` sign-off on any epic the human calls
high-stakes, or whenever the intake skill offers it (§2) and the human
accepts.

### Reviewer selection — the hard rule

The adversarial reviewer must be a **different mind than the author** of
the artifact. Same-model self-review converges on its own blind spots and
is forbidden — a review round produced by the authoring agent reviewing
its own spec is not an adversarial review and must never be presented as
one.

Selection, offered to the human explicitly and confirmed **every time**
(never assumed from a previous round, session, or config):

1. **A second coding agent**, when one is available and the human approves
   dispatching it: a different-vendor/different-model CLI (a probed WDD
   runner, a harness-native second model, or an agent the human operates
   themselves, e.g. pasting the prompt into another tool). The skill
   composes the review prompt; the human chooses the channel.
2. **The human as reviewer**, when no second agent exists, when dispatch
   isn't allowed in the environment, or when the human simply prefers it.
   The skill's job flips to *assistant*: it walks the human through the
   checklist below section by section, records THEIR findings, and never
   substitutes its own judgment for theirs.

The offer names the concrete options it detected ("Codex is on this
machine — want me to dispatch it for the adversarial round, run it through
a registered runner, or will you review it yourself?") and proceeds only
on an explicit answer. If the human declines review entirely, that is
their call — record the decision in the round log and move on; the skill
never nags, and never silently self-reviews as a "fallback".

### The reviewer persona: refute, don't improve

The reviewer's charter (embedded verbatim in the dispatch prompt):

- Your job is to **break this document**, not improve it. Suggestions are
  out of scope; failures are the deliverable.
- Every finding carries a **concrete failure scenario**: the inputs, state,
  or sequence under which the spec's text produces the wrong outcome — not
  "this is unclear" but "an implementer reading this line will build X,
  and X does Y under Z".
- Findings are **P1** (implementing the spec as written produces a defect
  or the spec cannot be implemented as written) or **P2** (a reader can
  reasonably build the wrong thing; underspecified boundary; untestable
  criterion). No style notes, no praise.
- If you cannot break a section, say what you tried. An empty report with
  no attack log is a non-review.

### The checklist (what the reviewer hunts, what engineers train on)

1. **Ambiguity lexicon**: every "should", "handle", "appropriate",
   "properly", "as needed", "etc." is a finding unless the sentence
   survives replacing it with a hostile reading.
2. **Testability per acceptance criterion**: for each AC, can a reviewer
   decide true/false from the diff or a command's output? An AC that needs
   interpretation is a P2.
3. **Unstated assumptions**: what must be true for this to work that the
   spec never says (scale, ordering, idempotency, permissions, encoding,
   time zones, concurrent access)?
4. **Abuse and scale probes**: rate limits, retries, loops, fan-out — the
   "50,000 emails in a few minutes" class. Anything that sends, spends,
   deletes, or loops gets a bounded-by-what question.
5. **Stateful behavior demands a state table**: if the spec describes
   behavior with more than two states or any transition rules in prose,
   demand a state machine or decision table. Models fill unspecified
   transitions with guesses; tables make the gaps visible.
6. **Contract citations**: any claim about an external system (API shape,
   protocol, library behavior) must cite something on disk — the
   contract-inventory doctrine extended to specs.
7. **Failure-path parity**: for every described happy path, where is the
   failure path? Unwritten error handling is a decision delegated to the
   least-informed party (the implementing model).

### Round protocol

- One round = one full pass producing numbered findings.
- The author (agent or human) amends the artifact and produces a
  **disposition table**: per finding, closed / partially closed / open /
  rejected-with-reason.
- The next round re-reviews the dispositions first, then makes one fresh
  pass over what changed.
- Convergence is a human call, guided by yield: when a round's findings
  shift from doctrine holes to bookkeeping, recommend stopping — further
  rounds cost more than implementation-time review catches.
- Artifacts live in the epic: `research/spec-review-<n>.md` (the findings
  + disposition), referenced from the intake research record where the
  research rung applies — reviews are research artifacts, citable and
  fingerprint-bound like any other.

## 2. Prose hardening in existing skills

- **`wdd-intake`**: the spec rung's guidance gains the state-table rule
  (stateful behavior gets a table, not prose) and the ambiguity lexicon as
  a self-check before presenting a draft. At `agree_spec` and
  `agree_design` sign-off, the skill OFFERS the adversarial round —
  one sentence, human decides, every time; the offer and the decision are
  part of the sign-off conversation, not a silent default either way.
- **`wdd-plan`**: the verification-reconciliation section gains the
  **independent-oracle rule**: the verification gate must include at least
  one check the implementing worker did not author (the deliverable
  command, golden fixtures frozen from a confirmed run, a conformance
  test pinned to a cited contract). A gate made solely of worker-authored
  tests can be satisfied by the cheating-agent loop — broken code
  validated by broken tests.
- **`wdd-worker`**: the report contract gains a **Decisions** section:
  every choice the brief did not dictate (library, data shape, algorithm,
  workaround) in one line each — decision + why. NEEDS_CONTEXT stays the
  escape hatch for decisions above the worker's pay grade.
- **`wdd-run`**: the controller routes notable worker decisions to
  `wddctl note` (existing verb) so they survive into shared-context; at
  review dispatch, the reviewer packet names the worker's Decisions
  section as a review target (a wrong decision reviewed early is a P2; the
  same decision discovered at final review is a rewrite).

## 3. Institutional knowledge: the epic retrospective

When a scope reaches `delivered` and before `scope archive`, the
controller (per `wdd-run` / `wdd-intake`'s rollover section) offers the
**retrospective step** — confirmed with the human like everything else:

- Distill the epic into `shared-context/knowledge/<slug>.md` — written
  BEFORE archive so it lands in the global, archive-surviving namespace.
- Content contract (one page, load-bearing, same discipline as design.md):
  - **Root causes**: every bug whose cause was diagnosed during the epic —
    symptom → cause → fix, one row each. Not "fixed the retry bug";
    "503s under load → connection pool exhausted by the nightly cron →
    pool size + cron window".
  - **Quirks**: environment/tooling/dependency surprises that cost time
    (the class of thing a senior "just knows").
  - **Decisions**: choices with a why that a future epic might revisit —
    from design decisions down to notable worker Decisions entries.
  - **Review catches**: what adversarial/task review caught that would
    have shipped — the training signal for better specs next time.
  - Every row cites its source in the epic's record (task id, review
    finding, note) — citable knowledge, not folklore.
- The human signs off on the distillation (it speaks for the epic's
  history; hallucinated institutional knowledge is worse than none), then
  `scope archive` runs as usual.
- Future epics consume it for free: the research rung's guidance already
  reads `shared-context/`; `knowledge/` rows are citable exactly like
  contract-inventory rows.

## Non-goals

- No new CLI verbs, no schema changes, no knowledge-graph/RAG machinery —
  `shared-context/knowledge/` is files, grep, and citations. External
  stores (e.g. a personal knowledge base) can mirror it; that is the
  operator's business.
- No automatic review dispatch — the human confirms the reviewer channel
  every time, by design, even when a second agent is configured.
- No retrospective hard gate — `scope archive` never refuses for a missing
  retrospective; the offer is mandatory, the artifact is not.
- Review rounds for task briefs (plan-level) reuse `wdd-spec-review`
  unchanged when asked; no separate brief-review skill.
