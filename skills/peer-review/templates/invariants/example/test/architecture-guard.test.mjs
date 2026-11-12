// Architecture guard: a new path that reaches the raw client without the owner fails here.
import test from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { findViolations } from './architecture-guard-lib.mjs';

const at = (rel) => fileURLToPath(new URL(rel, import.meta.url));

test('real source: only the owner imports the raw client (barrel does not export it)', () => {
  assert.deepEqual(findViolations(at('../src')), []);
});
test('fixture: clean project passes', () => {
  assert.deepEqual(findViolations(at('../fixtures/clean/src')), []);
});
test('fixture: an unauthorized writer is caught', () => {
  assert.deepEqual(findViolations(at('../fixtures/violating/src')), ['rogue.mjs']);
});
