// Owner policy tested ONCE, table-driven. Entry points are not re-tested for policy.
import test from 'node:test';
import assert from 'node:assert/strict';
import { checkWrite, createLedger } from '../src/ledger.mjs';
import { createRawClient } from '../src/raw-client.mjs';

const cases = [
  ['admin may write',               { role: 'admin',    amountCents: 500 },       { ok: true }],
  ['importer may write',            { role: 'importer', amountCents: 1 },         { ok: true }],
  ['at the limit is allowed',       { role: 'admin',    amountCents: 1_000_000 }, { ok: true }],
  ['viewer is denied',              { role: 'viewer',   amountCents: 500 },       { ok: false, reason: 'role-denied' }],
  ['missing role is denied',        { amountCents: 500 },                         { ok: false, reason: 'role-denied' }],
  ['zero amount is invalid',        { role: 'admin',    amountCents: 0 },         { ok: false, reason: 'invalid-amount' }],
  ['fractional amount is invalid',  { role: 'admin',    amountCents: 1.5 },       { ok: false, reason: 'invalid-amount' }],
  ['over the limit is refused',     { role: 'admin',    amountCents: 1_000_001 }, { ok: false, reason: 'over-limit' }],
];
for (const [name, input, expected] of cases) {
  test(`checkWrite: ${name}`, () => assert.deepEqual(checkWrite(input), expected));
}

test('append writes allowed entries tagged with the actor, and refuses denied ones', () => {
  const sink = [];
  const ledger = createLedger({ client: createRawClient(sink) });
  ledger.append({ role: 'admin', amountCents: 5 });
  assert.deepEqual(sink, [{ role: 'admin', amountCents: 5, actor: 'admin' }]);
  assert.throws(() => ledger.append({ role: 'viewer', amountCents: 5 }), /role-denied/);
  assert.equal(sink.length, 1);
});
