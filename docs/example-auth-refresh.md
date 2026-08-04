# Worked example: refresh-token support

A complete run of a small, realistic feature from request to a delivered
merge into `main`, using real `wddctl` output captured against a scratch
repository with the current, installed `wddctl` (schema v6). Where output
is long, it's trimmed with `...`; nothing here is invented. See
[`docs/why.md`](why.md) for why WDD exists at all, and
[`docs/workflow.md`](workflow.md) for the mechanics this example leans on
without re-explaining.

## The request

The developer says, roughly:

> Our sessions just expire and kick people to the login screen. Add refresh
> tokens so a user's session extends silently instead of dying on them.

That's it — no task breakdown, no file list. Turning this into a plan is
the judgment `wdd-intake` and `wdd-plan` cover.

## Setting up the epic

Before any spec gets written, the work needs a name. `wddctl epic new`
creates the epic directory and makes it the active one; `wddctl intake
configure` settles this epic's config overrides (or explicitly inherits
every default) before anything else is agreed. Both are one-line, low-
judgment steps — the real conversation starts at the spec:

```
$ wddctl epic new --slug auth-refresh --title "Refresh token support"
{"duplicate": false, "epic": "auth-refresh", "revision": 2}
$ wddctl intake configure --use-defaults --by ivo
{"duplicate": false, "revision": 3, "sha256": "sha256:1d7a061e..."}
```

`spec.md`, `design.md`, and the task briefs now live under
`.wdd/epics/auth-refresh/` — `wddctl` resolves the same relative names
(`spec.md`, `tasks/TASK-001-token-types.md`) into that directory
automatically; nothing in `plan.json` or a brief needs the epic slug
spelled out. `wddctl next` names the spec next:

```
$ wddctl intake spec --approved-by ivo
{"criteria": 2, "duplicate": false, "revision": 4}
```

`spec.md`'s acceptance criteria: AC-1, a 401 recovers silently when a valid
refresh token exists; AC-2, a reused refresh token revokes every session
for that user, not just the reused one. Research is skipped, explicitly and
attributed — refresh tokens are an internal contract this epic defines
itself, nothing external to reverse-engineer:

```
$ wddctl intake research --skip --by ivo --reason "no external API to reverse-engineer; refresh tokens are an internal contract this epic defines itself"
{"duplicate": false, "revision": 5}
```

`design.md` names the components (token issuance, the refresh endpoint,
the client retry, the session banner, and the shared event bus that
connects the last two — see "the part that actually matters" below) and
the epic deliverable — the command that proves the whole thing works:

```
$ wddctl intake design --approved-by ivo --deliverable-command "python3 -c \"print('refresh cycle: 401 -> POST /auth/refresh -> retried request succeeds')\""
{"duplicate": false, "revision": 6}
```

Every rung is bound to the exact bytes it approved; editing `spec.md` or
`design.md` after this point reopens that rung and cascades downstream.
[`docs/wddctl.md`](wddctl.md#the-intake-ladder) has the full ladder
reference, including drift and cascade.

## The decomposition judgment

"Add refresh tokens" touches four genuinely different concerns, and they
split along natural seams:

1. **The token contract itself** — what a refresh token *is*, how it's
   issued. Every other task depends on this existing first.
2. **The server endpoint** that exchanges a refresh token for a new access
   token. Needs the contract from (1).
3. **The client-side retry** — catch a 401, call the refresh endpoint
   silently, retry the original request. Needs the contract from (1) to
   know what it's sending, but not the server route itself (it just calls
   an HTTP endpoint).
4. **The UI messaging** for when refresh has failed and the session is
   really over. Doesn't need the token contract or the endpoint at all —
   it just needs to know *that* the session ended, not *why*.

That's the split. Each is one worker, one branch, one diff, one merge, and
each has an objective nobody would need to argue about.

**Now the part that actually matters: conflict domains.** The naive
version of tasks 3 and 4 is "task 3 touches the HTTP client, task 4 touches
the banner component" — disjoint files, fully parallel, nothing to think
about. That's wrong, and it's wrong in the specific way `wdd-plan` warns
about: *too narrow, omitting a file a task actually touches.*

