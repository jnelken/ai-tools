import { readdirSync, readFileSync } from 'node:fs';
import { basename, join, relative } from 'node:path';

function sourceFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return entry.isFile() && entry.name.endsWith('.mjs') ? [path] : [];
  });
}

// Returns the files under srcDir (other than the owner and the raw client itself)
// that import the raw client, or that re-export it from the public barrel.
export function findViolations(srcDir, { owner = 'ledger.mjs', raw = 'raw-client.mjs' } = {}) {
  const bad = [];
  const rawStem = basename(raw).replace(/\.[^.]+$/, '');
  const importFrom = /\b(?:from\s*|import\s*(?:\(\s*)?)['"]([^'"]+)['"]\s*\)?/g;
  for (const path of sourceFiles(srcDir)) {
    const name = relative(srcDir, path);
    if (name === owner || name === raw) continue;
    const text = readFileSync(path, 'utf8');
    for (const [, specifier] of text.matchAll(importFrom)) {
      const importedStem = basename(specifier).replace(/\.[^.]+$/, '');
      if (importedStem === rawStem) {
        bad.push(name);
        break;
      }
    }
  }
  return bad;
}
