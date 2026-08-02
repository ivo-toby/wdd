---
name: wdd-runners
description: Set up and maintain the WDD runner registry — external agent CLIs as workers or reviewers. Use when the user says "add a runner", "use qwen/codex as a worker", "set up local workers", or names an agent CLI (pi, codex, qwen, a local model) as a model value.
---

# WDD Runners

You run every `wddctl` command in this skill yourself. Presenting a command
to the user instead of executing it is a protocol violation.

## What a runner is

A runner is an external agent CLI registered in config so a task's `model`
value can resolve to it. Dispatch is file-in, file-out, one shot: `wddctl`
writes the prompt to a file, execs the runner's command in the task's
worktree, captures its output, and reads the result off the tail. That is
the whole mechanism.

Stated non-goals — do not promise or attempt any of these: no streaming,
no interactive sessions, no retries, no supervision, no tool-permission
mediation. The runner command owns its own sandboxing, permissions, and
model flags — it is authored per machine by the operator, and what it
allows its child agent to do is its business, not wddctl's.

## Discovery

Run `wddctl doctor --json` before touching config and again after
registering. For every configured runner it reports whether the command's
binary is actually on PATH — a cheap first check that catches typos and
missing installs before any probe execs anything. Tell the user in plain
terms which runners resolve and which do not.

## Authoring the command template

A runner's config entry is one argv list with three placeholders,
substituted anywhere they appear:

- `{worktree}` — the task's worktree directory (also the exec cwd).
- `{prompt}` — a FILE PATH to the assembled prompt, not prompt text. The
  CLI must accept a file, or the argv must read it (`"$(cat {prompt})"`
  does not work — there is no shell; use the CLI's file flag).
- `{logfile}` — a path the runner may write its OWN transcript to. This is
  distinct from wddctl's capture of stdout+stderr, which lands in a
  sibling log regardless. Omit it if the CLI has no transcript flag.

Ask the user for their CLI's actual headless flags — how it runs
non-interactively, how it takes a prompt file, how it sets the working
directory and model. Do not guess flags from memory; a wrong flag fails
the probe and wastes a round. Two real shapes for orientation:

```json
"runners": {
  "qwen-local": {"command": ["pi", "--headless", "--model", "qwen3.6",
                              "--cd", "{worktree}", "-p", "{prompt}"]},
  "codex":      {"command": ["codex", "exec", "--cd", "{worktree}", "{prompt}"]}
}
```

Derive the real entry from the user's answers, not from these examples.

## Registration order — probe, then config, then governance

This order is law. A runner that was never probed is configuration
fiction, and dispatch enforces that mechanically.

1. **Probe the explicit candidate**:
   `wddctl dispatch --probe-command '["pi", "--headless", ...]'`. This is
   the one deliberately ungoverned dispatch path — the command is explicit
   in the invocation, typed or approved by the human in this conversation,
   never loaded from config. It execs the candidate in a scratch temp
   directory against a canned prompt ("Reply with exactly: DONE") and
   reports exit code, wall time, and whether the token came back. `ok`
   requires both a zero exit AND `DONE` as the trailing output line — a
   command that exits 0 without answering has proven nothing.
2. **Register it**:
   `wddctl config set runners '{"NAME": {"command": [...]}}'`.
3. **Re-sign governance**: step 2 edited config after ratification, so get
   the user's explicit approval of the change and run
   `wddctl constitution amend --by <human>`.
4. Optionally re-verify the ratified entry: `wddctl dispatch --probe NAME`.
   This one is governed — it executes a config-loaded command, so it
   refuses under drift like any execution verb.

Why the probe matters, in user terms: a passing probe records a digest of
the exact command bytes. Task dispatch refuses any runner whose ratified
command has no passing probe on record — and editing the command after
probing, even appending one flag, changes the digest and re-refuses. The
proof follows the bytes, not the runner's name. So: any edit to a runner's
command means re-probing, every time, no exceptions. If state.json does
not exist yet, a passing `--probe-command` reports but cannot record;
re-probe once the scope exists.

## Using runners in a scope

A task routes to a runner when its `model` (worker) or `reviewModel`
(reviewer) — per-task override or the risk-tiered models config — names a
registered runner. Any other value stays harness-native; nothing changes
for it.

Dispatch one attempt with:

```sh
wddctl dispatch --task TASK-ID --role worker|reviewer
```

This assembles the packet (brief and context from the task's recorded
attempt snapshot — the task must have been `start`ed), execs the runner
once, and captures everything to `.wdd/dispatch/` — transient scratch,
gitignored, never committed. The result payload carries a bounded tail of
the log; read it as you would any subagent report, and read the full log
file when the tail is not enough.

- **Worker**: output must end in the standard status token (`DONE`,
  `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, `BLOCKED`); the result reports
  `statusToken`, or `null` if none appeared. The runner's commit still has
  to exist before `wddctl submit`, same as any worker.
- **Reviewer**: a clean exit must end with the standard `wddctl_review_result`
  JSON. Dispatch validates it against the frozen diff range, writes it to
  a `-result.json` file beside the log, and you record it with
  `wddctl review collect --task TASK-ID --result <path>` — the same verb
  internal review evidence flows through.

## Troubleshooting

- **Probe fails, exit nonzero or `tokenSeen: false`**: the command is not
  actually headless, a flag is wrong, or the CLI prints trailing noise
  after its answer (the token must be the last non-empty line). Ask the
  user for the CLI's real flags again; test the command by hand outside
  wddctl if it keeps failing.
- **Timeout** (`timedOut: true`, exit code null): pass
  `--timeout <seconds>` on the dispatch. Probes default to 120 seconds;
  task dispatch has no default limit, so set one for slow or untrusted
  CLIs. A timeout is a reported failure, never a retry — re-dispatch
  deliberately if you want another attempt.
- **`NEEDS_CONTEXT` in worker output**: not a runner failure. The runner
  worked; the task lacked information. Handle it as you would from any
  worker — re-dispatch with more context, don't re-probe.

## Done when

The runner is registered, governance is re-signed, and the last probe of
the exact configured command passed. Report the passing probe and close
with the handoff: "Runner's registered and probed. Want me to route the
mechanical tasks to it in the next plan?" Routing tasks per tier is
`wdd-plan`; dispatching them is `wdd-run`.
