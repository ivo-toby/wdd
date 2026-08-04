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

"Different mind" means **different model identity**, not different
process or session: a fresh session of the same model reviewing a spec
that model authored is still self-review. The skill states the artifact's
authoring model (from the round log's provenance line, or "unknown —
treat as this model" when the artifact predates logging) alongside each
candidate reviewer so the human confirms with the collision visible.

Selection, offered to the human explicitly and confirmed **every time**
(never assumed from a previous round, session, or config):

1. **A second coding agent**, when one is available and the human approves
   dispatching it: a different-vendor/different-model CLI. The channel is
   NOT `wddctl dispatch` (that verb is task-scoped and needs a snapshot
   that doesn't exist pre-plan): the skill writes a self-contained prompt
   — charter, checklist, the artifact path, grounding paths — and either
   execs the approved CLI directly (`codex exec "..."`-style, the exact
   invocation shown to the human before running) or hands the prompt to
   the human to paste into a tool they operate.
2. **The human as reviewer**, when no second agent exists, when dispatch
   isn't allowed in the environment, or when the human simply prefers it.
   The skill's job flips to *assistant*: it walks the human through the
   checklist below section by section, records THEIR findings, and never
   substitutes its own judgment for theirs.

The offer names the concrete options it detected ("Codex is on this
machine — want me to run it for the adversarial round, or will you review
it yourself?") and proceeds only on an explicit answer. If the human
declines review entirely, that is their call — the decline is recorded as
one line in the round log (see Artifacts below) and the skill moves on;
it never nags, and never silently self-reviews as a "fallback".

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

- Every round = one **full pass** over the whole artifact (never "only
  what changed" — round one's misses live in unchanged sections),
  reviewing the previous round's dispositions first.
- The reviewer's findings are recorded **verbatim and immutably**: the
  round file's Findings section is the reviewer's words; the author never
  edits, renumbers, or re-grades them. Dispositions live in a separate
  section of the same file: per finding, closed / partially closed /
  open / rejected-with-reason — and a later round may mark a disposition
  **upheld** or **reopened**.
- Convergence is a human call, guided by yield — but it is only
  recommendable when every P1 is closed or the HUMAN has explicitly
  accepted it open, with that acceptance recorded in the disposition. A
  live P1 plus a stopping recommendation is a protocol violation.
- Artifacts: `research/spec-review-log.md` (append-only: one line per
  offer — round, channel, authoring model, reviewer, or the decline) and
  `research/spec-review-<n>.md` per round (provenance header, verbatim
  findings, dispositions). These files are **deliberately NOT recorded as
  intake research artifacts** — adding one to the research record after
  the rung is approved would re-open research and cascade design and the
  plan approval for pure bookkeeping. They live in the epic directory,
  travel with it into the archive, and are cited by round number, not by
  rung fingerprint.

## 2. Prose hardening in existing skills

- **`wdd-intake`**: the spec rung's guidance gains the state-table rule
  (stateful behavior gets a table, not prose) and the ambiguity lexicon as
  a self-check before presenting a draft. At `agree_spec` and
  `agree_design` sign-off, the skill OFFERS the adversarial round —
  one sentence, human decides, every time; the offer and the decision are
  part of the sign-off conversation, not a silent default either way.
- **`wdd-plan`**: the verification-reconciliation section gains the
  **independent-oracle rule**: the verification gate must include at least
  one check whose *assertion content* the implementing worker did not
  author — the deliverable command counts only when it exercises behavior
  (a startup smoke, a golden fixture frozen from a human-confirmed run, a
  conformance test pinned to a cited contract), not when it merely runs
  the worker's own test suite. The brief's Verification section must NAME
  which check is the independent one; a plan whose every oracle is
  worker-authored is the cheating-agent loop with extra steps. (Prose
  rule; a provenance-tracking lint is explicitly out of scope — the
  planner and reviewer enforce it by reading.)
- **`wdd-worker`**: the report contract gains a **Decisions** section:
  every choice the brief did not dictate (library, data shape, algorithm,
  workaround) in one line each — decision + why. NEEDS_CONTEXT stays the
  escape hatch for decisions above the worker's pay grade.
- **`wdd-run`**: the controller copies EVERY worker Decisions entry —
  no notability triage at capture time — into the epic's knowledge draft
  (§3) as it lands, tagged with the task id; triage happens once, at
  distillation, where dropping a row is a visible act. (`wddctl note`
  stays what it is — reconciliation input that gets cleared when the
  checkpoint runs — and is NOT the decisions pipeline.) At review
  dispatch, the reviewer packet names the worker's Decisions section as a
  review target: a wrong decision reviewed early is a P2; the same
  decision discovered at final review is a rewrite.
- **`wdd-intake`**: the research rung reads `shared-context/knowledge/`
  FIRST — before any external reference — and cites applicable rows into
  the contract inventory or the design like any other on-disk source.
  Knowledge nobody is instructed to read is knowledge that doesn't exist.

## 3. Institutional knowledge: the epic retrospective

The knowledge file is a **living draft, not an archive-time memory test**:
`shared-context/knowledge/<slug>.md` is created when the epic starts
(the intake skill's epic-creation step) and appended throughout — worker
Decisions entries as they land (§2), root causes as they're diagnosed,
quirks as they bite. Distillation at the end is triage and tightening of
rows that already exist, never reconstruction from recollection; a root
cause that never made it into the draft while it was fresh is exactly the
knowledge that gets hallucinated later.

At `delivered`, before `scope archive`:

- `next`'s delivered-phase judgment text names the retrospective step
  alongside the archive command (machine surfaces the offer; the skills
  carry the conversation). An operator running `scope archive` directly
  bypasses the offer — acknowledged and accepted: the offer is standing
  guidance, not a gate (see Non-goals), and the draft file survives
  either way since it was written all along.
- Distillation content contract (load-bearing rows, same discipline as
  design.md): **Root causes** (symptom → cause → fix — not "fixed the
  retry bug" but "503s under load → pool exhausted by nightly cron →
  pool size + cron window"), **Quirks** (environment/tooling surprises
  that cost time), **Decisions** (choices a future epic might revisit,
  triaged from the draft's full capture), **Review catches** (what
  adversarial/task review caught that would have shipped). Completeness
  beats brevity — keep every diagnosed root cause; when the file grows
  past a few screens, add a five-line summary at the top rather than
  cutting rows.
- **Citations are record coordinates, not paths**: task ids, review round
  numbers, AC numbers, event types — stable across the archive rename and
  resolvable in `archive/<slug>/record.json` forever. Filesystem paths
  into `epics/<slug>/` are forbidden in knowledge rows (they go stale at
  archive; `archive/` paths are unresolvable before it).
- The human signs off on the distilled file; the sign-off is recorded IN
  the file as a trailer (`Signed-off-by: NAME <date> sha256:<digest of
  the content above this line>`) so later edits are detectable by anyone
  who cares to check — a convention, not a machine gate.
- The distillation step ends by **committing the file** (shared-context
  is committed durable state by standing doctrine; a knowledge file that
  exists only in one working tree is not institutional). Then
  `scope archive` runs as usual.
- Future epics consume it because §2 makes `wdd-intake` read
  `shared-context/knowledge/` first — consumption is instructed, not
  assumed.

## Non-goals

- No new CLI verbs, no schema changes, no knowledge-graph/RAG machinery —
  `shared-context/knowledge/` is files, grep, and citations. The single
  machine touch in this phase is one judgment-text edit: the
  delivered-phase `next` payload names the retrospective step. External
  stores (e.g. a personal knowledge base) can mirror it; that is the
  operator's business.
- No automatic review dispatch — the human confirms the reviewer channel
  every time, by design, even when a second agent is configured.
- No retrospective hard gate — `scope archive` never refuses for a missing
  retrospective, and running it directly (skipping the offer) is legal
  operator behavior. The living-draft design is what makes this safe: the
  knowledge accumulated during the epic exists whether or not the final
  distillation conversation happens.
- Review rounds for task briefs (plan-level) reuse `wdd-spec-review`
  unchanged when asked; no separate brief-review skill.
