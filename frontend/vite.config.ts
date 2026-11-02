import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  // Keep this checkout's transformed module graph isolated. Together with
  // strictPort this prevents an older Vite process from another checkout from
  // being mistaken for the pyc-new development server.
  cacheDir: 'node_modules/.vite-pyc-new',
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
  },
})
