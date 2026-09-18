import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Дев-режим проксирует API на локальный бэкенд; в продакшене статика
// отдаётся самим FastAPI (web/dist копируется в образ).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
  build: {
    outDir: 'dist',
  },
})
