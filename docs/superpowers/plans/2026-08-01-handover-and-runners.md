# Handover & Runners Implementation Plan (Phase 6b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The execution half of the front-half spec: immutable attempt snapshots, input-version binding with the `rebind` escape hatch, model/reviewModel routing decoration, and the runner registry with governed dispatch and digest-gated probes. Phase 6c (skills + workflow.md) follows.

**Architecture:** Snapshots and input binding live in a new `wave_delivery/handover.py` (materialization + digest recording + `inputs_status`), consumed by `start` (CLI layer — leases stays engine-pure) and the task-targeted verb gates. Runners live in `wave_delivery/runner.py` (`dispatch`, probes, log policy) reusing the fake-fixture testing idiom from phase 4's fake-gh. Routing decoration extends the phase-4 `models`/`task_risk` plumbing in `engine.decorate_actions`.

**Tech Stack:** Python 3 stdlib only; `unittest`; fixtures: a committed fake runner script (like `tests/fixtures/fake-gh`).

**Spec:** `docs/superpowers/specs/2026-08-01-front-half-intake-ladder-design.md` §3 (snapshots, input binding), §4 (routing), §6 (runners) — spec governs; flag conflicts.

## Global Constraints

- Stdlib only; errors via `errors.py`; state writes via `apply_mutation`; new event types: `task.rebound`, `runner.probed`, `task.dispatched`.
- `.wdd/dispatch/` is transient scratch: gitignored via `.wdd/.gitignore` (init/migrate write the entry), dir `0700`, files `0600`, attempt numbering, task-id sanitization `[A-Za-z0-9._-]` (the archive idiom).
- Legacy scopes: exempt from input binding (no recorded inputs → no gate); runners/dispatch work on any scope (they gate on governance, not intake).
- New tests in `tests/test_handover.py` (new file, local helpers; copy `_walk_intake`/scope patterns from `tests/test_intake.py` — no cross-file imports).
- Full suite FOREGROUND once per task (gpg workaround `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false`; it takes ~4-6 min — wait it out, never background-and-park). Conventional commits; never push.

---

### Task 1: Routing decoration — reviewModel, tiered review models

Spec §4 (routing), Sol plan-review P2 (reviewer routing representable).

**Files:** `wave_delivery/config.py` (validate `models.review` as string OR `{default, highRisk}`), `wave_delivery/engine.py` (`_resolve_model` precedence), `wave_delivery/cli.py` (pass-through unchanged — verify), tests in `tests/test_handover.py`.

**Interfaces:**
- `config.models.review`: plain string stays valid (means both tiers); object form `{"default": str|None, "highRisk": str|None}` validated like implementation.
- Decoration precedence: `start_task`/`assign_fix_writer` → task `model` override → risk-tiered `models.implementation`; `run_review` → task `reviewModel` override → risk-tiered `models.review` (object form) or the plain string; absent values → no `model` key (phase-4 rule unchanged).
- Task overrides come from the persisted task fields (6a landed them in state).

**Test contract:** plain-string review model still routes (regression); object form tiers by task risk; task `model`/`reviewModel` overrides win; all-null adds no key; config validation rejects bad shapes.

Commit: `feat(routing): task-level overrides and tiered review models in next payloads`

---

### Task 2: Attempt snapshots + input digests at start

Spec §3 ("Handover itself is immutable").

**Files:** create `wave_delivery/handover.py`; `wave_delivery/cli.py` (`start` handler materializes after `start_task` succeeds); `wave_delivery/setup.py` + `wave_delivery/finalize.py`'s migrate path (write `.wdd/.gitignore` with `dispatch/` — check where init scaffolds and add; migrate --governance also ensures it); `wave_delivery/schema.py` (task gains optional `"inputs": [{path, sha256}]` and `"snapshot": str|None`, validated); tests in `tests/test_handover.py`.

