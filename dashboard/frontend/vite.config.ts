import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Expose API_-prefixed env vars (e.g. API_BASE) to client code via
  // import.meta.env, alongside Vite's default VITE_ prefix. Picked up from
  // .env files locally and from the host's build env (e.g. Vercel) in prod.
  // Only non-secret values belong here — anything exposed is baked into the
  // client bundle.
  envPrefix: ['VITE_', 'API_'],
})
