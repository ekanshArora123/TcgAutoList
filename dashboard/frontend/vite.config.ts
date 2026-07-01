import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Read env from the single repo-root .env (two levels up) instead of a
  // frontend-local file, so all config lives in one place.
  envDir: '../../',
  // Expose API_-prefixed env vars (e.g. API_BASE) to client code via
  // import.meta.env, alongside Vite's default VITE_ prefix. Only these
  // prefixes reach the bundle — backend secrets in the same .env stay
  // server-side. Picked up from .env locally and from the host's build env
  // (e.g. Vercel project settings) in prod. Anything exposed is non-secret.
  envPrefix: ['VITE_', 'API_'],
})