**Interfaces:**
- `materialize_attempt(state, wdd_dir, task_id) -> {"snapshot": <dir>, "inputs": [{path, sha256}]}`: copies the task's brief + every context-ref file (paths from the persisted task fields) into `.wdd/dispatch/<sanitized-task>-<attempt>/` (attempt = 1 + count of existing dirs for the task), read-only files (`0400`), returns recorded digests. Anchors (`#...`) are stripped for file resolution; the same file referenced twice copies once.
- CLI `start`: after a successful `start_task`, materialize and record `inputs`/`snapshot` on the task via one `apply_mutation` (`task.dispatched` event) — or fold into the start mutation if leases' structure allows without breaking engine purity (implementer's call, documented; the recorded digests must land atomically with or after the start, never before a failed start).
- `next`'s `start_task` action payload gains `"snapshot": true` marker? No — keep payloads unchanged; the snapshot path is in the start RESULT (controller hands it to the worker). Verify `start`'s CLI output includes `snapshot` + `inputs` count.
- Legacy scopes: snapshots still materialize (harmless, useful) but `inputs` recording is skipped (no binding without the doctrine) — simplest consistent rule; document it.

**Test contract:** start on a v5 scope materializes the snapshot (files present, read-only, content equals source), records digests; second start attempt (after block/unblock or re-start) gets attempt dir `-2`; context ref with anchor resolves to its file; gitignore entry present after init; legacy start records no `inputs`.

Commit: `feat(handover): immutable attempt snapshots with recorded input digests`

---

### Task 3: Input-version binding — `inputs_changed`, gate, `rebind`

Spec §3 ("input-version binding"), the merged-evidence-is-history clause.

**Files:** `wave_delivery/handover.py` (`inputs_status(state, wdd_dir, task_id) -> None | {"path", "recorded", "actual"}`), `wave_delivery/cli.py` (task-targeted verb gate + `rebind` verb + `next` surfacing), tests in `tests/test_handover.py`.

**Interfaces:**
- `inputs_status`: None when task has no recorded `inputs` (legacy or pre-6b tasks) or all digests match the CURRENT files; else first mismatch (missing file → `"actual": "missing:<path>"`).
- Gate: the task-targeted governed verbs (`submit`, `review record/collect`, `verify record/collect`, `refresh`, `merge`) refuse via `IllegalTransition` (message contains "inputs changed"; names `rebind` and re-dispatch as remedies) when `inputs_status` is non-None FOR THAT TASK. Scope-level verbs and OTHER tasks unaffected. `start` is not gated (a fresh start re-materializes and re-records — that IS the re-dispatch path).
- `wddctl rebind --task ID --by NAME --repo .`: governed; refuses when `inputs_status` is None ("nothing to rebind"); re-records `inputs` digests against current bytes + `task.rebound` event `{by, at}` — the recorded human decision that existing work stands.
- `next`: for in-flight (non-terminal, non-todo) tasks with `inputs_status` non-None, emit an `inputs_changed` action (task-scoped, judgment naming the changed path and the two remedies, `recordWith` = the rebind command). Does not empty other actions — per-task, not scope-wide.
- Merged (`done`) and `cancelled` tasks: never gated, never surfaced — merged evidence is history.

**Test contract:** edit a context file after start → submit/merge refuse for that task, sibling task unaffected, next shows `inputs_changed` for it only; rebind records and clears; re-start (block→unblock→start or cancel-free path — use the cheapest legal re-dispatch) re-materializes and clears; done task with edited brief NOT gated (pin — distinct from the scope-wide plan_drift which WILL fire: acknowledge in the test via re-stamp first, document the interplay); legacy scope fully exempt.

Commit: `feat(handover): input-version binding with rebind and per-task inputs_changed`

---

### Task 4: Runner registry, probes, dispatch

Spec §6 (runners; probe-before-persist; digest-gated dispatch; log policy; reviewer output contract).

**Files:** create `wave_delivery/runner.py`; `wave_delivery/config.py` (validate optional `runners` map: name → `{"command": [str, ...]}` non-empty argv, placeholders `{worktree}`/`{prompt}`/`{logfile}` allowed anywhere); `wave_delivery/cli.py` (`dispatch` subparser: `--probe-command`, `--probe`, `--task/--role`); `wave_delivery/schema.py` (state gains optional top-level `"probes": {sha256: {at, ok}}`); fixture `tests/fixtures/fake-runner/fake-runner` (executable python: reads prompt arg, writes canned output — supports env toggles for DONE token / review-result JSON / failure); tests in `tests/test_handover.py`.

