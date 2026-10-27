import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    // В разработке API проксируется на локальный backend.
    proxy: { "/api": "http://localhost:8000" },
  },
});
