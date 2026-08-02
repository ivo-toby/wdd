# Front-Half Skills & Workflow Documentation Plan (Phase 6c)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The prose half of the front-half spec: a new `wdd-intake` skill owning the three ladder rungs, a slimmed decomposition-only `wdd-plan`, a new `wdd-runners` skill, targeted updates to the router / `wdd-run` / `wdd-review` / `wdd-setup`, and a `workflow.md` rewrite covering the full v5 lifecycle. No CLI code changes — phases 6a/6b landed all machinery (main at 04de65c, 523 tests).

**Architecture:** Skills are prose contracts for judgment; `wddctl` owns choreography. Every skill instruction must name only verbs and flags that exist in `wave_delivery/cli.py` — writers verify each command against the code before writing it, never from memory. Voice: the existing skills' register (see `skills/wave-driven-development/SKILL.md` "Talking to the user"); imperative, no filler, handoff at the end of every phase.

**Tech Stack:** Markdown only. Verification = command-accuracy audit against `cli.py` + the doc build being none (no build step); the whole-plan gate is the final review.

**Spec:** `docs/superpowers/specs/2026-08-01-front-half-intake-ladder-design.md` §1 (ladder), §2 (artifacts), §4 (granularity lever), §5 (skills — governs this plan), §6 (runners skill + workflow appendix). Spec governs; flag conflicts.

## Global Constraints

- No changes to `wave_delivery/` or `tests/` in this phase. Docs and `skills/` only.
- Every `wddctl` invocation cited in a skill MUST be verified against `wave_delivery/cli.py` (subparser + flags). A hallucinated flag in a skill is a Critical finding.
- Skills keep the standing rules: "You run every `wddctl` command in this skill yourself…" opener (except worker-side docs), the instrument-panel voice rule lives in the router only, every phase ends with a handoff naming the next skill's action in user terms.
- Skill frontmatter: `name` + `description` (the description is the trigger — write it for skill-selection, mention concrete trigger phrases).
- Do not sync skills to any home directory (`~/.claude`, `~/.agents`, dotfiles) — that happens after merge, outside this plan.
- Conventional commits; never push.

---

### Task 1: New `wdd-intake` skill

Spec §5 ("New wdd-intake"), §1 (ladder + cascade + drift), §2 (artifact contracts).

**Files:** create `skills/wdd-intake/SKILL.md`.

**Content contract:**
- Owns the three rungs — spec agreement, research, design — as ONE conversation; `wddctl next`'s emitted action names the stage, the skill supplies the judgment. Ingest/challenge/clarify prose moves here from `wdd-plan` (adapted, not copied verbatim).
- Spec rung: `.wdd/spec.md` four sections, acceptance criteria numbered `- [ ] AC-1: …` (checkable conditions); record with `wddctl intake spec --approved-by <human>`. Clarification is not a rung — it happens at each approval; batch questions in compact rounds.
- Research rung: when it applies (external contracts, unfamiliar APIs, references to transcribe) vs when to skip. The canonical artifact is the contract inventory (`.wdd/shared-context/contract-inventory.md`): operation → method/path/shape → citation, built by READING the reference, one row per operation. Record with `wddctl intake research --done --by <human> --artifacts <paths>` or an attributed skip `--skip --by <human> --reason "…"` — silence is not an option.
- Design rung: `.wdd/design.md`, one page with teeth, four sections (Components / Interfaces / Integration surfaces `- \`path\` — owned by: <responsibility>` / Epic deliverable + the command that proves it). Record with `wddctl intake design --approved-by <human> --deliverable-command "…"`. Size discipline: if it reads like a narrative, it is wrong.
- Fingerprint doctrine, in user terms: every approval binds to the bytes; editing an approved artifact re-opens its rung and cascades (spec clears research+design+plan approval; research clears design+plan; design clears plan). During execution the same edit surfaces as an `intake_drift` blocker — the remedy is re-approval plus a plan re-stamp (`plan apply --approved-by`, one command when the plan file is unchanged). Never treat the cascade as an error; it is the system refusing to run on unapproved foundations.
- Scope rollover: after `delivered`, `wddctl scope archive` retires the scope and restarts the ladder; nothing scope-specific carries forward.
- Handoffs: each rung ends with explicit sign-off and the offer to continue ("Spec's agreed. Should I start research / go straight to design?"); design approval hands to `wdd-plan` ("Design's approved. Want me to decompose it into tasks?").
- Verify every cited verb/flag against `wave_delivery/cli.py` (`intake` subparsers, `scope archive`).

Commit: `feat(skills): add wdd-intake — the ladder rungs as one conversation`

