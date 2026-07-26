# WDD

Wave-Driven Development is a skill pack plus a small deterministic controller,
`wddctl`, for running coding agents on work larger than one prompt.

`wddctl` owns every mechanical action: branch and worktree creation, state
transitions, conflict-domain enforcement, evidence tracking, and merging.
Skills cover only the judgment `wddctl` cannot make for you — what a task
should contain, whether a diff is correct, when work is actually done. This
split is the whole design: prose can be ignored, a state machine cannot.

## Vision

AI coding agents are getting better at implementation, but large bodies of
work still fail for boring engineering reasons: missing context, unclear
scope, weak review loops, merge chaos, forgotten decisions, and agents losing
track after context compression.

WDD treats agentic development as an engineering workflow, not a prompt
trick. Humans define intent, agents do focused work, and the controller keeps
enough durable state that the whole system stays understandable, reviewable,
and resumable — by a human or by an agent picking the work back up later.

A plan decomposes into tasks with explicit dependencies and conflict domains.
`wddctl` admits each task the moment it is safe to start, tracks it through
review and verification, and merges it once the evidence is in. Shared
context carries durable discoveries across tasks. Nothing about this requires
trusting an agent to remember a rule.

The goal is not to make agents faster at blindly editing files. The goal is
to make parallel agent work controlled enough that a senior engineer can
trust the process, inspect the state, interrupt it, resume it, and review the
result.

## Who WDD is for

WDD is for engineers who want to use coding agents on work larger than a
single prompt or an isolated bug fix: multi-step features, refactors,
migrations, architectural changes, test expansion, bug clusters, parallel
agent experiments, and cloud-agent workflows where context compression and
resumability matter.

It is overkill for small one-shot edits — just make the change directly.

## What changed

Earlier versions of WDD batched tasks into waves: a wave was proposed,
confirmed, activated as a unit, and only reconciled once every task in it
was done. That batching is gone as an execution gate.

`wddctl` now admits each task individually, the moment its own dependencies
are `done` and none of its `conflictDomains` overlap an already-active task.
A task no longer waits for its wave-mates. Two things do the job people
assumed waves were doing:

- **Conflict domains** (glob patterns on `plan.json` tasks) make two tasks
  that touch the same files mutually exclusive. This is enforced inside the
  `task.started` transition itself, not just advertised by `wddctl next` — an
  agent that ignores `next` and tries to start a colliding task is refused by
  the state machine, not by convention.
- **`scope.maxConcurrent`** bounds how many tasks are active at once, which
  is what actually limits rebase churn.

`wddctl plan preview` still projects a round-by-round admission order — it
looks wave-shaped — but it is explicitly a view, not a gate. Nothing waits
for a round to finish.

The other change is scope: there is one doctrine now. Earlier docs described
both a text-only workflow with no CLI and a `wddctl`-based one, side by side.
That's gone — see [`docs/spec-kit-mini-spec-findings.md`](docs/spec-kit-mini-spec-findings.md)
for why.

## Quickstart

Install `wddctl` and the skills (see Installation below), then in a target
repository:

1. Write a plan. `plan.json` is the only planning input:

   ```json
   {
     "schemaVersion": 1,
     "kind": "wdd_plan",
     "scope": {
       "id": "SCOPE-auth-refresh",
       "baseRef": "wdd/auth-refresh",
       "maxConcurrent": 3,
       "reviewPolicy": "risk_based",
       "reconcileEveryNMerges": 3
     },
     "tasks": [
       {
         "id": "TASK-001-token-types",
         "title": "Token type contract",
         "specPath": "tasks/TASK-001-token-types.md",
         "risk": "high",
         "dependsOn": [],
         "conflictDomains": ["src/auth/**", "src/schema.ts"]
       }
     ]
   }
   ```

   Write the referenced task briefs under `.wdd/tasks/`.

2. Apply the plan. This creates `.wdd/state.json` and the scope's base
   branch:

   ```sh
   wddctl plan apply --plan plan.json --repo .
   ```

3. Ratify the constitution. Execution is blocked until this happens:

   ```sh
   wddctl constitution probe --root . --output .wdd/constitution-proposal.json
   wddctl constitution ratify --by "your-name" --proposal .wdd/constitution-proposal.json
   ```

4. Run the loop. This is the whole engine:

   ```sh
   wddctl next                          # what needs doing
   # <do the one thing that needs judgment>
   wddctl <verb> --task TASK-ID         # record it
   ```

   A task with no review requirement typically costs six commands: `start`,
   `submit`, `verify record`, `freshness record`, `merge`, `release`. No
   hand-authored JSON, no revision tracking, no manual `git merge`.

   Want to see the projected schedule first? `wddctl plan preview` shows the
   admission order as rounds — a view, not a gate.

See [`docs/wddctl.md`](docs/wddctl.md) for the full command reference and
[`docs/artifact-schema.md`](docs/artifact-schema.md) for the `.wdd/` file
formats.

For how this actually plays out day to day — the two ways people drive it,
the controller/worker/reviewer split, a gate-by-gate task lifecycle, and
what to do when something goes wrong — see
[`docs/workflow.md`](docs/workflow.md). For a full worked example (a
refresh-token feature, decomposed, planned, run, and merged, including a
reviewer catching a real security bug), see
[`docs/example-auth-refresh.md`](docs/example-auth-refresh.md).

## Installation

Clone the repository:

```sh
git clone https://github.com/ivo-toby/wdd.git
cd wdd
```

### Skills

Copy `skills/*` into the agent's skill directory. For Claude Code:

```sh
mkdir -p ~/.claude/skills   # or /path/to/project/.claude/skills for a project-local install
cp -R skills/* ~/.claude/skills/
```

For Codex:

```sh
mkdir -p ~/.agents/skills   # or /path/to/project/.agents/skills for a project-local install
cp -R skills/* ~/.agents/skills/
```

Restart the agent if newly installed skills don't appear.

To update, `git pull --ff-only` in this clone and repeat the copy. Delete a
stale skill directory first if a release removes or renames one — `cp -R`
does not remove files that no longer exist in the source.

### Controller

Install `wddctl` into a chosen prefix:

```sh
python3 scripts/install_wave_delivery.py --prefix "$HOME/.local"
export PATH="$HOME/.local/bin:$PATH"
wddctl doctor
```

Or run it directly from the clone without installing:

```sh
python3 scripts/wddctl.py doctor
```

`wddctl` requires Python 3.10+ and uses only the standard library — no
dependencies to install.

## Branching model

```text
main
└── wdd/auth-refresh          # scope base branch (plan.json scope.baseRef)
    ├── task/TASK-001-token-types
    ├── task/TASK-002-refresh-route
    └── task/TASK-003-session-ui
```

Task branches are checked out in isolated Git worktrees at
`<repo>.wdd/worktrees/<scope>/<task>` — a sibling of the repository, so a
task's checkout never pollutes the controller's own working tree. Workers
never switch branches in the controller checkout.

Merges into the base happen only through `wddctl merge`, which merges inside
an integration worktree at `<repo>.wdd/integration/<scope>` unless the
controller checkout is already sitting on the base branch. Landing the base
branch itself into `main` is outside `wddctl`'s scope — that's an ordinary
PR once every task is done.
