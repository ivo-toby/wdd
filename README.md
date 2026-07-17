# WDD

Wave-Driven Development is a portable workflow kit for coding agents.

The runtime is made of two parts: agent skills that describe the workflow, and
the standard-library Python `wdctl` controller for deterministic state,
migration, leases, freshness checks, review evidence, and merge recording.
Agents create and update local artifacts under `.wdd/`, using Markdown for human
and agent context plus JSON where resumable machine state is useful.

This keeps the workflow inspectable across local agents and hosted coding
environments while still relying on scripts for the mechanical parts that should
not be left to prompt interpretation.

## Vision

AI coding agents are getting better at implementation, but large bodies of work still fail for boring engineering reasons: missing context, unclear scope, weak review loops, merge chaos, forgotten decisions, and agents losing track after context compression.

WDD treats agentic development as an engineering workflow, not a prompt trick.

The core idea is simple: humans define intent, agents do focused work, and the workflow preserves enough durable state for the whole system to remain understandable, reviewable, and resumable.

Epics provide the strategic boundary. Tickets group related work. Tasks are the executable unit. Waves maximize safe parallelism. Shared context carries durable knowledge across workers. Reviews, validation, and branch gates keep the system honest.

WDD is intentionally text-first. Markdown skills describe the process. Local artifacts are the source of truth. JSON is used where machines need resumable state, and `wdctl` handles deterministic controller operations without external runtime dependencies.

That makes the workflow portable across local coding agents, cloud agents, and whatever agent runtime comes next.

The goal is not to make agents faster at blindly editing files.

The goal is to make parallel agent work controlled enough that a senior engineer can trust the process, inspect the state, interrupt it, resume it, and review the final result.

## Principles

- Durable state over hidden context — if the workflow needs to remember something, it belongs in .wdd/.
- Tasks are the execution unit — workers get focused task files, not vague tickets or whole epics.
- Parallelism needs control — waves should maximize concurrency without pretending merge conflicts do not exist.
- Review is part of execution — worker self-review is useful, but separate review agents and human review remain first-class gates.
- Agents should preserve discoveries — durable worker memory keeps later tasks from rediscovering the same constraints.
- Inspectable artifacts over hidden infrastructure — the workflow should remain understandable from the repository state.
- Humans own intent and final judgment — WDD helps agents execute, but it does not remove engineering accountability.

## Who WDD Is For

WDD is for engineers who want to use coding agents on work larger than a single prompt or isolated bug fix.

It is especially useful for:

- multi-step features
- refactors
- migrations
- architectural changes
- test expansion
- bug clusters
- parallel agent experiments
- cloud-agent workflows where context compression and resumability matter

It is probably overkill for small one-shot edits.

## Which WDD Mode Should I Use?

Start with `wdd-info` when unsure. It is the read-only front door: describe the
work, and the agent recommends whether to skip WDD, use a micro-wave, start a
profiled epic, or resume existing WDD state.

| Situation | Use | What You Get |
|---|---|---|
| One small code change | No WDD | Normal agent work with repo-native verification |
| One chunky ticket that can split into 2-5 compact tasks | `micro` | A compact work brief, task briefs, `state.json`, and one finish handoff |
| Small feature or refactor with limited risk | `lite` | Compact epic artifacts, adaptive monitoring, and risk-based review |
| Multi-ticket feature, migration, or refactor | `standard` | The normal WDD epic flow with trimmed ceremony where safe |
| Auth, persistence, public API, data migration, security, or risky parallel work | `full` | Maximum review, validation, and monitoring discipline |

Copy-paste prompts:

```text
Should I use WDD for this? Recommend the mode and next step.
```

```text
Use WDD micro for this ticket. Split it into compact tasks only if worthwhile.
```

```text
Use WDD lite for this epic. Keep artifacts compact and use risk-based review.
```

```text
Use WDD full for this migration. Prioritize review and validation over speed.
```

The project constitution sets the default profile. Individual epics or
micro-waves record their chosen profile so future agents do not have to infer
it again.

