import path from "path";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "src"),
    },
  },
  server: {
    port: 3000,
    proxy: {
      // Leitet Frontend-API-Aufrufe an das lokale FastAPI-Backend weiter
      // (`uv run uvicorn tripplanner.trip_input.api:app --reload`, Port 8000).
      // Vermeidet CORS-Konfiguration im Backend für die lokale Entwicklung.
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  test: {
    globals: true,
    // jsdom: einige Komponenten-Hilfsfunktionen (z. B. Map.tsx::buildMarkerElement)
    // erzeugen echte DOM-Elemente und brauchen daher eine DOM-Umgebung im Test.
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
    alias: {
      "@": path.resolve(import.meta.dirname, "src"),
    },
  },
});
