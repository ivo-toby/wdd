# TASK-001-token-types: Token type contract

## Objective

Define the `RefreshToken` / `AccessToken` types and the storage interface
the refresh endpoint (TASK-002) will implement against.

## Scope

- Define types in `src/auth/tokens.ts`.
- Define the storage interface (no implementation).
- Add unit tests for type guards/validators.

## Non-scope

- No endpoint or route changes (TASK-002).
- No storage backend implementation.
- No documentation changes (TASK-003).

## Files to read first

- `src/schema.ts` — existing shared type conventions.
- `src/auth/README.md` — current auth module boundaries, if present.

## Conflict domains

- `src/auth/**`
- `src/schema.ts`

## Verification

`npm test -- src/auth/tokens.test.ts`

Tests that must exist: `src/auth/tokens.test.ts` — type guard/validator
cases the worker's red/green cycle produces.

## Definition of done

- [ ] Types and storage interface committed.
- [ ] Unit tests pass.
- [ ] No changes outside the declared conflict domains.
