# wdctl schema-v2 foundation

`wdctl` is an experimental, dependency-free controller for the mechanical part
of WDD. It does not replace the portable schema-v1 skill workflow yet.

For a schema-v2 scope, Markdown remains the human-authored specification while
the state file is the canonical source for revisions, task state, evidence, and
event history. Generated dashboards are projections and must not be edited.

## Current commands

```text
python3 -m wave_delivery init --state PATH --scope-id ID --task TASK-ID
python3 -m wave_delivery constitution ratify --state PATH --by NAME \
  --decision-fingerprint SHA256 --idempotency-key KEY --expected-revision 0
python3 -m wave_delivery event apply --state PATH --event task.started \
  --task TASK-ID --idempotency-key KEY --expected-revision 1
python3 -m wave_delivery next --state PATH
python3 -m wave_delivery status --state PATH --brief
python3 -m wave_delivery render --state PATH --output controller-state.md
python3 -m wave_delivery migrate --state .wdd/epics/EPIC/orchestration.json --to 2 --dry-run
python3 -m wave_delivery constitution probe --root . --output constitution-proposal.json
python3 -m wave_delivery lease ensure --state PATH --repo REPO --task TASK-ID \
  --idempotency-key KEY --expected-revision N
python3 -m wave_delivery freshness check --repo REPO --base epic/example --head task/TASK-ID
python3 -m wave_delivery monitor --once --state PATH --repo REPO
python3 -m wave_delivery review collect --state PATH --task TASK-ID --result review-a.json \
  --idempotency-key KEY --expected-revision N
python3 -m wave_delivery doctor --json
python3 scripts/install_wave_delivery.py --prefix /chosen/install/path
```

The in-repository equivalent is `python3 scripts/wdctl.py ...`.

The installer deliberately requires an explicit prefix. It copies the package to
`<prefix>/lib`, then writes both `<prefix>/bin/wdctl` and
`<prefix>/bin/wdctl.cmd`; the former invokes `python3 -m wave_delivery`, and
the latter invokes `python -m wave_delivery`.

## Guarantees in this slice

- State writes use a same-directory temporary file, `fsync`, and atomic replace.
- Event application takes an exclusive local lock, validates the current
  revision, and records idempotency keys.
- Execution transitions are rejected until an explicit constitution ratification
  records an actor and decision fingerprint.
- Review and verification evidence is tied to a task head SHA; a head update
  invalidates both.
- `next` is read-only and returns concise executable actions and blockers.
- Migration is dry-run-first, copies original state and task files into a local
  backup directory before applying stable task paths, and can be rolled back.
- Constitution probing gathers evidence but never ratifies; proposal fingerprints
  make ratification drift visible through `constitution status`.
- Lease acquisition creates or reuses one isolated Git worktree per task and
  records it atomically. Release refuses to remove a dirty worktree.
- Freshness uses `git merge-tree`, changed-file overlap, and conflict domains to
  distinguish current, nonmaterially stale, materially stale, and conflicted branches.
- Monitoring observes local Git branches and worktrees without invoking a model,
  and only writes state when observations change.
- Review collection validates normalized results, freezes base/head SHA evidence,
  and aggregates multiple reviewer outputs in one state transition.

## Deferred work

- Markdown constitution rendering and mandatory stale-proposal enforcement for
  every execution adapter.
- External scheduler adapters and verification command execution.
- Installer-generated `wdctl` POSIX and Windows launchers.