---

### Task 2: Slim `wdd-plan` to pure decomposition

Spec §5 ("wdd-plan slims"), §3 (plan schema: Deliverable/Interfaces brief sections, context refs, per-task routing), §4 (granularity lever).

**Files:** rewrite `skills/wdd-plan/SKILL.md`; check `skills/wdd-plan/templates/` (task.md/plan.json templates — update to the v5 brief shape if they exist and are stale).

**Content contract:**
- REMOVE: Ingest, Challenge-and-clarify, Agree-spec sections (they now live in `wdd-intake`). Entry precondition instead: the ladder is complete (`wddctl next` no longer emits rungs); if it isn't, hand to `wdd-intake` instead of improvising.
- KEEP (verbatim-in-spirit, tightened): task splitting, integration-sink tasks, conflict domains, dependencies, risk axes incl. contract-transcription, riskRules interplay, maxConcurrent, preview/lint reading, approval flow, verification-reconciliation (acceptance criteria ↔ verification.commands), the two-formats warning (JSON plan / prose briefs).
- ADD — brief anatomy per task (spec §3): every brief carries **Deliverable** (what observably exists/runs when the task is done, testable from the diff) and **Interfaces** (what it consumes/produces, cited from design.md and the contract inventory — cite rows, don't restate from memory); context refs (`context` field: files under `.wdd/` the worker needs, anchors allowed) are machine-carried into the dispatch packet — list what the worker needs, nothing more.
- ADD — the granularity lever (spec §4, normative): decompose until each task's residual judgment fits the configured worker tier; frontier worker → few intent-level tasks, small worker → many near-pseudocode briefs with every contract cited. Classify each task mechanical vs judgment and route per task via `model`/`reviewModel` fields; review tier follows diff nature, not habit.
- ADD — lint codes now include the v5 set; name the new ones a planner must expect: `missing_spec`, `missing_deliverable`, `missing_interfaces`, `missing_context`, `missing_criteria`, `unowned_surface`, `nonprose_brief`, `base_is_target` (verify the full current list against `wave_delivery/plan.py` / `cli.py` lint output and cite only real codes).
- Approval: `plan apply --approved-by` binds a composite fingerprint over plan + briefs + context files; any later edit surfaces as `plan_drift`, remedy is a re-stamp. Say it once, plainly.
- Handoff unchanged: "Plan's applied. Want me to run it?" → `wdd-run`.

Commit: `feat(skills): slim wdd-plan to decomposition with deliverable-driven briefs`

---

### Task 3: New `wdd-runners` skill

Spec §5 ("New wdd-runners"), §6 (runners, probes, governance order).

**Files:** create `skills/wdd-runners/SKILL.md`.

**Content contract:**
- Triggers: "add a runner", "use qwen/codex as a worker", "set up local workers", model values naming agent CLIs.
- What a runner is: file-in/file-out one-shot headless exec; the runner command owns its own sandboxing/permissions/model flags — authored per machine by the operator. Non-goals stated: no streaming, no retries, no supervision.
- Discovery: `wddctl doctor` reports which configured runners' commands are on PATH; use it before and after registration.
- Authoring help: walk the user to a headless command template with `{worktree}`/`{prompt}`/`{logfile}` placeholders (prompt arrives as a FILE PATH; `{logfile}` is the runner's OWN transcript file, distinct from wddctl's capture). Give 1-2 example shapes (from spec §6's examples: pi/codex style) but derive the real one from the user's CLI's actual headless flags — ask, don't guess.
- Registration order is law: probe the explicit candidate first (`wddctl dispatch --probe-command '["…"]'` — ungoverned because the human just typed/approved the argv), then `wddctl config set runners '…'`, then governance re-approval (`constitution amend --by <human>`), then optionally `dispatch --probe <name>` to re-verify the ratified command (governed). A runner that was never probed is configuration fiction; dispatch refuses a runner whose command digest lacks a passing probe, and editing the command after the probe re-refuses (digest follows the exact bytes).
- Using runners: a task whose routed model (task `model`/`reviewModel` override or tier config) names a runner is dispatched with `wddctl dispatch --task ID --role worker|reviewer`; worker output ends in a status token, reviewer output must end in the standard review-result JSON which `dispatch` validates and `review collect` records. Logs live under `.wdd/dispatch/` — transient, gitignored, never committed; read the tail like any subagent report.
- Troubleshooting: probe fails (command not headless, wrong flags, token not last line), timeout via `--timeout`, `NEEDS_CONTEXT` in worker output = re-dispatch with more context, not a runner failure.
- Handoff: registration ends with the passing probe reported and the offer to route work — "Runner's registered and probed. Want me to route the mechanical tasks to it in the next plan?"
- Verify all flags against `wave_delivery/cli.py` and `wave_delivery/runner.py`.

Commit: `feat(skills): add wdd-runners — registry, probes, governed dispatch`

---

### Task 4: Router + wdd-run + wdd-review + wdd-setup updates

Spec §5 (wdd-run dispatch packet, wdd-review deliverable-first, finalize tie-in), §1 (rungs in the router's judgment table).

**Files:** edit `skills/wave-driven-development/SKILL.md`, `skills/wdd-run/SKILL.md`, `skills/wdd-review/SKILL.md`, `skills/wdd-setup/SKILL.md`.

**Content contract:**
- Router: judgment table gains the intake rungs ("Ladder rungs pending (`agree_spec`/`research`/`agree_design` in `next`): use `wdd-intake`") between setup and plan; `wdd-runners` listed with its triggers; artifact layout gains `spec.md`, `design.md`, `shared-context/contract-inventory.md`, `archive/`, `dispatch/` (transient); skill list in frontmatter description updated. Keep it a router — no rung content here.
- `wdd-run`: dispatch packet section updated to the machine-carried reality — `start` output now includes the snapshot dir; the worker packet = snapshot paths (immutable copies of brief+context) + worktree + branch + deliverable expectation + status-token contract; reviewer packet = brief + context + diff + the numbered criteria it discharges. When the routed model names a configured runner, the packet assembly is `wddctl dispatch --task --role` — the controller runs one command instead of composing a subagent prompt (hand off runner setup to `wdd-runners`). New next actions the controller must handle: `inputs_changed` (per task: re-dispatch fresh or `rebind --task --by <human>` — a HUMAN decision, never the controller's own), `intake_drift`/`plan_drift` blockers (route to `wdd-intake` re-approval + re-stamp). Keep-the-loop-alive tiers stay.
- `wdd-review`: review against the declared **Deliverable first**, then Interfaces against the contract inventory (cite rows), then acceptance criteria by number. Final review walks `AC-n` numbers and design.md's epic deliverable statement. Keep vacuous-test/reference-fidelity learnings.
- `wdd-setup`: post-ratification handoff now points to `wdd-intake` (not `wdd-plan`): "Setup's done. Should I start the intake ladder? Bring a spec or describe the feature." Mention runners as an optional setup follow-on when the user names local/CLI models (route to `wdd-runners`).
- Verify every touched command citation against `cli.py`.

Commit: `feat(skills): route the ladder — router, run, review, setup updates for v5`

---

### Task 5: workflow.md lifecycle rewrite

Spec §6 ("worked example in docs/workflow.md's appendix territory"), §1-§2 (lifecycle).

**Files:** edit `docs/workflow.md`.

**Content contract:**
- Update the lifecycle walkthrough to v5: init → setup/ratify → **intake ladder (spec → research → design)** → plan → execute (snapshots at start, input binding, inputs_changed/rebind) → finalize (multi-command verification incl. the deliverable command) → delivered → **scope archive** → next scope. Every command block RUN FOR REAL against a scratch repo (the doc's own standing rule, stated in its intro — honor it); update stale transcripts that predate v5 where the surrounding section is touched.
- The skills list in the "Talking to an agent" section gains `wdd-intake` and `wdd-runners`.
- Appendix: one worked local-runner example — register a stub runner (the committed `tests/fixtures/fake-runner/fake-runner` is fine as the stub; label it), probe → config set → amend → dispatch a worker → show the log tail and status token. Real transcripts, labeled as produced with the stub.
- Do NOT rewrite sections untouched by v5 (the prose about enforcement-vs-obligation, roles, etc.) beyond what accuracy requires.
- Length discipline: the doc is 600 lines; it may grow, but every added line must be lifecycle or transcript, not narrative padding.

Commit: `docs(workflow): v5 lifecycle — ladder, handover, runners worked example`

---

## Self-review checklist (after Task 5)

- Spec §5 fully discharged: wdd-intake (T1), wdd-plan slim (T2), wdd-runners (T3), wdd-run packet + wdd-review deliverable-first + finalize tie-in prose (T4), workflow example (T5).
- Command-accuracy: zero cited verbs/flags absent from `cli.py`.
- No content duplicated between wdd-intake and wdd-plan (the ladder/decomposition boundary is clean); router stays a router.
- Every skill ends with a handoff; the handoff chain is closed (setup → intake → plan → run → …; archive → intake).
- No home-directory syncs, no CLI changes.
