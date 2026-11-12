# Invariant enforcement templates

Reusable examples for the convention in `instructions/development/shared.md` (*Invariants*): when a
review finding is a rule that must hold on **every path** reaching a side effect, give it **one owner**,
test the owner's policy once, test that each entry point delegates, and enforce it mechanically.

Everything runs with Node only (no dependencies): `./run-fixtures.sh` (or
`node --test example/test/*.test.mjs` from this directory).

| Template | Shows |
|---|---|
| `example/src/raw-client.mjs` + `example/src/index.mjs` | **Unexported raw client**: the side-effecting client is imported by the owner only and is absent from the public barrel. |
| `example/src/ledger.mjs` | **The owner**: one policy (`checkWrite`) applied by `append()`. |
| `example/src/api.mjs`, `example/src/importer.mjs` | Entry points that delegate to the owner. |
| `eslint.config.example.mjs` | **ESLint restriction**: `no-restricted-imports` and a core `no-restricted-syntax` selector bar ESM imports and CommonJS `require()` calls outside the owner. Merge the patterns/selectors into existing rule options and adjust paths; not executed by the fixtures. |
| `example/test/architecture-guard.test.mjs` (+ `-lib.mjs`) | **Architecture guard**: fails when any other `.js`, `.jsx`, `.mjs`, `.cjs`, `.ts`, `.tsx`, `.mts`, or `.cts` file imports or requires the raw client. Fixtures `fixtures/clean` (passes) and `fixtures/violating` (rogue ESM and CommonJS writers; all must be caught) keep the guard itself honest. |
| `example/test/owner-policy.test.mjs` | **Owner-policy tests**: one table-driven suite covering every allowed and denied input. |
| `example/test/delegation.test.mjs` | **Delegation tests**: each entry point calls the owner; an unauthorized write fails and an allowed one passes through the real owner. |

## Adapting

1. Name the invariant in one sentence and the owner's path; put both in a comment at the top of the owner.
2. List every path that reaches the side effect (grep for the raw client). Route each through the owner.
3. Replace the stand-in raw client, policy and entry points; keep the test shapes.
4. Wire the ESLint block (or the guard test) into the repo's normal lint/test command so a new bypass fails CI.
