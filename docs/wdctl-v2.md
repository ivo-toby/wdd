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
```

The in-repository equivalent is `python3 scripts/wdctl.py ...`.

## Guarantees in this slice

- State writes use a same-directory temporary file, `fsync`, and atomic replace.
- Event application takes an exclusive local lock, validates the current
  revision, and records idempotency keys.
- Execution transitions are rejected until an explicit constitution ratification
  records an actor and decision fingerprint.
- Review and verification evidence is tied to a task head SHA; a head update
  invalidates both.
- `next` is read-only and returns concise executable actions and blockers.

## Deferred work

- v1-to-v2 migration and stable task-path conversion.
- Constitution probing and Markdown ratification rendering.
- Git leases, worktree management, monitoring adapters, and review execution.
- Installer-generated `wdctl` POSIX and Windows launchers.
