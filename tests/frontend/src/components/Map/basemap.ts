import {
  setWorkerUrl,
  type StyleSpecification,
  type SourceSpecification,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// MapLibre GL JS berechnet die Worker-URL zur Laufzeit relativ zu
// `import.meta.url` des eigenen Moduls (parst Vector-Tiles abseits des
// Main-Threads). Im Vite-Production-Build wird diese dynamisch berechnete
// URL von Rollup nicht erkannt/mitgebündelt - der Worker-Request würde ins
// Leere laufen (Karte bleibt dauerhaft im Ladezustand, kein `load`-Event,
// siehe `vite.config.ts`, `optimizeDeps.exclude` für den Dev-Server-seitigen
// Teil des gleichen Problems). Der `?worker&url`-Import lässt Vite die
// Worker-Datei als eigenständigen, korrekt referenzierten Chunk bündeln;
// `setWorkerUrl()` überschreibt MapLibres eigene (kaputte) Berechnung damit.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
// Liberty-Style (OpenMapTiles-Schema) von openfreemap.org, einmalig
// vendored (siehe README.md). Sprite/Glyphs bleiben bei der CDN, nur die
// Vektor-Tile-Quelle wird unten auf den selbst gehosteten Tile-Server
// umgebogen (siehe buildBasemapStyle).
import libertyStyleRaw from "../../assets/liberty-style.json";

setWorkerUrl(maplibreWorkerUrl);

// Lokaler Vektor-Tile-Server (docker-compose.yml, Service "tiles"; siehe
// scripts/build_basemap_tiles.sh, run.sh `start tiles`). Liefert PMTiles
// per `pmtiles serve` als ZXY/TileJSON-Endpunkt aus, gebaut aus demselben
// DE+DK+SE-OSM-Extrakt, den auch GraphHopper fuers Routing nutzt.
const TILES_BASE_URL = "http://localhost:8081";

/** Ersetzt in einem MapLibre-Style nur die `openmaptiles`-Vektor-Quelle
 * durch den selbst gehosteten Tile-Server; alle anderen Felder (Sprite,
 * Glyphs, Layer, weitere Quellen) bleiben unveraendert.
 */
export function buildBasemapStyle(
  baseStyle: StyleSpecification,
  tilesBaseUrl: string,
): StyleSpecification {
  return {
    ...baseStyle,
    sources: {
      ...baseStyle.sources,
      openmaptiles: {
        ...baseStyle.sources.openmaptiles,
        url: `${tilesBaseUrl}/basemap.json`,
      } as SourceSpecification,
    },
  };
}

export const basemapStyle = buildBasemapStyle(
  libertyStyleRaw as unknown as StyleSpecification,
  TILES_BASE_URL,
);
