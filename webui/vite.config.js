import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";

// Dev: proxy /api to the FastAPI backend (default :5173).
// In production, `npm run build` outputs to webui/dist, which the backend serves.
const pkg = JSON.parse(readFileSync(new URL("./package.json", import.meta.url)));

export default defineConfig({
  plugins: [react()],
  define: {
    // Web UI version, independent of the backend version. Read from package.json
    // so the UI badge reflects the frontend's own release, not the server's.
    "import.meta.env.WEBUI_VERSION": JSON.stringify(pkg.version),
  },
  server: {
    port: 5174,
    proxy: {
      "/api": "http://localhost:5173",
    },
  },
  build: {
    outDir: "dist",
  },
});
