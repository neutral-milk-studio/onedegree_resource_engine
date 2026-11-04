/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const REVIEW_API = "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    // In live mode the browser calls the API through this proxy, on the same origin.
    proxy: {
      "/review": REVIEW_API,
      "/services": REVIEW_API,
      "/ingest": REVIEW_API,
      "/metrics": REVIEW_API,
      "/audit": REVIEW_API,
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
    css: false,
  },
});
