// THE OWNER of the invariant:
//   "Every ledger write is authorized, validated and tagged with its actor."
// All paths that write go through append(). The policy lives here once and is
// tested once (test/owner-policy.test.mjs).
import { createRawClient } from './raw-client.mjs';

export const POLICY = {
  allowedRoles: ['admin', 'importer'],
  maxAmountCents: 1_000_000,
};

export function checkWrite({ role, amountCents }) {
  if (!POLICY.allowedRoles.includes(role)) return { ok: false, reason: 'role-denied' };
  if (!Number.isInteger(amountCents) || amountCents <= 0) return { ok: false, reason: 'invalid-amount' };
  if (amountCents > POLICY.maxAmountCents) return { ok: false, reason: 'over-limit' };
  return { ok: true };
}

export function createLedger({ client = createRawClient() } = {}) {
  return {
    append(entry) {
      const verdict = checkWrite(entry);
      if (!verdict.ok) throw new Error(`ledger write refused: ${verdict.reason}`);
      client.write({ ...entry, actor: entry.role });
    },
  };
}
