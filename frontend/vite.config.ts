import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const browserSecurityHeaders = {
  "Content-Security-Policy": "frame-ancestors 'none'",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "Referrer-Policy": "no-referrer"
};
const apiProxy = {
  "/api": "http://127.0.0.1:8000"
};

export default defineConfig({
  plugins: [react()],
  build: {
    // Monaco is isolated behind a second-level lazy boundary; keep warnings useful for unexpected growth.
    chunkSizeWarningLimit: 2800
  },
  server: {
    port: 5173,
    headers: browserSecurityHeaders,
    proxy: apiProxy
  },
  preview: {
    headers: browserSecurityHeaders,
    proxy: apiProxy
  }
});
