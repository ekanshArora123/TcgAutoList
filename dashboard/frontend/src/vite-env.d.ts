/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Backend origin for API calls, e.g. "http://localhost:5000" (no /api, no trailing slash). */
  readonly API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
