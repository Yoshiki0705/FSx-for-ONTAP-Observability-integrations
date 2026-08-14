// ESLint flat configuration.
//
// Why this file exists
// --------------------
// `npm run lint` had no configuration at all. ESLint 9 dropped .eslintrc in
// favour of eslint.config.js, and with neither present the command exits 2 on
// "couldn't find an eslint.config.(js|mjs|cjs) file" -- a configuration error,
// not a lint result.
//
// That went unnoticed because two things hid it. The CI step skips the lint
// entirely when no .ts file exists, and there are currently none; and the step
// was marked continue-on-error, so the config error would have been reported as
// a passing step the moment a .ts file appeared. The gate was therefore broken
// in exactly the circumstance that would make it matter.
//
// The repo still carries typescript, ts-jest and the @typescript-eslint
// packages, and jest.config.js is set up for **/*.test.ts, so TypeScript is an
// expected future surface rather than a dead dependency.

const tsParser = require('@typescript-eslint/parser');
const tsPlugin = require('@typescript-eslint/eslint-plugin');

module.exports = [
  {
    ignores: [
      'node_modules/**',
      'dist/**',
      'coverage/**',
      '.venv/**',
      '**/__pycache__/**',
      '.playwright-mcp/**',
    ],
  },
  {
    files: ['**/*.ts'],
    languageOptions: {
      parser: tsParser,
      ecmaVersion: 2022,
      sourceType: 'module',
    },
    plugins: { '@typescript-eslint': tsPlugin },
    rules: {
      // Deliberately a small set that only fires on a defect, matching the
      // approach taken for ruff: a gate that arrives red gets switched off.
      // Style belongs in a formatter, not here.
      'no-unused-vars': 'off', // superseded by the TypeScript-aware rule
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      'no-undef': 'error',
      'no-dupe-keys': 'error',
      'no-unreachable': 'error',
      'no-constant-condition': 'error',
      'require-await': 'error',
      // await inside a loop is usually a serialised batch; flag it here because
      // the Lambda handlers in this repo fan out to vendor APIs.
      'no-await-in-loop': 'warn',
    },
  },
];
