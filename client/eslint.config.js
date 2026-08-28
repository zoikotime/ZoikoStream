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
])
