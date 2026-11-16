import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API runs on the host at :8000; requests to /api are proxied there
// so the browser sees a single origin, exactly as behind nginx in Docker.
const apiTarget = process.env.VITE_API_PROXY ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  // The PDF viewer chunk (pdf.js, ~630 kB) is loaded lazily, so it may exceed the default limit.
  build: { chunkSizeWarningLimit: 700 },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
