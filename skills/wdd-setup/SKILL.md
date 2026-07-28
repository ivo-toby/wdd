---
name: wdd-setup
description: Initialize WDD in a repository — run wddctl init, resolve the open config questions with the user in one round, and ratify the constitution + config. Replaces the old wdd-constitution skill. Use when .wdd/state.json is missing, when open config questions remain, or when governance needs amending.
---

# WDD Setup

You run every `wddctl` command in this skill yourself. Presenting a command
to the user instead of executing it is a protocol violation.

## Fresh repository

1. Run `wddctl init --repo .`. It scaffolds `.wdd/` deterministically:
   `config.json` with probed defaults plus an `openQuestions` list,
   a prose `constitution.md` draft, `tasks/`, `shared-context/`, and
   `state.json`. Re-running it is a safe no-op.
2. Run `wddctl next` and do what it says. During setup it emits exactly one
   action at a time:
   - `resolve_config` — ask the user every listed question in ONE compact
     round (not one message per question), then record each answer with
     `wddctl config set <path> <value>`. Values are JSON when structured
     (`'["pytest -q"]'`), bare strings otherwise (`local`).
   - `ratify` — show the user the current `config.json` values and the
     `constitution.md` text (summarize, link the files), get explicit
     sign-off, then run `wddctl constitution ratify --by <name>`. Never
     ratify content the user has not seen.
   - `plan` — setup is done; switch to the `wdd-plan` skill.
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
