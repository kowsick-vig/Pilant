import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Flask backend (studio.py) runs on 127.0.0.1:5008.
// In dev, Vite proxies API/legacy routes to Flask so the SPA can call
// same-origin paths; in prod, Flask serves frontend/dist directly.
const FLASK_ORIGIN = 'http://127.0.0.1:5008'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': FLASK_ORIGIN,
      '/oauth': FLASK_ORIGIN,
      '/studio/gmail_panel_frame': FLASK_ORIGIN,
      // Legacy server-rendered subsystems left out of the React migration
      // (Gmail live inbox actions, Composer canvas, Customer-360) still need
      // to be reachable through the same origin during dev.
      '/inbox': FLASK_ORIGIN,
      '/message': FLASK_ORIGIN,
      '/compose': FLASK_ORIGIN,
      '/composer': FLASK_ORIGIN,
      '/customers': FLASK_ORIGIN,
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
