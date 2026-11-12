// ESLint flat-config restriction for the invariant owner pattern (see README.md).
// Copy into your eslint.config.* and adjust the paths. Everything except the owner
// is barred from importing or requiring the raw client; the owner is exempted by a later block.
export default [
  {
    files: ['src/**/*.{js,jsx,mjs,cjs,ts,tsx,mts,cts}'],
    rules: {
      'no-restricted-imports': ['error', {
        patterns: [{
          group: ['**/raw-client', '**/raw-client.*'],
          message: 'Writes go through the owner (src/ledger). Do not import the raw client directly.',
        }],
      }],
      'no-restricted-syntax': ['error', {
        selector: "CallExpression[callee.name='require'][arguments.0.value=/raw-client/]",
        message: 'Writes go through the owner (src/ledger). Do not require the raw client directly.',
      }],
    },
  },
  {
    // The single owner may import or require the raw client.
    files: ['src/ledger.*'],
    rules: {
      'no-restricted-imports': 'off',
      'no-restricted-syntax': 'off',
    },
  },
];
