/// <reference types="vite/client" />
/// <reference types="geojson" />

interface ImportMetaEnv {
  /** Base URL of the vector tile server (default: http://localhost:8081). */
  readonly VITE_TILES_URL?: string;
}
