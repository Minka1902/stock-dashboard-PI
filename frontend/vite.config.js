import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // With the same-origin default BASE (""), proxy API calls from the dev server
  // to the backend so the hot-reload flow still works. In the single-port build
  // the backend serves dist/ and no proxy is involved.
  //
  // The target is an env var because it used to be hard-coded to :8000 while
  // the launcher took an --ApiPort flag: choosing any other port produced a UI
  // that silently couldn't reach its API. start.ps1 sets this to match.
  server: {
    proxy: {
      "/api": process.env.VITE_API_TARGET || "http://localhost:8000",
    },
  },
})