## What WDD Provides

- A project constitution for setup, model aliases, storage mode, branch policy,
  profile defaults, review policy, verification expectations, and governance.
- A `wdd-info` front door for mode choice, resume guidance, and ceremony/cost
  tradeoffs.
- Micro-waves under `.wdd/work/` for bounded ticket-sized work that benefits
  from compact task splitting without full epic ceremony.
- Epic definition from vague product, technical, refactor, migration, or bug
  cluster input.
- Ticket folders that group related work.
- Task files that are the independently executable worker-agent units.
- Kanban task movement through `todo/`, `in-progress/`, `review/`, `done/`,
  `blocked/`, and `cancelled/`.
- Progressive-disclosure shared context for architecture, conventions, testing,
  validation, and durable worker discoveries.
- Dependency-aware and conflict-aware wave planning.
- Wave activation as a strategy-selected batch of eligible tasks.
- Persistent `orchestration.json` with `schemaVersion: 1` for resumability.
- Active-wave monitoring through the best available scheduler, with a durable
  manual fallback prompt when no scheduler is supported.
- Controller-managed worker PRs, review gates, feedback routing, stale-branch
  checks, merges into the epic branch, wave reconciliation, epic validation,
  and final PR preparation.

## Runtime Components

WDD relies on the files in this repository:

- `skills/` contains the agent skills that Codex and Claude Code discover.
- `wave_delivery/` contains the `wdctl` Python package.
- `scripts/wdctl.py` runs the in-repository controller without installation.
- `scripts/install_wave_delivery.py` installs the controller into a chosen
  prefix and writes `wdctl` launchers.

The controller is a standard-library Python tool for schema-v2 scopes. It is
available as `python3 -m wave_delivery`, `python3 scripts/wdctl.py`, or an
installed `wdctl` launcher. It currently provides
atomic revisioned state, explicit constitution ratification, deterministic next
actions, generated controller-state projections, and dry-run-first v1-to-v2
migration. It also offers Git worktree leases and risk-based branch freshness
checks, live-Git merge completion proof, plus zero-LLM local Git monitoring. It
is additive while v1 artifacts remain supported; see
[`docs/wdctl-v2.md`](docs/wdctl-v2.md).

The experimental controller uses only Python's standard library at runtime.

Repository-native checks can still be referenced as verification when available,
such as tests, linters, type checks, builds, CI status, or `git diff --check`.
Those checks prove the target project; `wdctl` proves the WDD controller state.

## Installation

Install or copy the directories in `skills/` into the agent's skill directory.
Copy the full directories recursively, including their `templates/` and
`agents/` subdirectories where present. The skill folders are the installable
runtime; users should not need a separate repository-root `templates/` folder.

Use a clone of this repository as the source:

```sh
git clone https://github.com/ivo-toby/wdd.git
cd wdd
```

Install the controller globally for your user:

```sh
python3 scripts/install_wave_delivery.py --prefix "$HOME/.local"
```

Then make sure the prefix is on your `PATH`:

```sh
export PATH="$HOME/.local/bin:$PATH"
wdctl doctor
```

For a project-local controller install, choose a prefix inside the target
repository:

```sh
python3 scripts/install_wave_delivery.py --prefix /path/to/project/.wdd/tools
/path/to/project/.wdd/tools/bin/wdctl doctor
```

You can also run the controller directly from the WDD clone without installing:

```sh
python3 scripts/wdctl.py doctor
```

### Global Skills

Install WDD skills globally when you want them available in every repository.

For Codex:

```sh
mkdir -p "$HOME/.agents/skills"
cp -R skills/* "$HOME/.agents/skills/"
```

For Claude Code:

```sh
mkdir -p "$HOME/.claude/skills"
cp -R skills/* "$HOME/.claude/skills/"
```

Restart Codex if newly installed skills do not appear. Claude Code watches
existing skill directories live, but restart it after creating a top-level
skills directory for the first time.