Task 3 (retry) is the thing that discovers "refresh failed, the session is
dead." Task 4 (banner) is the thing that needs to *know* that happened.
Something has to carry that fact between them. The natural answer is a
small client-side event bus — task 3 emits `session-expired`, task 4
subscribes to it. That module, `src/client/session-events.ts`, is written
by whichever of the two tasks lands first and *used* by whichever lands
second. If both tasks' `conflictDomains` omit it, nothing stops both
workers from creating it independently with incompatible shapes, and one
worker's version silently disappears at merge — exactly the failure mode
`wdd-plan` describes for domains drawn too narrow.

The fix is to list `src/client/session-events.ts` in *both* tasks'
`conflictDomains`. This means task 3 and task 4 — which have no
`dependsOn` relationship, no reason either would wait for the other on
paper — cannot be admitted concurrently. Whichever starts first holds the
domain until it merges; the other is blocked for that whole time, not
because of a missing dependency edge but because they'd otherwise write
the same file. That is the correct outcome, not a scheduling defect to
route around by, say, inventing an artificial `dependsOn` to make the
serialization "official." The domain overlap already says everything a
dependency edge would say, and says it more precisely: it's not that task
4 conceptually follows task 3, it's that they share exactly one file and
nothing else.

Risk: tasks 1 and 2 touch token issuance and the auth endpoint —
`"risk": "high"`, reviewed regardless of policy defaults. Tasks 3 and 4
are `"risk": "normal"` — a client retry loop and a banner are not where a
bad merge is expensive to unwind.

## The plan

```json
{
  "schemaVersion": 1,
  "kind": "wdd_plan",
  "scope": {
    "id": "SCOPE-auth-refresh",
    "baseRef": "wdd/auth-refresh",
    "maxConcurrent": 3,
    "reviewPolicy": "risk_based",
    "reconcileEveryNMerges": 2
  },
  "tasks": [
    {
      "id": "TASK-001-token-types",
      "title": "Refresh token type contract",
      "specPath": "tasks/TASK-001-token-types.md",
      "risk": "high",
      "dependsOn": [],
      "conflictDomains": ["src/auth/tokens.ts", "src/session/types.ts"],
      "context": ["spec.md"]
    },
    {
      "id": "TASK-002-refresh-endpoint",
      "title": "POST /auth/refresh endpoint",
      "specPath": "tasks/TASK-002-refresh-endpoint.md",
      "risk": "high",
      "dependsOn": ["TASK-001-token-types"],
      "conflictDomains": ["src/api/auth_routes.ts", "src/session/types.ts"],
      "context": ["spec.md#AC-2"]
    },
    {
      "id": "TASK-003-client-retry",
      "title": "Client-side silent retry on 401",
      "specPath": "tasks/TASK-003-client-retry.md",
      "risk": "normal",
      "dependsOn": ["TASK-001-token-types"],
      "conflictDomains": ["src/client/http.ts", "src/client/session-events.ts"],
      "context": ["spec.md#AC-1"]
    },
    {
      "id": "TASK-004-session-ui",
      "title": "Session expiry UI messaging",
      "specPath": "tasks/TASK-004-session-ui.md",
      "risk": "normal",
      "dependsOn": [],
      "conflictDomains": ["src/ui/session-banner.ts", "src/client/session-events.ts"],
      "context": ["spec.md"]
    }
  ]
}
```

Note task 1 and task 2 both list `src/session/types.ts` — the same
reasoning applies there: it's a shared contract file, both tasks legitimately
write to it, so they're correctly serialized by the domain *and* by the
`dependsOn` edge. Two independent mechanisms agreeing is fine; it isn't
redundant, because the domain would still protect a third task that later
needs the same file with no dependency relationship to either.

Each task's `context` field points at the `spec.md` acceptance criterion it
discharges, where one applies — machine-carried evidence for handover, not
something a worker has to remember from the intake conversation. `wddctl
plan lint --plan plan.json` comes back clean except two advisory
`missing_criteria` warnings on tasks 1 and 4, which is expected: neither
implements an acceptance criterion directly, they're the shared contract
and the supporting UI.

## Reading `plan preview`

Before applying anything:

```
$ wddctl plan preview --plan plan.json
{
  "scope": "SCOPE-auth-refresh",
  "maxConcurrent": 3,
  "note": "projected admission order; rounds are a view, not a gate",
  "rounds": [
    {"round": 1, "tasks": ["TASK-001-token-types", "TASK-004-session-ui"]},
    {"round": 2, "tasks": ["TASK-002-refresh-endpoint", "TASK-003-client-retry"]}
  ]
}
```

