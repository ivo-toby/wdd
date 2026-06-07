---
name: wdd-start-epic
description: Start a WDD feature or spike by turning user intent into a local epic folder with PRD/design context that is strong enough to generate self-contained tickets.
---

# WDD Start Epic

Use this when the user wants to start a feature, spike, migration, or broad refactor under WDD.

## Workflow

1. Read `.wdd/constitution.md` and relevant repo docs.
2. Clarify only missing product or technical constraints that affect ticket generation.
3. Create the epic:
   - `wdd new feature <slug> --title "<title>"`
   - or `wdd new spike <slug> --title "<title>"`
4. Fill the epic body, `prd.md`, and `design.md` with enough context for another agent to generate tickets without reading the conversation.
5. Record adapter links in frontmatter if external trackers exist.

## Gate

Do not write tickets until the epic has a clear outcome, non-goals, target shape, and verification expectations.