### Project Skills

Install WDD skills at project level when a repository should carry its own WDD
workflow with the codebase.

For Codex, copy the skills into the repository's `.agents/skills` directory:

```sh
mkdir -p /path/to/project/.agents/skills
cp -R skills/* /path/to/project/.agents/skills/
```

For Claude Code, copy the same skill directories into `.claude/skills`:

```sh
mkdir -p /path/to/project/.claude/skills
cp -R skills/* /path/to/project/.claude/skills/
```

Commit project-level skill installs if the whole team should share the same WDD
workflow:

```sh
cd /path/to/project
git add .agents/skills .claude/skills
git commit -m "Install WDD skills"
```

### Updating

Update the source clone first:

```sh
cd /path/to/wdd
git pull --ff-only
```

Then repeat the install step for each copied install:

```sh
python3 scripts/install_wave_delivery.py --prefix "$HOME/.local"
cp -R skills/* "$HOME/.agents/skills/"
cp -R skills/* "$HOME/.claude/skills/"
```

If a WDD release removes or renames a skill, delete the old WDD skill directory
before copying again. A plain `cp -R` updates files but does not remove stale
files that no longer exist in the source clone.

For project-level installs, repeat the copy into the target project and commit
the resulting changes:

```sh
cp -R skills/* /path/to/project/.agents/skills/
cp -R skills/* /path/to/project/.claude/skills/
cd /path/to/project
git add .agents/skills .claude/skills
git commit -m "Update WDD skills"
```

If you symlink skill directories from a clone instead of copying them, running
`git pull --ff-only` in that clone updates the skill files in place. Still rerun
`scripts/install_wave_delivery.py` after pulling, because the `wdctl` installer
copies `wave_delivery/` into the selected prefix.

## Skill Pack

For Codex-style local skills, that means:

```text
~/.agents/skills/
  wave-driven-development/
  wdd-info/
  wdd-init-project/
    templates/
  wdd-constitution/
    templates/
  wdd-start-work/
    templates/
  wdd-plan-work/
    templates/
  wdd-run-work/
  wdd-finish-work/
  wdd-start-epic/
    templates/
  wdd-plan-epic/
    templates/
  wdd-start-wave/
    templates/
  subagent-pr-orchestration/
  wdd-reconcile-wave/
    templates/
  wdd-epic-validation/
    templates/
  wdd-final-pr/
    templates/
  wdd-status/
  wdd-sync-github-project/
    scripts/
    references/
```

Compatibility wrapper skills are also included for older phase names:

```text
  wdd-write-tickets/
  wdd-validate-tickets/
  wdd-plan-waves/
```

These wrappers route old intents into `wdd-plan-epic`, because planning now
creates tickets, tasks, validation notes, waves, shared context, and
orchestration state in one coherent pass.

## Workflow

0. `wdd-info`
   - Recommends no WDD, `micro`, `lite`, `standard`, `full`, or resuming
     existing work. It does not modify files.

1. `wdd-init-project`
   - Creates `.wdd/`, `.wdd/work/`, `.wdd/templates/`, `.wdd/epics/`, and the
     initial constitution if missing, using the templates bundled inside the
     `wdd-init-project` skill folder.

2. `wdd-constitution`
   - Records setup choices: model aliases, model usage preferences, storage
     mode, default profile, branch conventions, review policy, merge policy,
     verification expectations, and governance.

3. Micro-wave flow for a chunky ticket:
   - `wdd-start-work` creates `.wdd/work/<work-id>/brief.md`.
   - `wdd-plan-work` creates compact task briefs and `state.json`.
   - `wdd-run-work` dispatches or resumes the micro-wave.
   - `wdd-finish-work` reconciles verification, review, and final handoff.

4. `wdd-start-epic`
   - Turns vague input into an implementation-ready epic and initializes
     `shared-context/`. The epic records `profile: lite`, `standard`, or `full`
     from user input or the constitution default.