Read this as a projection, not a promise. Round 1 has the two tasks with no
unmet dependencies — token types and the banner — admitted together
because neither's domains overlap the other's at that moment. Round 2 has
the two tasks that depend on task 1.

Here's the thing this preview *can't* show you: it doesn't reveal that
task 3 and task 4 share `src/client/session-events.ts`, because task 3's
dependency on task 1 already puts it in round 2, one round after task 4.
The dependency edge and the domain overlap point the same direction here,
so the projection can't distinguish "these are serialized because of a
dependency" from "these are also serialized because of a domain." Reading
a round-projection alone would not have caught a missing conflict domain
in this particular shape of plan — the only way to catch it was the manual
check in the decomposition step above: does more than one task write this
file? `plan preview` is a schedule projection, not a domain-correctness
linter; treat it as the former only.

A single fat round (many tasks admitted at once) would mean domains are
probably too narrow — workers about to collide on undeclared shared files.
A long thin round-by-round schedule (one task per round, many rounds) means
domains are probably too coarse — you paid for parallel workers and got a
queue. This plan's two rounds of two tasks each is a reasonable middle.

## The run

Plan applied, approved by name — `--approved-by` now records a composite
fingerprint over the plan and every task brief, not just the plan JSON:

```
$ wddctl plan apply --plan plan.json --repo . --approved-by ivo
{"approvedBy": "ivo", "created": true, "revision": 7, "scope": "SCOPE-auth-refresh",
 "diff": {"added": ["TASK-001-token-types", "TASK-002-refresh-endpoint",
                     "TASK-003-client-retry", "TASK-004-session-ui"], "removed": [], "updated": []},
 "base": {"action": "created", "baseRef": "wdd/auth-refresh", "from": "HEAD"}}
```

`wddctl next` opens the loop:

```
$ wddctl next
{
  "actions": [
    {"task": "TASK-001-token-types", "action": "start_task",
     "command": "wddctl start --task TASK-001-token-types --repo ."},
    {"task": "TASK-004-session-ui", "action": "start_task",
     "command": "wddctl start --task TASK-004-session-ui --repo ."}
  ],
  "blockers": [
    {"task": "TASK-002-refresh-endpoint", "code": "dependencies", "dependsOn": ["TASK-001-token-types"]},
    {"task": "TASK-003-client-retry", "code": "dependencies", "dependsOn": ["TASK-001-token-types"]}
  ]
}
```

The controller starts both round-1 tasks:

```
$ wddctl start --task TASK-001-token-types --repo .
{"branch": "task/TASK-001-token-types", "worktree": ".worktrees/SCOPE-auth-refresh/TASK-001-token-types", "headSha": "03ddd58...", "revision": 8}
$ wddctl start --task TASK-004-session-ui --repo .
{"branch": "task/TASK-004-session-ui", "worktree": ".worktrees/SCOPE-auth-refresh/TASK-004-session-ui", "headSha": "03ddd58...", "revision": 10}
```

The worktree sits *inside* the repository (`worktrees.root` defaults to
`.worktrees`, gitignored automatically) — a task's checkout never pollutes
the controller's own working tree, but it isn't a sibling directory either;
see the `worktree` field in [`docs/artifact-schema.md`](artifact-schema.md)
for the full resolution rule.

Two workers go to work in their own worktrees. The TASK-001 worker writes
the token contract:

```ts
// src/auth/tokens.ts
export function issueRefreshToken(userId: string): RefreshToken {
  return {
    token: Math.random().toString(36).slice(2),
    userId,
    expiresAt: Date.now() + 30 * 24 * 60 * 60 * 1000,
  };
}
```

...commits, and submits:

```
$ wddctl submit --task TASK-001-token-types --repo .
{"event": "task.pr_recorded", "headSha": "4a621a0...", "status": "review", "revision": 12}
```

### The reviewer finds a genuine P1

TASK-001 is `risk: high`, so it needs review. The reviewer reads the diff
against the brief and catches something real: a refresh token — a
long-lived credential that, if guessed, lets an attacker mint fresh access
tokens indefinitely — is being generated with `Math.random()`, which is
not a cryptographically secure source of randomness. This is exactly the
class of finding `wdd-review` calls out for weighting heavily on
high-risk, auth-touching tasks.

