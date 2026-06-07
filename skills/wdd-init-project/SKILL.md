---
name: wdd-init-project
description: Initialize a project for Wave-Driven Development with local .wdd storage, install the WDD skill pack, and create or inspect the project constitution before any epic work starts.
---

# WDD Init Project

Use this when a user wants to install, initialize, or adopt WDD in a coding-agent project.

## Workflow

1. Inspect the repo instructions and current project state.
2. Run `wdd init --agent codex` if `.wdd/` does not exist.
3. Run `wdd install-skills` unless the user asks not to update local skills.
4. Read `.wdd/constitution.md`.
5. Ask for missing project boundaries only when repo docs and the constitution do not answer them.
6. Report the initialized paths and the next valid command.

## Rules

- Do not overwrite an existing constitution unless the user explicitly asks.
- Do not create epics until the constitution has enough boundaries and prerequisites for agents to follow.
- Keep GitHub/Jira/etc. as adapters; local `.wdd/` files are the default source of truth.

