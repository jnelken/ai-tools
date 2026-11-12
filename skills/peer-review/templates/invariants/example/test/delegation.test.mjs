// Every entry point must delegate to the owner. A fake owner records the calls.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createApi } from '../src/api.mjs';
import { importRows } from '../src/importer.mjs';
import { createLedger } from '../src/ledger.mjs';
import { createRawClient } from '../src/raw-client.mjs';

const fakeLedger = () => { const calls = []; return { calls, append: (e) => calls.push(e) }; };

test('api.postPayment delegates to ledger.append', () => {
  const ledger = fakeLedger();
  createApi({ ledger }).postPayment({ role: 'admin', amountCents: 7 });
  assert.deepEqual(ledger.calls, [{ role: 'admin', amountCents: 7 }]);
});
test('importRows delegates each row to ledger.append', () => {
  const ledger = fakeLedger();
  importRows([{ amountCents: 1 }, { amountCents: 2 }], { ledger });
  assert.equal(ledger.calls.length, 2);
});
test('unauthorized write through an entry point fails; allowed write passes (real owner)', () => {
  const sink = [];
  const ledger = createLedger({ client: createRawClient(sink) });
  const api = createApi({ ledger });
  assert.throws(() => api.postPayment({ role: 'viewer', amountCents: 7 }), /role-denied/);
  api.postPayment({ role: 'admin', amountCents: 7 });
  importRows([{ amountCents: 3 }], { ledger });
  assert.equal(sink.length, 2);
});
