// ESLint flat-config restriction for the invariant owner pattern (see README.md).
// Copy into your eslint.config.* and adjust the paths. Everything except the owner
// is barred from importing the raw client; the owner is exempted by a later block.
export default [
  {
    files: ['src/**/*.{js,mjs,ts,tsx}'],
    rules: {
      'no-restricted-imports': ['error', {
        patterns: [{
          group: ['**/raw-client', '**/raw-client.*'],
          message: 'Writes go through the owner (src/ledger). Do not import the raw client directly.',
        }],
      }],
    },
  },
  {
    // The single owner may import the raw client.
    files: ['src/ledger.*'],
    rules: { 'no-restricted-imports': 'off' },
  },
];
