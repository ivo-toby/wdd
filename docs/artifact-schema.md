# WDD Artifact Schema

WDD artifacts are local text files. Markdown carries human and agent context.
Hand-editable JSON carries resumable orchestration state where structured state
is useful.

The canonical root is `.wdd/`.

## Constitution

Path:

```text
.wdd/constitution.md
```

Required frontmatter:

```yaml
---
id: WDD-CONSTITUTION
kind: constitution
version: 1.0.0
status: active
ratified: YYYY-MM-DD
last_amended: YYYY-MM-DD
---
```

Required body sections:

- Project Scope
- Setup Configuration
- Model Usage
- Storage Mode
- Branching Policy
- Review Policy
- Verification Policy
- Agent Roles
- Planning Rules
- Task Rules
- Wave Rules
- Shared Context Rules
- Governance

The constitution stores user choices that must not be rediscovered by later
agents, including model aliases, target branch, branch naming conventions,
review blocking policy, feedback-fix preference, and patch fallback policy.

## Epic

Path:

```text
.wdd/epics/EPIC-auth-refresh/epic.md
```

Required frontmatter:

```yaml
---
id: EPIC-auth-refresh
kind: epic
type: feature
slug: auth-refresh
title: Auth Refresh
status: draft
created_at: YYYY-MM-DD
updated_at: YYYY-MM-DD
target_branch: main
epic_branch: epic/auth-refresh
schema_version: 1
ticket_count: 0
task_count: 0
adapter_links:
  github_issue: null
  jira_epic: null
---
```

Required body sections:

- Summary
- Goal
- Background
- Product Context
- Technical Context
- Deliverables
- Non-Goals
- Assumptions
- Constraints
- Risks
- Dependencies
- Affected Areas
- Validation Strategy
- Definition of Done
- Open Questions
- Planning Notes

The epic is ready for planning only when deliverables are concrete, scope
boundaries are explicit, and the definition of done is testable.

## GitHub Project Adapter Manifest

Optional path:

```text
.wdd/epics/EPIC-auth-refresh/adapters/github-project.json
```

Required shape when present:

```json
{
  "schemaVersion": 1,
  "updatedAt": "YYYY-MM-DDTHH:MM:SSZ",
  "epic": {
    "id": "EPIC-auth-refresh"
  },
  "project": {
    "owner": "OWNER",
    "number": 4,
    "id": "PVT_...",
    "title": "Auth Refresh",
    "url": "https://github.com/orgs/OWNER/projects/4",
    "repo": "OWNER/REPO",
    "wdd_id": "EPIC-auth-refresh"
  },
  "items": {
    "TICKET-001-token-contract": {
      "kind": "ticket",
      "localPath": "TICKET-001-token-contract/ticket.md",
      "github": {
        "itemId": "PVTI_...",
        "issueNumber": 123,
        "url": "https://github.com/OWNER/REPO/issues/123"
      },
      "fingerprints": {
        "local": "sha256:...",
        "remote": "sha256:..."
      }
    }
  }
}
```

The manifest belongs to optional adapter state. It records GitHub Project links
and sync fingerprints so `wdd-sync-github-project` can detect local-only,
remote-only, and conflicting changes without making GitHub Projects the WDD
source of truth.

## Ticket Folder

Path:

```text
.wdd/epics/EPIC-auth-refresh/TICKET-001-token-contract/ticket.md
```

Required frontmatter:

```yaml
---
id: TICKET-001-token-contract
kind: ticket
epic: EPIC-auth-refresh
slug: token-contract
title: Token Contract
status: planned
task_count: 2
depends_on: []
conflict_domains:
  - src/auth/**
adapter_links:
  github_issue: null
---
```

Required body sections:

- Summary
- Objective
- Scope
- Non-Scope
- Shared Context References
- Task Inventory
- Dependencies
- Conflict Domains
- Validation Expectations
- Review Focus
- Completion Criteria

Tickets are containers. They group related tasks but are not assigned directly
to worker agents.

## Task

Path:

```text
.wdd/epics/EPIC-auth-refresh/TICKET-001-token-contract/todo/TASK-001-token-types.md
```

Required frontmatter:

