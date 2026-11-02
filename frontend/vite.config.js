import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Backend to forward /api to (override with API_URL=http://host:port npm run dev)
    proxy: { '/api': process.env.API_URL || 'http://localhost:8000' },
  },
})
