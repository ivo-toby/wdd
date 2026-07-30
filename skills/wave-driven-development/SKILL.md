---
name: wave-driven-development
description: Overview and router for Wave-Driven Development (WDD) — running coding agents on work larger than one prompt via the wddctl controller. Use this first to decide whether WDD applies, to learn the .wdd/ artifact layout and controller/worker/reviewer roles, and to find the right WDD skill (wdd-plan, wdd-run, wdd-worker, wdd-review, wdd-status, wdd-setup).
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

## When to use it

Use WDD when work naturally splits into several tasks that can run
concurrently, or that benefit from a separate reviewer pass. Skip it for one
small, self-contained edit — just make the change directly.

## Artifact layout

```
.wdd/
  constitution.md     # human-authored prose governance
  config.json         # machine config; edit via wddctl config set
  plan.json           # the only planning input
  state.json          # wddctl-owned; never hand-edit
  state.md            # generated projection (wddctl render)
  tasks/<TASK-ID>.md  # worker briefs
  shared-context/     # durable discoveries
```

## Roles

- **Controller** — plans, dispatches workers, routes review findings,
  reconciles, merges. Never implements task code, never hand-edits
  `state.json`. See `wdd-run`.
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
- No `.wdd/plan.json`, or new work to decompose: use `wdd-plan`.
- Plan exists and tasks need dispatching, reviewing, or merging: use
  `wdd-run` (controller), `wdd-worker` (if you are the dispatched worker),
  or `wdd-review` (if you are the dispatched reviewer).
- Just want to know where things stand: use `wdd-status`.

`wdd-sync-github-project` is an optional adapter for mirroring state to a
GitHub Project; use it only if the constitution asks for it.