**Interfaces:**
- `runner_command_digest(command: list[str]) -> "sha256:..."` (canonical JSON of the argv template).
- `probe_command(command, *, timeout=120) -> {ok, exitCode, wallMs, tokenSeen}`: temp dir, canned prompt "Reply with exactly: DONE", token = trailing non-empty line == "DONE". `--probe-command '["..."]'` is UNGOVERNED (explicit candidate). On success records `probes[digest] = {at, ok: true}` via ungoverned `apply_mutation` (`runner.probed`, monitor-precedent observation write) WHEN state exists; without state, prints the result and says registration will re-probe.
- `--probe <name>`: GOVERNED (config-loaded command); same recording.
- `dispatch --task ID --role worker|reviewer --repo .`: GOVERNED + input-binding-gated like other task verbs. Resolves the task's routed model (worker: task model → risk tier; reviewer: reviewModel → review tier) — a value naming a configured runner dispatches via it; a non-runner value refuses with "not a configured runner; harness-native dispatch is the controller's job". Refuses when the runner's command digest lacks a passing probe record. Assembles the prompt packet: role contract line, brief + context SNAPSHOT paths (worker: from the task's recorded snapshot; reviewer: materialize a fresh attempt snapshot), deliverable section, status-token contract (worker) or `wddctl_review_result` JSON contract (reviewer — reuse `review.py`'s existing result schema constant; find it). Substitutes `{worktree}`/`{prompt}`/`{logfile}`; execs; captures to `.wdd/dispatch/<task>-<role>-<attempt>.log` (policy per Global Constraints); result: exit code, log path, bounded tail (last 4KB), and for workers the trailing status token if present / for reviewers the parsed+validated result JSON written to a sibling `-result.json` for `review collect`.
- No streaming, no retries, no timeout management beyond a single `--timeout` arg (default none) — the non-goals are law.

**Test contract:** config validation (good/bad runner shapes); probe-command success/failure recording + digest match; probe <name> governed (refuses under governance drift — reuse a drift fixture); dispatch refuses unprobed runner, refuses non-runner model, worker dispatch runs fake-runner and extracts DONE from the log tail, reviewer dispatch validates the fake review-result JSON and writes the result file consumable by `review collect` (drive one collect end-to-end), log/permission/naming policy, edited-command-after-probe refuses (digest mismatch).

Commit: `feat(runner): registry, digest-gated probes, and governed one-shot dispatch`

---

### Task 5: E2E + docs

**Files:** `tests/test_handover.py` (e2e), `docs/wddctl.md` (dispatch/probe/rebind/inputs_changed sections + snapshot mention in start; REAL transcripts via the fake runner, labeled as stub), `docs/artifact-schema.md` (dispatch dir layout, probes section, task inputs/snapshot fields).

**Test contract (e2e):** v5 scope with a runner registered (probe-command → config set → amend): start (snapshot recorded) → dispatch worker via fake runner (DONE) → submit → dispatch reviewer (result JSON) → review collect → verify/freshness/merge → an inputs_changed + rebind interlude mid-scope → finalize through delivered. One journey proving 6a+6b compose.

Commit: `test+docs: runner dispatch e2e and handover documentation`

---

## Self-review checklist (after Task 5)

- Spec §3: snapshots (T2), input binding + rebind + inputs_changed (T3), merged-evidence-is-history pin (T3). §4: routing decoration incl. review tiers (T1). §6: runners map, probe-before-persist + digest gating, governed dispatch, log policy, reviewer speaks `review collect`'s existing contract (T4). 
- Deferred to 6c (NOT here): all skill prose (wdd-intake, wdd-runners, wdd-run dispatch-packet wording, wdd-plan slimming), workflow.md.
- Interplay pins: plan_drift (scope-wide, from 6a) vs inputs_changed (per-task) documented and tested side by side (T3).