```yaml
---
id: TASK-001-token-types
kind: task
epic: EPIC-auth-refresh
ticket: TICKET-001-token-contract
wave: WAVE-001
slug: token-types
title: Token Types
status: todo
depends_on: []
conflict_domains:
  - src/auth/token-types.ts
assigned_model_class: simple-implementation
review_model_class: review
branch: task/TASK-001-token-types
worker_worktree: null
worktree_status: unassigned
pr: null
current_gate: not_started
branch_freshness: unknown
verification:
  - project-specific verification command
---
```

Required body sections:

- Status
- Parent Ticket
- Wave
- Objective
- Scope
- Non-Scope
- Relevant Context
- Likely Files / Areas
- Dependencies
- Conflict Domains
- Assigned Model Class
- Branch
- Worker Worktree
- PR / Patch Reference
- RED-GREEN TDD Plan
- Implementation Notes
- Durable Memory Notes To Consider
- Task-Level Definition of Done
- Validation Steps
- Verification Evidence
- Review Feedback
- Completion Notes

Task files are the worker implementation briefs. A task file moves through
`todo/`, `in-progress/`, `review/`, `done/`, `blocked/`, and `cancelled/` as
the durable visible state.

## Shared Context

Index path:

```text
.wdd/epics/EPIC-auth-refresh/shared-context/index.md
```

Resource path:

```text
.wdd/epics/EPIC-auth-refresh/shared-context/resources/architecture.md
```

The index must include:

- Overview
- Resource Index
- When To Read Each Resource
- Key Decisions
- Key Warnings
- Known Constraints
- Recent Durable Memory

Resource files are focused and scannable. Worker agents may propose updates in
their task branches. The controller reconciles shared-context changes into the
epic branch, especially when concurrent workers touch the same resource.

Durable memory items use:

```markdown
### Short Title

- Source task: TASK-001-token-types
- Source PR/branch: task/TASK-001-token-types
- Status: confirmed | inferred | needs verification
- Summary:
- Why it matters:
- Affected files or areas:
- Follow-up implications:
```

## Wave Plan

Path:

```text
.wdd/epics/EPIC-auth-refresh/wave-plan.md
```

Required frontmatter:

```yaml
---
id: EPIC-auth-refresh-WAVES
kind: wave_plan
epic: EPIC-auth-refresh
status: planned
created_at: YYYY-MM-DD
updated_at: YYYY-MM-DD
---
```

Required body sections:

- Task Inventory
- Dependency Grid
- Conflict Grid
- Waves
- Activation Rules
- Stop Conditions
- Known Conflict Risks
- Manual Adjustments

Waves schedule tasks, not tickets. A wave is activated as a batch of
concurrently eligible tasks. Eligibility requires no unresolved dependency, no
active conflict-domain blocker, no stale prerequisite, and no explicit blocked
status.

## Orchestration JSON

Path:

```text
.wdd/epics/EPIC-auth-refresh/orchestration.json
```

Required top-level field:

```json
{
  "schemaVersion": 1
}
```

Minimum structure:

