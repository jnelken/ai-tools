// ESLint flat-config rule options for the invariant owner pattern (see README.md).
// Merge these restrictions into your existing rule options; later flat-config
// entries replace options for the same rule. Adjust the paths for your project.
export default [
  {
    files: ['src/**/*.{js,jsx,mjs,cjs,ts,tsx,mts,cts}'],
    // The owner can reach the raw client; this excludes only these restrictions,
    // without disabling the repository's other rules for that file.
    ignores: ['src/ledger.*'],
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
];