```
$ wddctl review record --task TASK-001-token-types --reviewer "codex-review" \
  --findings '[{"severity":"P1","summary":"issueRefreshToken uses Math.random() for the token value; Math.random() is not cryptographically secure and refresh tokens must be unguessable","file":"src/auth/tokens.ts","line":9}]'
{"duplicate": false, "outcome": "blocking", "revision": 13, "status": "in_progress"}
```

```
$ wddctl next
{"actions": [
  {"task": "TASK-001-token-types", "action": "assign_fix_writer",
   "recordWith": "wddctl submit --task TASK-001-token-types --repo ."},
  {"task": "TASK-004-session-ui", "action": "await_worker", ...}
]}
```

### The fix cycle

The controller dispatches a fix worker: this task, this finding, don't
broaden scope. The fix is small and precise:

```ts
// src/auth/tokens.ts
import { randomBytes } from "crypto";
// ...
    token: randomBytes(32).toString("hex"),
```

```
$ wddctl submit --task TASK-001-token-types --repo .
{"event": "task.head_updated", "headSha": "ff16b0b...", "status": "review", "revision": 14}
```

New head, evidence invalidated, back to `review` automatically — the
`task.pr_recorded` event became `task.head_updated` this time, same PR
reference. The reviewer looks again at the new diff:

```
$ wddctl review record --task TASK-001-token-types --reviewer "codex-review" --findings '[]'
{"duplicate": false, "outcome": "passed", "revision": 15}
$ wddctl verify record --task TASK-001-token-types --status passed --command "tsc --noEmit && vitest run tokens"
{"duplicate": false, "revision": 16, "status": "merge_ready"}
```

### The merge

Meanwhile TASK-004's worker (normal risk, no review needed) has also
finished — the banner and the shared event-bus module:

```ts
// src/client/session-events.ts
export function on(event: string, handler: () => void): void { ... }
export function emit(event: string): void { ... }

// src/ui/session-banner.ts
import { on } from "../client/session-events";
export function mountSessionBanner(): void {
  on("session-expired", () => showBanner("Your session ended. Please sign in again."));
}
```

```
$ wddctl submit --task TASK-004-session-ui --repo .
{"event": "task.pr_recorded", "status": "in_progress", "revision": 17}
$ wddctl verify record --task TASK-004-session-ui --status passed --command "vitest run session-banner"
{"duplicate": false, "revision": 18, "status": "merge_ready"}
```

Both tasks now check freshness and merge:

```
$ wddctl freshness record --task TASK-001-token-types --repo .
{"classification": "current", "revision": 19}
$ wddctl freshness record --task TASK-004-session-ui --repo .
{"classification": "current", "revision": 20}
$ wddctl merge --task TASK-001-token-types --repo .
{"action": "merged", "baseSha": "36932ea...", "headSha": "ff16b0b...", "revision": 21}
$ wddctl merge --task TASK-004-session-ui --repo .
{"action": "merged", "baseSha": "fce6232...", "headSha": "ad3031a...", "revision": 22}
$ wddctl release --task TASK-001-token-types --repo . && wddctl release --task TASK-004-session-ui --repo .
```

Two merges since the last checkpoint, and `reconcileEveryNMerges` is 2:

```
$ wddctl next
{"actions": [
  {"task": "-", "action": "run_reconciliation", "code": "merge_count", "merges": 2,
   "recordWith": "wddctl reconcile done"},
  {"task": "TASK-002-refresh-endpoint", "action": "start_task", ...},
  {"task": "TASK-003-client-retry", "action": "start_task", ...}
]}
$ wddctl reconcile done
{"duplicate": false, "revision": 25}
```

### Round 2, and the domain paying off

Both remaining tasks admit together now that task 1 is done and task 4 has
released the shared event-bus domain:

```
$ wddctl start --task TASK-002-refresh-endpoint --repo .
$ wddctl start --task TASK-003-client-retry --repo .
```

The TASK-003 worker needs `src/client/session-events.ts` — and finds it
already exists, written by TASK-004, already merged into the base this
branch started from. It doesn't recreate the module; it just imports
`emit` from it:

```ts
// src/client/http.ts
import { emit } from "./session-events";
export async function fetchWithRetry(input, init) {
  const first = await fetch(input, init);
  if (first.status !== 401) return first;
  const refreshed = await fetch("/auth/refresh", { method: "POST" });
  if (!refreshed.ok) { emit("session-expired"); return first; }
  return fetch(input, init);
}
```