```json
{
  "schemaVersion": 1,
  "epic": {
    "id": "EPIC-auth-refresh",
    "name": "Auth Refresh",
    "targetBranch": "main",
    "baseBranch": "epic/auth-refresh"
  },
  "configuration": {
    "storageMode": "local-markdown",
    "models": {
      "planning": "configured-model-key",
      "implementationSimple": "configured-model-key",
      "implementationComplex": "configured-model-key",
      "review": "configured-model-key",
      "feedbackFix": "configured-model-key",
      "epicValidation": "configured-model-key",
      "prDescription": "configured-model-key"
    },
    "branching": {
      "epicBranchConvention": "epic/[epic-slug]",
      "taskBranchConvention": "task/[task-id]-[task-slug]",
      "taskPrTarget": "epic branch",
      "finalPrTarget": "target branch",
      "epicBranchRequiredBeforeWorkerDispatch": true,
      "activationArtifactsSyncedBeforeTaskWorktrees": true,
      "isolatedWorktreePerRepositoryTask": true,
      "workersMaySwitchControllerCheckout": false
    }
  },
  "waves": [
    {
      "id": "WAVE-001",
      "status": "planned",
      "tasks": [
        {
          "id": "TASK-001-token-types",
          "ticket": "TICKET-001-token-contract",
          "path": "TICKET-001-token-contract/todo/TASK-001-token-types.md",
          "status": "todo",
          "dependsOn": [],
          "conflictDomains": ["src/auth/token-types.ts"],
          "assignedModel": "configured-model-key",
          "reviewModel": "configured-model-key",
          "workerThreadId": null,
          "reviewThreadId": null,
          "branch": "task/TASK-001-token-types",
          "workerWorktree": null,
          "worktreeStatus": "unassigned",
          "pr": null,
          "latestCommit": null,
          "branchFreshness": "unknown",
          "blockingFeedback": [],
          "verification": null,
          "currentGate": "not_started"
        }
      ]
    }
  ],
  "monitoring": {
    "mode": "manual",
    "cadence": "5m",
    "status": "inactive",
    "lastCheckedAt": null,
    "nextCheckDueAt": null,
    "schedulerRef": null,
    "fallbackPrompt": "Run subagent-pr-orchestration for EPIC-auth-refresh WAVE-001. Read orchestration.json and controller-state.md, verify the epic branch contains current activation artifact state before assigned worker worktrees branch from it, inspect every active worker and reviewer reference, update task gates, and stop when all active tasks are merged, blocked, cancelled, or ready for wdd-reconcile-wave."
  }
}
```

The controller updates this file after task assignment, task movement, epic
branch creation or verification, task branch creation or verification, worker
worktree creation or verification, PR or patch creation, review start, P1/P2
feedback, feedback routing, verification, stale-branch checks, merge, blocker,
wave completion, and reconciliation.

Before any worker starts, the controller must create or verify the epic branch.
Before dispatching parallel repository-writing workers, it must sync activation
artifact changes to the epic branch, create or verify one isolated worktree per
task from that synced state, record the assigned path, and tell each worker to
start there. Workers must not switch branches in the controller checkout.

The `monitoring` object records how the controller heartbeat is driven. Allowed
`mode` values are:

- `codex_thread_heartbeat`: Codex thread automation attached to the active
  controller conversation.
- `claude_loop`: Claude Code `/loop` or scheduled task in the active local
  session.
- `external_scheduler`: durable external runner such as a desktop scheduled
  task, cloud routine, GitHub Actions schedule, or equivalent project adapter.
- `manual`: no scheduler is available; the controller records an exact fallback
  prompt and due time for a human or fresh agent to resume.

Every heartbeat tick must be bounded and idempotent: load current artifacts,
poll worker and reviewer references, advance gates, update artifacts, and stop
or deactivate monitoring when all active-wave tasks are merged, blocked,
cancelled, or ready for wave reconciliation. Monitoring must not depend on
hidden conversation state.

## Controller State

Path:

```text
.wdd/epics/EPIC-auth-refresh/controller-state.md
```

Required body sections:

- Controller Rule
- Active Wave
- Monitoring
- Active Task Gates
- Worker Worktrees
- Branch Freshness
- Open P1/P2 Feedback
- Verification Status
- Shared Context Reconciliation
- Event Log
- Next Action

The controller state is human-readable. It must show the assigned worktree path
for every active repository-writing task before that worker starts.
`orchestration.json` is the machine-readable resume surface.

## Epic Validation

Path:

```text
.wdd/epics/EPIC-auth-refresh/epic-validation.md
```

Required body sections:

- Validation Summary
- Epic Definition Of Done
- Deliverable Checklist
- Task State Audit
- Review Audit
- Verification Evidence
- Shared Context Audit
- Monitoring Audit
- Integration Risks
- Branch State
- Result

Epic validation happens only after all waves are complete.

## Final PR

Path:

```text
.wdd/epics/EPIC-auth-refresh/final-pr.md
```

Required body sections:

- PR Title
- Epic Summary
- Completed Deliverables
- Definition Of Done Checklist
- Validation Evidence
- Test Results
- Wave Summary
- Task Summary
- Review Summary
- Known Limitations
- Risks
- Follow-Up Tasks
- Documentation Updates
- References

The final PR targets the original target branch and is prepared only after
epic validation passes.
