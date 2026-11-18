import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import appVersion from './app-version.json'

export default defineConfig({
  plugins: [react()],
  // The Android app's own version (also used by android/app/build.gradle), for the "new version available" check
  define: { __APP_VERSION__: JSON.stringify(appVersion) },
  server: {
    port: 5173,
    // Backend to forward /api to (override with API_URL=http://host:port npm run dev)
    proxy: { '/api': process.env.API_URL || 'http://localhost:8000' },
  },
})
