# Worked example: refresh-token support

A complete run of a small, realistic feature from request to merge, using
real `wddctl` output captured against a scratch repository on this branch.
Where output is long, it's trimmed with `...`; nothing here is invented.

## The request

The developer says, roughly:

> Our sessions just expire and kick people to the login screen. Add refresh
> tokens so a user's session extends silently instead of dying on them.

That's it — no task breakdown, no file list. Turning this into a plan is
the judgment `wdd-plan` covers.

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
      "conflictDomains": ["src/auth/tokens.ts", "src/session/types.ts"]
    },
    {
      "id": "TASK-002-refresh-endpoint",
      "title": "POST /auth/refresh endpoint",
      "specPath": "tasks/TASK-002-refresh-endpoint.md",
      "risk": "high",
      "dependsOn": ["TASK-001-token-types"],
      "conflictDomains": ["src/api/auth_routes.ts", "src/session/types.ts"]
    },
    {
      "id": "TASK-003-client-retry",
      "title": "Client-side silent retry on 401",
      "specPath": "tasks/TASK-003-client-retry.md",
      "risk": "normal",
      "dependsOn": ["TASK-001-token-types"],
      "conflictDomains": ["src/client/http.ts", "src/client/session-events.ts"]
    },
    {
      "id": "TASK-004-session-ui",
      "title": "Session expiry UI messaging",
      "specPath": "tasks/TASK-004-session-ui.md",
      "risk": "normal",
      "dependsOn": [],
      "conflictDomains": ["src/ui/session-banner.ts", "src/client/session-events.ts"]
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

Plan applied, constitution ratified (see `wdd-constitution` and
`docs/wddctl.md`), and `wddctl next` opens the loop:

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
{"branch": "task/TASK-001-token-types", "worktree": ".../TASK-001-token-types", "headSha": "34a4211...", "revision": 2}
$ wddctl start --task TASK-004-session-ui --repo .
{"branch": "task/TASK-004-session-ui", "worktree": ".../TASK-004-session-ui", "headSha": "34a4211...", "revision": 3}
```

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
{"event": "task.pr_recorded", "headSha": "f9cdf2b...", "status": "review", "revision": 4}
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
{"duplicate": false, "outcome": "blocking", "revision": 5, "status": "in_progress"}
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
{"event": "task.head_updated", "headSha": "e6befd7...", "status": "review", "revision": 6}
```

New head, evidence invalidated, back to `review` automatically — the
`task.pr_recorded` event became `task.head_updated` this time, same PR
reference. The reviewer looks again at the new diff:

```
$ wddctl review record --task TASK-001-token-types --reviewer "codex-review" --findings '[]'
{"outcome": "passed", "revision": 8}
$ wddctl verify record --task TASK-001-token-types --status passed --command "tsc --noEmit && vitest run tokens"
{"status": "merge_ready", "revision": 9}
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
{"event": "task.pr_recorded", "status": "in_progress", "revision": 7}
$ wddctl verify record --task TASK-004-session-ui --status passed --command "vitest run session-banner"
{"status": "merge_ready", "revision": 10}
```

Both tasks now check freshness and merge:

```
$ wddctl freshness record --task TASK-001-token-types --repo .
{"classification": "current", "revision": 11}
$ wddctl freshness record --task TASK-004-session-ui --repo .
{"classification": "current", "revision": 12}
$ wddctl merge --task TASK-001-token-types --repo .
{"action": "merged", "baseSha": "028b572...", "headSha": "e6befd7...", "revision": 13}
$ wddctl merge --task TASK-004-session-ui --repo .
{"action": "merged", "baseSha": "98dadf3...", "headSha": "f0b1dd1...", "revision": 14}
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
{"duplicate": false, "revision": 17}
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
{"duplicate": false, "revision": 22}
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
{"duplicate": false, "revision": 22}
```

### Finishing the scope

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
{"duplicate": false, "revision": 33}
```

## What the developer sees at the end

```
$ wddctl next
{"actions": [], "blockers": []}

$ wddctl status
SCOPE-auth-refresh revision 33
constitution: ratified
tasks: done=4, todo=0
active: 0

$ wddctl render --output .wdd/state.md
```

And the actual history, on the base branch, as an ordinary `git log`:

```
*   merge TASK-003-client-retry into wdd/auth-refresh
|\
| * TASK-003: silent-refresh retry with session-expired event
* |   merge TASK-002-refresh-endpoint into wdd/auth-refresh
|\ \
| |/
|/|
| * TASK-002: add POST /auth/refresh route with reuse detection
|/
*   merge TASK-004-session-ui into wdd/auth-refresh
|\
| * TASK-004: session-expiry banner + session-events bus
* |   merge TASK-001-token-types into wdd/auth-refresh
|\ \
| |/
|/|
| * TASK-001: use crypto.randomBytes for refresh token generation (fixes P1)
| * TASK-001: refresh token type contract
|/
* initial commit
```

Four tasks, one real security fix caught before merge, one durable
discovery about session-reuse semantics recorded at a reconciliation
checkpoint, and a clean, individually-reviewable commit history on
`wdd/auth-refresh` — which is now an ordinary branch ready for a normal PR
into `main`, outside `wddctl`'s scope entirely. Nothing about getting here
required trusting that the token-fix worker, the retry worker, or the
banner worker remembered a rule from three tasks ago; the state machine
carried the parts that mattered — evidence, ordering, exclusion — and the
skills carried the judgment.
