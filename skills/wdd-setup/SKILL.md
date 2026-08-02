---
name: wdd-setup
description: Initialize WDD in a repository — run wddctl init, resolve the open config questions with the user in one round, and ratify the constitution + config. Replaces the old wdd-constitution skill. Use when .wdd/state.json is missing, when open config questions remain, or when governance needs amending.
---

# WDD Setup

You run every `wddctl` command in this skill yourself. Presenting a command
to the user instead of executing it is a protocol violation.

## Fresh repository

A repository whose git history contains a deleted `.wdd/` still counts as
fresh: do not `git checkout` or otherwise restore those artifacts — they
belong to a decommissioned scope and may predate the current state schema.
Initialize from scratch.

1. Run `wddctl init --repo .`. It scaffolds `.wdd/` deterministically:
   `config.json` with probed defaults plus an `openQuestions` list,
   a prose `constitution.md` draft, `tasks/`, `shared-context/`, and
   `state.json`. Re-running it is a safe no-op.
2. Run `wddctl next` and do what it says. During setup it emits exactly one
   action at a time:
   - `resolve_config` — relay every listed question to the user in ONE
     compact round (not one message per question), as plain decisions in
     your own words. Never show the user config paths, JSON syntax, or CLI
     flags — the `path` on each question is for YOU. Once they answer,
     translate each answer yourself into `wddctl config set <path> <value>`
     (values are JSON when structured, `'["npm test"]'`; bare strings
     otherwise, `local`). "We have no tests yet" is an answer too: set
     `verification.commands` to `[]` and put the user's reason in
     `verification.unavailableJustification`. When relaying the
     verification question, steer toward a compound answer: the strongest
     gate covers the build, the tests, AND a startup smoke check (does the
     binary run at all) — a unit-test-only gate passes packages that never
     start. The model question maps the
     user's answer onto three roles in one set — for example
     `wddctl config set models '{"planning": null, "implementation":
     {"default": "haiku", "highRisk": "sonnet"}, "review": "sonnet"}'` —
     and "harness defaults are fine" is a valid answer: record it as all
     nulls so the question resolves and dispatchers use their own default.
     If an answer instead names a local or CLI agent (qwen, codex, some
     other local model) rather than a harness-known alias, that value only
     resolves once it's registered and probed as a runner — mention this
     as a follow-on and point at `wdd-runners` before moving on.
   - `ratify` — show the user the current `config.json` values and the
     `constitution.md` text (summarize, link the files), get explicit
     sign-off, then run `wddctl constitution ratify --by <name>`. Never
     ratify content the user has not seen.
   - `agree_spec` (or any other intake rung) — governance is done; the
     intake ladder has begun. Switch to the `wdd-intake` skill.
   - `plan` — only legacy-migrated scopes skip the ladder and land here
     directly; switch to the `wdd-plan` skill.
3. While resolving questions, also fill the constitution's prose sections
   (Intent, reviewer focus) from what you know of the project — it is prose
   for judgment, never configuration. All machine knobs go in `config.json`.

## Amending later

Config and constitution are fingerprint-bound to ratification. After any
edit, execution verbs refuse with a governance-drift error until you get the
user's explicit re-approval of the change and run
`wddctl constitution amend --by <name>`.

## Legacy repositories

A `.wdd/` from before the config split (constitution containing a JSON
models block, no `config.json`) is converted with
`wddctl migrate --governance --apply`. This backs up the old constitution,
extracts the model aliases into `config.json`, and deliberately invalidates
ratification — walk the user through re-approval as for a fresh setup.

## Done when

- `wddctl next` no longer emits `resolve_config` or `ratify`.
- The user has explicitly approved what was ratified.

Close with the handoff: offer to start the intake ladder — "Setup's done.
Should I start the intake ladder? Bring a spec or describe the feature."
The ladder is `wdd-intake`; don't make the user discover that.
