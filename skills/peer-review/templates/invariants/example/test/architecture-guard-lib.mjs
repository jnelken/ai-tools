import { readdirSync, readFileSync } from 'node:fs';
import { basename, extname, join, relative } from 'node:path';

const sourceExtensions = new Set(['.js', '.mjs', '.ts', '.tsx']);

function sourceFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return entry.isFile() && sourceExtensions.has(extname(entry.name)) ? [path] : [];
  });
}

// Returns files under srcDir (other than the owner and raw client) that reach it
// through ESM imports/re-exports or CommonJS require().
export function findViolations(srcDir, { owner = 'ledger.mjs', raw = 'raw-client.mjs' } = {}) {
  const bad = [];
  const rawStem = basename(raw).replace(/\.[^.]+$/, '');
  const importFrom = /\b(?:from\s*|import\s*(?:\(\s*)?|require\s*\(\s*)['"]([^'"]+)['"]\s*\)?/g;
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
  return bad.sort();
}
