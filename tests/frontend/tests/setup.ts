/** Vitest-Setup: minimale Browser-API-Polyfills für jsdom.
 *
 * `maplibre-gl` ruft beim Import `window.URL.createObjectURL` auf (Worker-Setup),
 * was jsdom nicht implementiert. Für Unit-Tests reicht ein No-Op-Stub – echtes
 * Kartenverhalten wird hier nicht getestet (siehe `Map.tsx`-Kommentar zu
 * MapLibre-Worker-Setup).
 */
if (typeof URL.createObjectURL !== "function") {
  URL.createObjectURL = () => "blob:mock";
}
if (typeof URL.revokeObjectURL !== "function") {
  URL.revokeObjectURL = () => {};
}
