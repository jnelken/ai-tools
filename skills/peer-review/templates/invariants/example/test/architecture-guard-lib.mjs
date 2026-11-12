import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

// Returns the files under srcDir (other than the owner and the raw client itself)
// that import the raw client, or that re-export it from the public barrel.
export function findViolations(srcDir, { owner = 'ledger.mjs', raw = 'raw-client.mjs' } = {}) {
  const bad = [];
  for (const name of readdirSync(srcDir).filter((f) => f.endsWith('.mjs'))) {
    if (name === owner || name === raw) continue;
    const text = readFileSync(join(srcDir, name), 'utf8');
    if (text.includes(raw)) bad.push(name);
  }
  return bad;
}