Its commit touches only `src/client/http.ts` — the shared file needed no
changes, because task 4 had already built exactly the seam task 3 needed.
This is the payoff of drawing the domain correctly: no merge conflict on
`session-events.ts`, no rework, because the plan told both tasks up front
that the file was shared ground.

Meanwhile the TASK-002 worker builds the endpoint, and makes a design
decision worth flagging beyond this task:

```ts
// src/api/auth_routes.ts
export function postAuthRefresh(refreshToken: string, userId: string): Session {
  if (usedTokens.has(refreshToken)) {
    revokeAllSessionsForUser(userId);   // reuse -> kill every session, not just this one
    throw new Error("refresh token reuse detected; all sessions revoked");
  }
  usedTokens.add(refreshToken);
  return { accessToken: "new-access-token", refreshToken: "rotated-token" };
}
```

### A reconciliation checkpoint from a note, not a merge count

Detecting reuse and revoking the *whole* session family — not just the one
token — is exactly the kind of fact a later task (a device-list feature,
an "active sessions" screen, anything else touching sessions) needs to
know and has no way to discover from its own brief. The worker queues it:

```
$ wddctl note --task TASK-002-refresh-endpoint \
  --note "refresh-token reuse now revokes every session for the user, not just the reused token; any future device-list or 'active sessions' feature must treat a 401 from reuse detection as a full sign-out, not a per-device error"
{"duplicate": false, "revision": 30}
$ wddctl reconcile status
{"due": {"code": "pending_notes", "notes": 1}, "mergesSinceCheckpoint": 0, ...}
```

Zero merges since the last checkpoint — this reconciliation is due purely
because of the note, independent of `reconcileEveryNMerges`. `next` surfaces
it immediately, alongside the review and verification still in flight:

```
$ wddctl next
{"actions": [
  {"task": "-", "action": "run_reconciliation", "code": "pending_notes", "notes": 1,
   "recordWith": "wddctl reconcile done"},
  {"task": "TASK-002-refresh-endpoint", "action": "run_review", ...},
  {"task": "TASK-003-client-retry", "action": "run_verification", ...}
]}
```

Reconciliation doesn't block the other work — it's just another item in
the queue. The controller handles it here by reading the note (there's no
other task left `todo` to update a brief for, this late in the scope), and
would write it into `.wdd/shared-context/sessions.md` if this scope had
further tasks ahead of it that needed to know. That write is entirely a
controller responsibility: `wddctl` never touches `.wdd/shared-context/`
itself, and the note's text does not survive `reconcile done` in any
retrievable form — the pending-notes list is cleared, not archived.

```
$ wddctl reconcile done
{"duplicate": false, "revision": 33}
```

### Finishing the tasks

TASK-002's review comes back clean, both tasks verify, both are current
against the base, both merge:

```
$ wddctl review record --task TASK-002-refresh-endpoint --reviewer "codex-review" --findings '[]'
$ wddctl verify record --task TASK-002-refresh-endpoint --status passed --command "vitest run auth_routes"
$ wddctl verify record --task TASK-003-client-retry --status passed --command "vitest run http"
$ wddctl freshness record --task TASK-002-refresh-endpoint --repo .
$ wddctl freshness record --task TASK-003-client-retry --repo .
$ wddctl merge --task TASK-002-refresh-endpoint --repo .
$ wddctl merge --task TASK-003-client-retry --repo .
$ wddctl release --task TASK-002-refresh-endpoint --repo .
$ wddctl release --task TASK-003-client-retry --repo .
```

One more reconciliation checkpoint fires (two more merges, hitting
`reconcileEveryNMerges` again):

```
$ wddctl reconcile done
{"duplicate": false, "revision": 43}
```

## Finalize: review, verify, handoff, delivered

Every task is `done`, so the scope itself moves into `finalize` — `wddctl
next` stops returning empty and starts naming scope-level work, the same
one-action-at-a-time discipline it used per task. This is not an ordinary
PR "outside `wddctl`'s scope": the controller drives the whole handoff,
short of the click that actually merges it.

