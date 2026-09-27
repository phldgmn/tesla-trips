/// <reference types="vite/client" />
/// <reference types="geojson" />

interface ImportMetaEnv {
  /** Base URL of the vector tile server (default: http://localhost:8081). */
  readonly VITE_TILES_URL?: string;
}

declare module "virtual:trip-export-bundle" {
  const bundle: import("./export/export-html").ExportBundle;
  export default bundle;
}
