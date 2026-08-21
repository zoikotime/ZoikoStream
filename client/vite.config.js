import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  // Load env from the repo root so backend + frontend share one .env file.
  envDir: '..',
  plugins: [
    react(),
    tailwindcss(),
  ],
  // The app's .jsx uses the automatic JSX runtime (no `import React`). Vitest transforms
  // through esbuild rather than the react plugin's babel step, so it needs telling — without
  // this, every component fails with "React is not defined" under test only.
  esbuild: { jsx: 'automatic', jsxImportSource: 'react' },
  // Vitest — dev-only. jsdom because the payment flow reads window.location/history.
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.js'],
    // Only our own tests; never crawl node_modules or the build output.
    include: ['src/**/*.test.{js,jsx}'],
    css: false,
  },
})