5. `wdd-plan-epic`
   - Builds shared context, ticket folders, task files, dependency and conflict
     grids, `wave-plan.md`, `orchestration.json`, and initial
     `controller-state.md`. The selected profile controls artifact detail,
     monitoring cadence, and review strictness.
   - Recommends a strategy per wave: profile, execution mode, review mode,
     monitoring mode, confidence, and rationale. When confirmation is required,
     the user or controller confirms or adjusts before activation.

   Execution modes:
   - `bundled`: one worker/branch/review loop for the whole wave.
   - `hybrid`: 2-3 bundles inside the wave.
   - `parallel`: one worker/branch/review loop per task.

6. `wdd-start-wave`
   - Activates the next pending wave as a strategy-selected batch of eligible
     tasks.
   - Blocks if the selected wave strategy requires confirmation and has not
     been confirmed.
   - Dispatches according to strategy: bundled, hybrid, or parallel.
   - Records monitoring mode and fallback prompt. `lite` and `standard` use
     adaptive cadence by default; `full` may keep tighter monitoring.
   - In Codex, creates or verifies the active thread heartbeat before the
     controller ends the turn, or records the scheduler failure and fallback.

7. `subagent-pr-orchestration`
   - Dispatches one worker per task file, tracks every active task
     independently, starts required reviews, routes P1/P2 feedback, enforces
     stale-branch checks, runs bounded idempotent monitor ticks, and merges or
     marks merge-ready according to profile and policy.

8. `wdd-reconcile-wave`
   - Confirms merged or closed task state, reconciles drift, updates shared
     context, adjusts future tasks, and decides whether the next wave can start.

9. `wdd-epic-validation`
   - Validates the completed epic branch against the epic definition of done,
     task evidence, reviews, shared context, and integration state.

10. `wdd-final-pr`
   - Prepares the final epic PR from `epic/[epic-slug]` into the target branch
     with a comprehensive human-review description.

11. `wdd-status`
    - Reports actual `.wdd/` state without modifying files by default.

Optional adapter:

- `wdd-sync-github-project`
  - Mirrors a GitHub Project with one local WDD epic, imports GitHub Project
    items as tickets and tasks, emits dry-run plans for local-to-GitHub updates,
    and records sync fingerprints in an epic-local adapter manifest.
  - GitHub Projects remain an external planning mirror. `.wdd/` artifacts remain
    the WDD execution source of truth.

## Artifact Layout

```text
.wdd/
  constitution.md
  templates/
  work/
    WORK-filter-builder/
      brief.md
      state.json
      tasks/
        TASK-001-api-contract.md
        TASK-002-ui-state.md
        TASK-003-tests.md
  epics/
    EPIC-auth-refresh/
      epic.md
      adapters/
        github-project.json
      wave-plan.md
      orchestration.json
      controller-state.md
      epic-validation.md
      final-pr.md
      shared-context/
        index.md
        resources/
          architecture.md
          discovered-conventions.md
          testing-strategy.md
          validation-strategy.md
          task-findings.md
      TICKET-001-token-contract/
        ticket.md
        todo/
          TASK-001-token-types.md
        in-progress/
        review/
        done/
        blocked/
        cancelled/
```

Task files are the worker implementation briefs. The workflow does not create a
separate canonical brief artifact.

## Branching Model

Task work never merges directly to the target branch.

```text
main
└── epic/auth-refresh
    ├── task/TASK-001-token-types
    ├── task/TASK-002-refresh-route
    └── task/TASK-003-session-ui
```

Worker task PRs target the epic branch. Workers do not merge their own PRs.
Before any worker starts, the controller creates or verifies the epic branch.
Before dispatching parallel repository-writing workers, the controller syncs
activation artifact changes to the epic branch, creates or verifies one isolated
worktree per task from that synced state, and tells each worker the exact path
to use. Workers must not switch branches in the controller checkout. The
controller owns the merge gate, including review status, verification, branch
freshness, and shared-context reconciliation. The final epic PR targets the
original target branch after epic validation passes.
