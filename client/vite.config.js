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
})