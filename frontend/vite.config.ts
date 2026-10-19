import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Backend API base (FastAPI). /api is proxied in dev so the browser
// always talks to the same origin.
const BACKEND = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: BACKEND,
        changeOrigin: true,
      },
    },
  },
});
