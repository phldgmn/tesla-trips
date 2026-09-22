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
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  optimizeDeps: {
    // MapLibre GL JS spawnt beim Start einen Web-Worker (parst Vector-Tiles
    // abseits des Main-Threads). Der Worker-URL wird intern relativ zu
    // `import.meta.url` des maplibre-gl-Moduls berechnet. Bündelt Vites
    // Dependency-Pre-Bundling maplibre-gl in `.vite/deps/`, landet die
    // berechnete Worker-URL dort ebenfalls - aber Vite kopiert die
    // Worker-Datei (`maplibre-gl-worker.mjs`) nicht mit in dieses
    // Verzeichnis, wodurch der Worker-Request mit 404 fehlschlägt. Die
    // Karte bleibt dann dauerhaft im Ladezustand (kein `load`/`idle`-Event),
    // Route/Ladehalte-Marker/`fitBounds` werden nie gerendert. Fix (siehe
    // MapLibre-GL-JS-Doku): maplibre-gl von der Pre-Bundling ausschließen,
    // damit Vite es unveraendert aus node_modules ausliefert und die
    // Worker-URL korrekt aufgelöst wird.
    exclude: ["maplibre-gl"],
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
