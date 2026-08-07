---
name: wave-driven-development
description: Overview and router for Wave-Driven Development (WDD) — running coding agents on work larger than one prompt via the wddctl controller. Use this first to decide whether WDD applies, to learn the .wdd/ artifact layout and controller/worker/reviewer roles, and to find the right WDD skill (wdd-setup, wdd-intake, wdd-spec-review, wdd-plan, wdd-run, wdd-worker, wdd-review, wdd-status, wdd-runners).
---

# Wave-Driven Development

WDD coordinates multiple coding agents on work that splits into independently
executable tasks. `wddctl` owns every mechanical action — branch and worktree
creation, state transitions, conflict-domain enforcement, merging. These
skills cover only the judgment `wddctl` cannot make for you.

Agents run every `wddctl` command themselves; presenting a command to the
user instead of executing it is a protocol violation. Exceptions: the
human-owned final merge, and anything `merge.mode: human` reserves for
people.

## Talking to the user

`wddctl` output is your instrument panel, not your voice. Report in plain
language: what happened, what is in flight, what needs the user. Revision
numbers, scope IDs, event names, lint codes, config paths, and command
strings are for you and the record — surface one to the user only when
they must act on it personally (a PR to merge, a file to read). "Plan
applied: ten tasks, the scaffold task is starting now, everything else
waits on it" beats a paraphrase of the state file.

Every phase ends with a handoff, not a full stop. When a skill's work is
done, name the natural next step in the user's terms and offer to take it
— "Setup's done. Should I start the intake ladder? Bring a spec or
describe the feature." — instead of waiting to be asked. The user should
never need to know which skill comes next; offering it is your job.

## When to use it

Use WDD when work naturally splits into several tasks that can run
concurrently, or that benefit from a separate reviewer pass. Skip it for one
small, self-contained edit — just make the change directly.

## Artifact layout

```
.wdd/
  constitution.md     # human-authored prose governance
  config.json         # global machine config; edit via wddctl config set
  state.json          # wddctl-owned; never hand-edit
  state.md            # generated projection (wddctl render)
  epics/<slug>/       # the active epic (wddctl epic new); one at a time
    config.json       #   sparse per-epic overrides (config set --epic)
    spec.md           #   intake: agreed goal/scope/numbered acceptance criteria
    design.md         #   intake: components/interfaces/surfaces/deliverable
    plan.json         #   the applied plan, mirrored at plan apply
    tasks/<ID>.md     #   worker briefs
    research/         #   epic-specific research artifacts
  shared-context/     # durable cross-epic discoveries
    contract-inventory.md  # intake research: operation -> shape -> citation
  archive/<slug>/     # retired epics, moved wholesale (wddctl scope archive)
  dispatch/           # transient runner-dispatch scratch, gitignored, never committed
```

Artifact refs never carry the `epics/` prefix — `spec.md`, `tasks/T1.md`,
`research/x.md` always mean the active epic; `shared-context/...` is
always global.

## Roles

- **Controller** — the main agent session, not a subagent: plans,
  dispatches workers, routes review findings, reconciles, merges — and
  keeps the loop alive until the scope is done. Dispatching is not
  delegating the watching, and the controller role itself is never
  dispatched. Never implements task code, never hand-edits `state.json`.
  See `wdd-run`.
- **Worker** — implements exactly one task in its assigned worktree. See
  `wdd-worker`.
- **Reviewer** — reviews one task's diff and classifies findings. See
  `wdd-review`.

## The loop

```
wddctl next                     # what needs doing
<do the one thing needing judgment>
wddctl <verb> --task ID         # record it
```

That is the whole engine. Everything else is judgment:

- No `.wdd/state.json`: run `wddctl init --repo .`, then follow `wdd-setup`.
  Never restore a `.wdd/` that only exists in git history to satisfy this
  check — deleted WDD artifacts are a decommissioned scope, not a shortcut;
  a resurrected state is stale by definition and may predate the current
  schema.
- Open config questions or an unratified constitution: use `wdd-setup`.
- Ladder rungs pending (`create_epic`/`configure_epic`/`agree_spec`/
  `research`/`agree_design` in `next`): use `wdd-intake`.
- Ladder complete but no applied plan, or new work to decompose: use
  `wdd-plan`.
- Plan exists and tasks need dispatching, reviewing, or merging: use
  `wdd-run` (controller), `wdd-worker` (if you are the dispatched worker),
  or `wdd-review` (if you are the dispatched reviewer).
- Scope already `delivered` and there's more work to bring: `wddctl scope
  archive --repo .` first, then `wdd-intake` to start the next scope's
  ladder.
- Mid-epic pivot to genuinely different work: `wddctl epic park` (see
  `wdd-intake`'s "Parking an epic" — pivot-in-place when it's the same
  epic with a new approach).
- A spec, design, plan, or brief needs breaking before someone builds on
  it ("review this spec", "poke holes", a security pass): use
  `wdd-spec-review` — a different model (or the human) as the adversary,
  confirmed with the human every time.
- Just want to know where things stand: use `wdd-status`.
- Registering an external agent CLI as a worker or reviewer ("add a
  runner", "use qwen/codex as a worker", a model value naming a local CLI):
  use `wdd-runners`.

`wdd-sync-github-project` is an optional adapter for mirroring state to a
GitHub Project; use it only if the constitution asks for it.
