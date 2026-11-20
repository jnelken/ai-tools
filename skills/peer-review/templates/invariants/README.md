# Invariant enforcement templates

Reusable examples for the invariants rule in `instructions/development/shared.md` (*Invariants*): when a
review finding is a rule that must hold on **every path** reaching a side effect, give it **one owner**,
test the owner's policy once, test that each entry point delegates, and enforce it mechanically.

## Procedure

A review finding is either an **instance** (one defect at one place) or an **invariant** (a rule that must hold at every path reaching a side effect: a write, network call, payment, permission check, or secret read). Classify every finding that way before fixing it. Fixing each reported call site of an invariant one round at a time just waits for the next review to find the next site.

For an invariant finding, **before fixing, list every code path that reaches this side effect. If more than one, propose a single owner.** Prefer one owner (a single module or function) that applies the policy, with mechanical enforcement over another per-site patch. Then:

1. **Record** the invariant and its owner in the code or plan doc: one sentence of the rule, and the path to the owner.
2. **Check changed paths** in the diff against it: anything new that reaches the side effect must go through the owner.
3. **Test the owner's policy once**, with table-driven cases covering every allowed and denied input.
4. **Test each entry point delegates** to the owner, so a new path that bypasses it fails a test.
5. **Enforce mechanically** where cheap: keep the raw client unexported, add an ESLint restriction, or add an architecture guard test that fails on a bypassing import.

Escalate only when choosing the owner is an architectural decision beyond the approved scope; otherwise make the call and note it. `instructions/development/shared.md` (*Invariants*) keeps only the classify-and-single-owner rule and points here.

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
