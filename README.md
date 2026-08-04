# WDD

Wave-Driven Development is a skill pack plus a small deterministic controller,
`wddctl`, for running coding agents on work larger than one prompt.

> **Experimental.** WDD is young and under active redesign — shaped by
> running it on real projects and turning every observed failure into an
> enforced rule. It works, it is tested, and it will change under you.
> [Why it exists and what building it taught us](docs/why.md) is the
> honest long-form version.

`wddctl` owns every mechanical action: branch and worktree creation, state
transitions, conflict-domain enforcement, evidence tracking, and merging.
Skills cover only the judgment `wddctl` cannot make for you — what a task
should contain, whether a diff is correct, when work is actually done. This
split is the whole design: prose can be ignored, a state machine cannot.

## Documentation map

- [Why WDD exists, and what building it taught us](docs/why.md) — the
  reasoning and the benchmark learnings. Start here if you're deciding
  whether this is for you.
- [The developer workflow](docs/workflow.md) — the full lifecycle,
  narrated with real transcripts.
- [Worked example](docs/example-auth-refresh.md) — one small feature,
  request to merge.
- [`wddctl` reference](docs/wddctl.md) — every verb, with captured
  output.
- [Artifact schema](docs/artifact-schema.md) — every file format under
  `.wdd/`.

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

A scope's own lifecycle ends the same way, one level up: once every task is
merged, `wddctl` runs the whole epic branch through a final review and
verification, then waits — a scope reaches `delivered` only on an observed
human merge into the target branch, never one `wddctl` performs itself.

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

Earlier versions batched tasks into waves — proposed, confirmed, activated
as a unit, reconciled only once every task in it was done. That batching is
gone as an execution gate. `wddctl` now admits each task the moment its own
dependencies are `done` and none of its `conflictDomains` overlap an
already-active task; a task no longer waits for its wave-mates. Two things
do the job waves used to do:

- **Conflict domains** (glob patterns on `plan.json` tasks) make two tasks
  that touch the same files mutually exclusive, enforced inside the
  `task.started` transition itself — an agent that ignores `next` and tries
  to start a colliding task is refused by the state machine, not by
  convention.
- **`scope.maxConcurrent`** bounds how many tasks are active at once, which
  is what actually limits rebase churn.

`wddctl plan preview` still projects a round-by-round admission order — it
looks wave-shaped — but it is explicitly a view, not a gate; nothing waits
for a round to finish.

The other change is scope, not mechanics: earlier docs described both a
text-only workflow with no CLI and a `wddctl`-based one, side by side.
That's gone — prose-only choreography kept failing in exactly the ways a
state machine can't, which is the founding observation of the whole design.

## Getting started

The normal way to use WDD is to talk to a coding agent that has the skills
installed. You never type `wddctl` yourself — the skills obligate the agent
to run it, and the state machine keeps the agent honest. Your side of the
conversation is judgment: answering setup questions, agreeing on scope,
approving the plan, and merging the final PR.

