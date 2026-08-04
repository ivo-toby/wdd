# Why WDD exists, and what building it taught us

WDD is experimental software. It is young, it changes fast, and most of
what is true about it was learned the hard way within the last few
months — by running it on real projects, watching it fail, and turning
each failure into a rule the system enforces. This chapter is the honest
version of the sales pitch: why the thing exists, how it thinks, and
what the trial runs actually showed.

## Why

The bottleneck in software development has moved. Coding agents produce
working code faster than anyone can read it, which means the engineering
rigor that used to live in code review has to move upstream — into
specs, decomposition, verification gates, and evidence — or it silently
disappears.

Most agent failures on large work are boring engineering failures:
missing context, ambiguous scope, two agents editing the same file, a
review that never happened, a "done" that nobody verified, decisions
that evaporate when the context window rolls over. None of these are
model-intelligence problems. They are process problems, and process
problems have process solutions.

WDD's one structural bet: **prose can be ignored; a state machine
cannot.** Every rule that matters is enforced by `wddctl` — a small,
deterministic controller with no AI in it — and the skills (markdown
instructions agents load) carry only the judgment a machine can't make.
An agent that ignores the instructions still can't start two conflicting
tasks, merge unreviewed work, or run on a spec nobody approved, because
the state machine refuses. The instructions make agents effective; the
machine makes them safe.

The second bet: **evidence over claims.** Agents self-report success,
and self-reports are unreliable in both directions. So every meaningful
state transition in WDD is bound to evidence — a SHA-256 of the approved
bytes, a recorded verification result, a named reviewer — and gates
compare evidence, not narratives. "The spec was approved" means the hash
of the file still matches what a named human signed off on. Anything
else is drift, and drift blocks execution until a human re-approves.

## How, in one paragraph

A repository gets a ratified constitution (prose values) and config
(machine knobs). Each body of work is an **epic**: named, configured,
then walked up an intake ladder — agree the spec, do the research,
agree the design — with every rung fingerprint-bound to a human
approval. A plan decomposes the epic into tasks with explicit
dependencies and conflict domains; workers implement in isolated
worktrees from immutable snapshots; reviewers and verification commands
gate every merge; a final review and verification gate the epic itself;
delivery is observed (a human merges — `wddctl` never does), and the
epic is archived as a self-contained unit. Then the ladder restarts for
the next one. `docs/workflow.md` walks all of this with real
transcripts.

## What the benchmarks taught us

We ran the same non-trivial project (a CHIP-8 emulator with a terminal
debugger) through WDD with different agent stacks — GPT-class models in
Codex, GLM/Kimi/DeepSeek via a local harness — and rescued a real MCP
server project with a 35B local model that had previously failed
spectacularly without WDD. These runs shaped the system more than any
design document. The learnings, in the order they hurt:

### 1. Quality follows the gates, not the model

The Codex build passed every opcode test, froze correct golden hashes,
and completed the full lifecycle — and shipped a garbled terminal
renderer, because rendering was the one surface no acceptance criterion,
no golden test, and no reviewer ever touched. The "weaker" stack got the
renderer right and the timing badly wrong — differently gated,
differently lucky. Across every run, output quality tracked the
verification gates almost exactly; whatever isn't gated is uniformly
untested luck, regardless of which model you use. This is the strongest
argument for writing checkable acceptance criteria, and it's why WDD
lints briefs for missing deliverables and pushes for verification
commands that exercise behavior, not just unit tests.

### 2. Fabrication migrates to whatever you didn't gate

A small local model rebuilding an API client fabricated endpoints with
confident, false citations. We added the contract-inventory rule — cite
only files you actually opened, fetch missing dependencies or mark rows
NOT READ — and the request layer came out perfect on the next run. The
fabrication moved to the response shapes, which the inventory didn't
cover. We added response-shape rows; it moved to the OAuth flow, the one
path with no test that could fail. Each fix is real progress — but the
lesson is structural: invention concentrates on exactly the surfaces
where nothing can catch it. Gates don't just verify; they steer.

### 3. Small models follow machines, not manners

A 35B model walked the fingerprint-bound intake ladder flawlessly — the
state machine told it exactly what was next, and it did it. The same
model wandered badly wherever guidance was prose-shaped: it guessed at a
scaffolding command that didn't exist, then searched the whole
filesystem for template files. The fix wasn't better prose — it was
`wddctl plan template`, a deterministic scaffold emitter. When a smaller
model misbehaves, the highest-yield fix is usually moving one more
mechanical step from instructions into the machine.

### 4. Honesty must be a rule, not a virtue

The same model stamped review records as "passed, no findings" in a
human's name — for reviews that never ran. It claimed fixes it hadn't
made. It "helpfully" improvised an OAuth flow when the real contract was
inconvenient. What changed the behavior was never a persona instruction
("be rigorous") — it was concrete, checkable obligations: *never put a
human's name on evidence for work they didn't do; a review record for a
review that didn't happen is fabricated evidence; "I did X verified by
Y" is illegal without the Y.* Those rules now live in the skills and in
the constitution template, because testable obligations move models and
vibes don't.

### 5. Golden values are only golden once a human confirmed them

Both emulator builds froze verification hashes from their own output —
a regression pin that certifies nothing. One froze the hash of a test
ROM's results screen before the results had even rendered. The doctrine
that came out of it: an oracle is only an oracle if its expected value
was confirmed by something other than the code under test — a human
looking at the screen once, a cited reference, an independent
implementation. And at least one gate per task must be an oracle the
implementing worker didn't author.

### 6. Adversarial review upstream is absurdly cheap

WDD's own specs go through adversarial review rounds — a different
model, explicitly charged to break the document, every finding carrying
a concrete failure scenario. Recent phases averaged around a dozen real
defects caught per spec before any code existed, several of which would
have shipped as production bugs. The reviewer must be a different model
than the author (same-model review converges on shared blind spots),
and the human confirms who reviews, every time. This is now a skill
(`wdd-spec-review`), because it is the highest-leverage hour in the
whole workflow.

### 7. The process catches its own corners being cut

The one implementation task we let skip per-task review — "it's just
isolated release tooling" — produced both blockers found by the final
whole-branch review, guarded by tests that couldn't fail. The system's
layered reviews exist precisely because every layer occasionally
fails; the time we removed a layer, the failure walked straight
through the gap.

## What experimental means here

Concretely: WDD is a young system under active, sometimes daily,
redesign. State schemas have already been through six versions
(migrations are provided and tested, but they exist because things
change). The test suite is substantial and the controller is built with
the same evidence discipline it enforces — but it has been exercised by
a handful of projects and one very demanding dogfooding loop, not by
years of production use across teams. Expect sharp edges, expect the
docs to occasionally trail the code by a day, and expect design
decisions to be revisited when trial evidence says they're wrong — that
last part is a feature.

If the ideas resonate, the honest recommendation is: try it on
something small and real, with whatever agents you already use, and see
whether the gates catch something your current process wouldn't have.
That test is the only pitch that matters.
