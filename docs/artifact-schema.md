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
- WDD Profile Defaults
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
default WDD profile, review blocking policy, feedback-fix preference, and patch
fallback policy.

Recommended profile fields in the body:

```yaml
wdd_profile_default: standard
allowed_profiles:
  - micro
  - lite
  - standard
  - full
review_mode_default: risk_based
monitoring_default: adaptive
```

Profiles mean:

- `micro`: bounded ticket-sized work under `.wdd/work/`.
- `lite`: compact epic artifacts, adaptive monitoring, risk-based review.
- `standard`: normal epic workflow with token-conscious defaults.
- `full`: maximum ceremony for high-risk work.

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
profile: standard
review_mode: risk_based
monitoring_mode: adaptive
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
boundaries are explicit, and the definition of done is testable. The `profile`
field records the chosen ceremony level. User input overrides the constitution
default when creating the epic; later agents honor the recorded epic value.

## Micro-Wave Work Packet

Use micro-waves for a single chunky ticket or bounded request that can split
into 2-5 parallel tasks but does not need epic, ticket, wave-plan, validation,
and final-PR ceremony.

Path:

```text
.wdd/work/WORK-filter-builder/brief.md
```

Required frontmatter:

```yaml
---
id: WORK-filter-builder
kind: work_packet
profile: micro
slug: filter-builder
title: Filter Builder
status: draft
created_at: YYYY-MM-DD
updated_at: YYYY-MM-DD
target_branch: main
base_branch: work/filter-builder
schema_version: 1
task_count: 0
adapter_links:
  github_issue: null
  jira_issue: null
---
```

Required body sections:

- Summary
- Goal
- Scope
- Non-Scope
- Relevant Context
- Parallelization Notes
- Validation Strategy
- Definition of Done
- Open Questions
- Finish Notes

Micro-wave task path:

```text
.wdd/work/WORK-filter-builder/tasks/TASK-001-api-contract.md
```

Micro-wave task files are compact implementation briefs. Required body sections:

- Objective
- Scope
- Context To Read
- Likely Files
- Dependencies
- Conflict Domains
- Validation
- Done
- Evidence

Micro-wave state path:

```text
.wdd/work/WORK-filter-builder/state.json
```

Minimum structure:

```json
{
  "schemaVersion": 1,
  "kind": "micro_wave_state",
  "profile": "micro",
  "work": {
    "id": "WORK-filter-builder",
    "title": "Filter Builder",
    "targetBranch": "main",
    "baseBranch": "work/filter-builder"
  },
  "configuration": {
    "reviewMode": "risk_based",
    "monitoringMode": "adaptive",
    "maxParallelTasks": 5
  },
  "tasks": [
    {
      "id": "TASK-001-api-contract",
      "path": "tasks/TASK-001-api-contract.md",
      "status": "todo",
      "branch": "work/TASK-001-api-contract",
      "workerWorktree": null,
      "currentGate": "not_started",
      "risk": "low",
      "reviewRequired": false,
      "verification": null
    }
  ],
  "monitoring": {
    "mode": "manual",
    "cadence": "adaptive",
    "status": "inactive",
    "lastCheckedAt": null,
    "nextCheckDueAt": null,
    "schedulerRef": null,
    "fallbackPrompt": "Run wdd-run-work for WORK-filter-builder. Read state.json and task files, inspect active worker or PR references, update gates, and stop when tasks are ready for wdd-finish-work."
  }
}
```

Micro-waves keep the WDD safety core: bounded scope, branch/worktree isolation
for repository-writing workers, verification evidence, no worker self-merge,
and a final controller handoff. They intentionally omit ticket containers,
separate wave plans, epic validation, and final PR artifacts unless the user
asks to upgrade the work packet into an epic.

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

Required body sections for `standard` and `full` task files:

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

Allowed compact body sections for `lite` task files:

- Objective
- Scope
- Context To Read
- Likely Files
- Dependencies And Conflicts
- TDD And Validation
- Done
- Evidence

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
    "profile": "standard",
    "reviewMode": "risk_based",
    "monitoringMode": "adaptive",
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
    "cadence": "adaptive",
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

When `mode` is `codex_thread_heartbeat`, `schedulerRef` must identify a
verified active Codex heartbeat automation. If the controller cannot create,
update, or verify that heartbeat before ending its turn, it must select the next
scheduler option or downgrade to `manual` and record the failed scheduler
attempt in controller state.

Every heartbeat tick must be bounded and idempotent: load current artifacts,
poll worker and reviewer references, advance gates, update artifacts, and stop
or deactivate monitoring when all active-wave tasks are merged, blocked,
cancelled, or ready for wave reconciliation. Monitoring must not depend on
hidden conversation state.

When monitoring mode is `adaptive`, default cadence is based on gate activity:

- `no_pr`: slower polling, usually 15-30 minutes.
- `needs_review`, `reviewing`, `needs_fixes`, or `merge_ready`: faster polling,
  usually around 5 minutes.
- repeated no-change ticks: downgrade to manual fallback when safe.

The cadence changes do not relax heartbeat verification. A recorded
`codex_thread_heartbeat` still requires a verified scheduler reference.

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
