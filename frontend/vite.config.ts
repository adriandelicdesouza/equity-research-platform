import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Dev server proxies the API to the loopback-bound dev gateway (make up-dev).
export default defineConfig({
  plugins: [react()],
  server: {
    host: "localhost",
    port: 5173,
    strictPort: true,
    proxy: { "/api": { target: "http://localhost:8091", changeOrigin: false } },
  },
  build: { sourcemap: false },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
