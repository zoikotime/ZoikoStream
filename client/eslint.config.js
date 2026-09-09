import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  // Generated output, not source. playwright-report/ in particular ships a bundled copy of
  // the trace viewer, which alone contributed ~740 no-undef errors and drowned the real
  // lint signal the moment anyone ran the E2E suite with its HTML reporter.
  globalIgnores(['dist', 'playwright-report', 'test-results', 'blob-report']),
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
  },
  {
    // Vitest globals (describe/it/expect/vi) in test files only — without this the new
    // payment tests would report no-undef and inflate the lint baseline.
    files: ['src/**/*.test.{js,jsx}', 'src/test/**/*.{js,jsx}'],
    languageOptions: { globals: { ...globals.browser, ...globals.node, vi: 'readonly' } },
    rules: { 'react-refresh/only-export-components': 'off' },
  },
  {
    // Node-only files. These execute in Node — the build/test tooling configs, and everything
    // under e2e/, which Playwright's runner loads in its own Node process — so `process`,
    // `__dirname` and friends genuinely exist at runtime.
    //
    // Scoped to these paths on purpose. Adding `process` to the browser block above would make
    // `process.env.FOO` lint clean inside src/, where Vite replaces nothing and the reference
    // is a ReferenceError in the browser: the lint error is load-bearing there, so it stays.
    // src/ reads configuration through `import.meta.env`, which is already a browser global.
    //
    // `react-refresh/only-export-components` is off because these are not components at all;
    // Fast Refresh never sees them.
    files: ['*.config.js', 'e2e/**/*.js'],
    languageOptions: { globals: globals.node },
    rules: { 'react-refresh/only-export-components': 'off' },
  },
])