Install the skills and `wddctl` (see [Installation](#installation)), open an
agent session in the target repository, and drive it with prose:

1. **Set up.** Say: *"Set up WDD in this repo."*
   The agent runs `wddctl init`, which scaffolds `.wdd/` and leaves a short
   list of open questions. The agent asks you all of them in one round —
   which merge surface (real PRs or fully local), the verification command
   if it couldn't be probed — then shows you the config and constitution
   and asks you to sign off. Ratification is the first gate: nothing
   executes until you have approved.

2. **Bring the work.** Say: *"Let's build \<feature\> — here's the spec"*
   (a doc, an issue, a design note; if there is no spec yet, write one
   first — that part is still your job).
   The agent names the work as an **epic** and walks an intake ladder with
   you: agree the spec (numbered, checkable acceptance criteria), do the
   research (contracts cited from files actually read — never from
   memory), agree the design. Every rung ends with your explicit sign-off,
   fingerprint-bound to the exact bytes you approved. Then it decomposes
   the epic into tasks with dependencies and conflict domains, lints the
   plan for the classic failure modes (serialized chains, everything
   high-risk, per-file domain lists), and presents the plan plus the
   projected schedule. Nothing is applied until you approve — your name
   goes into the record.

3. **Let it run.** Say: *"Run the scope."*
   The controller loop dispatches a worker per task (test-first, in an
   isolated worktree), routes each diff through review, blocks merges on
   P1/P2 findings, and merges tasks as their evidence lands. On the `pr`
   surface every task is a real pull request; in `pr`/`human` mode nothing
   merges until a person clicks the button. Interrupt with prose any time:
   "block TASK-004, we're rethinking the UI."

4. **Finish.** When the last task merges, the agent walks the finalize
   ladder: a final review of the whole epic branch against `.wdd/spec.md`,
   full verification, and a handoff PR from the epic branch into your
   target branch. **The final merge is yours** — `wddctl` has no code path
   to perform it, and the scope only reaches `delivered` once Git proves
   you did.

"Where are we?" works at any point — the agent reads the state and reports
the phase, active tasks, and blockers. For the full day-to-day narrative,
see [`docs/workflow.md`](docs/workflow.md).

## Quickstart (driving wddctl yourself)

Prefer the terminal — or scripting a CI step? The same flow, verb by verb.
Install `wddctl` and the skills (see Installation below), then in a target
repository:

1. Initialize. This scaffolds `.wdd/` — machine config with probed defaults,
   a prose constitution draft, and controller state:

   ```sh
   wddctl init --repo .
   ```

2. Follow the controller. `wddctl next` names each remaining setup step —
   resolve the open config questions (`wddctl config set merge.surface pr`),
   then ratify:

   ```sh
   wddctl next
   wddctl constitution ratify --by "your-name"
   ```

3. Plan. Write `plan.json` and the task briefs (see `skills/wdd-plan`), then:

   ```sh
   wddctl plan apply --plan plan.json --repo .
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

`merge.surface` and `merge.mode` (both set via `wddctl config set`, and
overridable per scope from `plan.json`) pick which of three setups fits a
given repository: `local`/`controller` keeps the whole loop offline in
`state.json` — good for a solo project or a repo with no PR workflow at
all; `pr`/`controller` pushes each task's branch, opens a real PR via `gh`,
mirrors review findings as PR comments, and pushes the merged base back to
`origin` once `wddctl merge` lands it — good for a team that wants the
normal GitHub review surface without giving up the mechanical merge; and
`pr`/`human` does the same push-and-PR dance but refuses to merge
automatically, instead surfacing `await_human_merge` until a person merges
the PR themselves and `wddctl merge --observed` proves it happened in Git
— good for repos where a human must be the one to click "Merge." See
[`docs/wddctl.md`](docs/wddctl.md#merge-surfaces-and-modes) for the full
matrix, mode-by-mode command behavior, and real transcripts.

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

### Upgrading

Releases are tagged (`vX.Y.Z`, semver, automated on every merge to main);
`wddctl --version` tells you what you're running. To upgrade the tools:
`git pull --ff-only` in the clone, re-run the installer, and re-copy the
skills (delete removed skill directories first — `cp -R` does not prune).

Repositories with an existing `.wdd/` may also need a **state migration**
after a schema-changing upgrade. You'll know: every command refuses with a
schema-version error until you migrate. The conversion is explicit, backs
up your state file first, and never runs implicitly:

```sh
wddctl migrate --dry-run   # show what would change
wddctl migrate --apply     # convert (state.json is backed up beside itself)
```

Migrated scopes keep working mid-flight — recorded approvals and evidence
survive, exemptions are stamped where a new gate would otherwise demand
records that predate it, and drift detection stays live. Repositories from
before the config/constitution split additionally need
`wddctl migrate --governance` (this deliberately invalidates ratification;
the agent walks you through re-approval).

## Branching model

```text
main
└── wdd/auth-refresh          # scope base branch (plan.json scope.baseRef)
    ├── task/TASK-001-token-types
    ├── task/TASK-002-refresh-route
    └── task/TASK-003-session-ui
```

Task branches are checked out in isolated Git worktrees, by default at
`<repo>/.worktrees/<scope>/<task>` — inside the repository, but gitignored
automatically, so a task's checkout never pollutes the controller's own
working tree or gets committed by accident. The location is a config key
(`worktrees.root`, default `.worktrees`); set it to an absolute path to move
worktrees outside the repository entirely. Workers never switch branches in
the controller checkout.

Merges into the base happen only through `wddctl merge`, which merges inside
an integration worktree at `<repo>.wdd/integration/<scope>` — always a
sibling of the repository, unaffected by `worktrees.root` — unless the
controller checkout is already sitting on the base branch. Landing the base
branch itself into the target branch is the finalize ladder's job, not an
afterthought: once every task merges, `wddctl` walks the scope through a
final review and full verification, then `wddctl finalize handoff` pushes
the base branch and opens the epic→target PR itself (`pr` surface) or hands
you the exact push-and-PR steps (`local` surface). The one thing `wddctl`
never does is click "Merge" — `finalize delivered` only records that a
human did, once Git proves it.
