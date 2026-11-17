import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: proxy API sang backend FastAPI (uvicorn app.main:app --port 8000)
const API = process.env.MEP_API_URL ?? "http://localhost:8000";

export default defineConfig({
  // Nhúng trong web Tri thức: VITE_BASE=/thongtinduan/ (xem src/deploy.ts, deploy/trithuc)
  base: process.env.VITE_BASE || "/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/chat": API,
      "/conversations": API,
      "/mcp": API,
      "/knowledge": API,
      "/projects": API,
    },
  },
});
