import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The Python backend serves the built bundle from src/guiltyspark/web via
// importlib.resources, so emit directly there. `base: "./"` keeps asset URLs
// relative. In Docker the web stage runs with the same relative layout
// (WORKDIR /app/frontend), so this one config works for local and image builds.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    // Relative to this config's directory (frontend/). Emits straight into the
    // Python package's web/ so `guiltyspark dashboard` serves the bundle.
    outDir: "../src/guiltyspark/web",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    // `npm run dev` proxies the JSON API to a locally running dashboard.
    proxy: {
      "/api": "http://localhost:8343",
    },
  },
});
