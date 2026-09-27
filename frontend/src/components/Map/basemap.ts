import { type StyleSpecification, type SourceSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// Liberty style (OpenMapTiles schema) from openfreemap.org, vendored once
// (see README.md). Sprite/glyphs stay on the CDN; only the vector tile
// source is overridden below to the self-hosted tile server (see
// buildBasemapStyle).
import libertyStyleRaw from "../../assets/liberty-style.json";

// Local vector tile server (docker-compose.yml, service "tiles"; see
// scripts/build_basemap_tiles.sh, run.sh `start tiles`). Serves PMTiles via
// `pmtiles serve` as a ZXY/TileJSON endpoint, built from the same DE+DK+SE OSM
// extract GraphHopper uses for routing. Override with `VITE_TILES_URL`.
export const DEFAULT_TILES_URL = "http://localhost:8081";
const TILES_BASE_URL: string =
  import.meta.env.VITE_TILES_URL || DEFAULT_TILES_URL;

/** Replaces only the `openmaptiles` vector source in a MapLibre style with
 *  the self-hosted tile server; all other fields (sprite, glyphs, layers,
 *  other sources) remain unchanged.
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

/** Unmodified public OpenFreeMap Liberty style (tiles, sprite and glyphs from
 *  tiles.openfreemap.org). Used by the interactive export, which cannot reach
 *  the local tile server. */
export const onlineBasemapStyle =
  libertyStyleRaw as unknown as StyleSpecification;
