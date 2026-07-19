import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: proxy /api to the FastAPI backend (default :5173).
// In production, `npm run build` outputs to webui/dist, which the backend serves.
export default defineConfig({
  plugins: [react()],
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
