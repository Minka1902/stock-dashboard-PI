import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // With the same-origin default BASE (""), proxy API calls from the dev server
  // to the backend so the hot-reload flow still works. In the single-port build
  // the backend serves dist/ and no proxy is involved.
  //
  // The target is an env var so a backend on another port (uvicorn --port N)
  // gets a dev UI that can reach it: VITE_API_TARGET=http://localhost:N.
  server: {
    proxy: {
      "/api": process.env.VITE_API_TARGET || "http://localhost:8000",
    },
  },
})
