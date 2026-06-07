# WDD

Wave-Driven Development is a local-first workflow for planning and executing
agentic software work in dependency-aware waves.

It gives coding agents a durable project structure:

- a project constitution for boundaries and prerequisites,
- epics and tickets stored as Markdown with mandatory YAML frontmatter,
- validation before execution,
- a dependency/conflict-aware wave plan,
- controller state and implementation briefs for subagent orchestration.

GitHub is optional. Local `.wdd/` files are the default source of truth; external
trackers can be added later through `adapter_links` metadata.

## Install

```bash
npm install -g @ivo-toby/wdd
```

From a checkout of this repo:

```bash
npm install
npm link
```

## Initialize A Project

```bash
wdd init --agent codex
wdd install-skills
```

`wdd init` creates:

```text
.wdd/
  config.yaml
  constitution.md
  epics/
```

`wdd install-skills` copies the packaged skills into `~/.agents/skills` by
default. Use `--target <path>` to install somewhere else.

The package installs step-level skills for coding agents:

- `wdd-init-project`
- `wdd-constitution`
- `wdd-start-epic`
- `wdd-write-tickets`
- `wdd-validate-tickets`
- `wdd-plan-waves`
- `wdd-start-wave`
- `wdd-reconcile-wave`
- `wave-driven-development`
- `subagent-pr-orchestration`

The step skills make individual phases discoverable to an agent. The two broad
skills remain as the overview/planning and execution-controller workflows.

## Plan A Feature Or Spike

```bash
wdd new feature auth-refresh --title "Auth Refresh"
wdd ticket create WDD-0001 token-contract \
  --title "Token Contract" \
  --verify "npm test -- auth"
wdd validate WDD-0001
wdd waves plan WDD-0001
```

The agent should then inspect and refine:

```text
.wdd/epics/WDD-0001-auth-refresh/
  epic.md
  prd.md
  design.md
  tickets/
    WDD-0001-T001-token-contract.md
  wave-plan.yaml
```

## Start A Wave

```bash
wdd start-wave WDD-0001 --json
```

This does not implement code. It writes:

```text
.wdd/epics/WDD-0001-auth-refresh/
  controller-state.yaml
  briefs/
    WDD-0001-T001-token-contract.md
```

The controller agent reads those files and uses the
`subagent-pr-orchestration` skill to dispatch implementation subagents.

After a wave is merged and reconciled:

```bash
wdd reconcile WDD-0001 --wave 1 --done
```

## Useful Commands

```bash
wdd status --json
wdd validate WDD-0001 --json
wdd waves plan WDD-0001 --json
wdd schema --json
wdd doctor --json
```

## Development

```bash
npm test
npm run lint
```

The CLI is plain Node.js ESM with one runtime dependency, `yaml`.