```
$ wddctl next
{"actions": [{"action": "final_review",
  "judgment": "dispatch a reviewer against the whole epic branch diff, per wdd-review's final-review contract, checked against spec.md; walk spec.md's acceptance criteria AC-1..AC-2 in order and confirm design.md's epic deliverable statement is observably true",
  "recordWith": "wddctl finalize review record --reviewer NAME --findings '[]' --repo ."}]}
$ wddctl finalize review record --reviewer "codex-review" --findings '[]' --repo .
{"duplicate": false, "headSha": "618ebab...", "outcome": "passed", "revision": 44}
```

Full verification runs every ratified `verification.commands` entry plus
the epic deliverable command recorded at `intake design`:

```
$ wddctl finalize verify record --results '[{"command": "npm test", "status": "passed"}, {"command": "python3 -c \"print('"'"'refresh cycle: 401 -> POST /auth/refresh -> retried request succeeds'"'"')\"", "status": "passed"}]' --repo .
{"duplicate": false, "headSha": "618ebab...", "revision": 45, "status": "passed"}
```

A clean review and a passed verification, both pinned to the current base
head, unlock the handoff. On the `local` surface (this run), `wddctl
finalize handoff` records that handoff happened and returns instructions —
the push and the PR are the operator's to run; on `pr`, it pushes the base
branch and opens the epic→target PR itself, the same way task-level
`submit` opens one:

```
$ wddctl finalize handoff --repo .
{"duplicate": false, "headSha": "618ebab...",
 "instructions": "push wdd/auth-refresh to your remote (e.g. 'git push origin wdd/auth-refresh') and open a pull request into main yourself; wddctl does not perform this on the local surface. Once the human merge lands, run 'wddctl finalize delivered --by NAME --repo .' to record it.",
 "pr": null, "revision": 46, "targetBranch": "main"}
```

The merge itself is not a `wddctl` command — an ordinary `git merge` (or
clicking "Merge" on the handoff PR), performed by whoever owns `main`:

```
$ git checkout -q main
$ git merge --no-ff -q wdd/auth-refresh -m "merge scope SCOPE-auth-refresh into main"
$ wddctl finalize delivered --by ivo --repo .
{"by": "ivo", "duplicate": false, "headSha": "618ebab...", "revision": 47, "targetBranch": "main"}
$ wddctl next
{"actions": [], "blockers": [], "phase": "delivered", "revision": 47, "scope": "SCOPE-auth-refresh"}
```

`finalize delivered` doesn't take this on trust — it fetches `main`
best-effort and requires the base branch's head to actually be an ancestor
of it. Run it before the merge lands and it refuses, naming exactly what
hasn't happened yet. From here `wddctl scope archive` would retire this
scope's records into `.wdd/archive/` and reopen a fresh ladder for the next
epic — see [`docs/workflow.md`](workflow.md#finishing-a-scope) for that
transcript.

## What the developer sees at the end

```
$ wddctl status
SCOPE-auth-refresh revision 47
constitution: ratified
tasks: done=4, todo=0
phase: delivered
```

And the actual history, on `main`, as an ordinary `git log`:

```
*   0bbf68d merge scope SCOPE-auth-refresh into main
|\
| *   618ebab wdd: merge TASK-003-client-retry into wdd/auth-refresh
| |\
| | * 3ad364e TASK-003: silent-refresh retry with session-expired event
| * |   0e95f70 wdd: merge TASK-002-refresh-endpoint into wdd/auth-refresh
| |\ \
| | |/
| |/|
| | * 27789f5 TASK-002: add POST /auth/refresh route with reuse detection
| |/
| *   fce6232 wdd: merge TASK-004-session-ui into wdd/auth-refresh
| |\
| | * ad3031a TASK-004: session-expiry banner + session-events bus
| |/
|/|
| * 36932ea wdd: merge TASK-001-token-types into wdd/auth-refresh
|/|
| * ff16b0b TASK-001: use crypto.randomBytes for refresh token generation (fixes P1)
| * 4a621a0 TASK-001: refresh token type contract
|/
* 03ddd58 initial commit
```

Four tasks, one real security fix caught before merge, one durable
discovery about session-reuse semantics recorded at a reconciliation
checkpoint, and a clean, individually-reviewable commit history — merged
into `main` with `wddctl` proving, not assuming, that the human-owned final
merge actually happened. Nothing about getting here required trusting that
the token-fix worker, the retry worker, or the banner worker remembered a
rule from three tasks ago; the state machine carried the parts that
mattered — evidence, ordering, exclusion — and the skills carried the
judgment.
