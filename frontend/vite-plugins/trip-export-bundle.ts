/** Vite plugin for the interactive trip export.
 *
 * Exposes a virtual module `virtual:trip-export-bundle` whose default export
 * is the pre-bundled, self-contained viewer (JS + CSS) plus the MapLibre
 * worker source - all inlined into the exported HTML file
 * (see `src/export/export-html.ts`).
 *
 * Both bundles are produced by nested `build()` calls (`write: false`,
 * minified, no code splitting) so the exported file needs no external
 * assets: the viewer entry re-creates the read-only viewer app, and the
 * worker entry inlines `maplibre-gl-shared.mjs` so the worker is a single
 * self-contained script.
 */

import path from "path";
import react from "@vitejs/plugin-react";
import { build, type Plugin } from "vite";

const VIRTUAL_ID = "virtual:trip-export-bundle";
const RESOLVED_ID = "\0" + VIRTUAL_ID;

/** Bundles `entry` into a single minified JS chunk (plus one CSS asset when
 *  `cssCodeSplit` is off). Throws unless exactly one JS chunk is produced -
 *  anything else would silently lose code in the single-file export. */
async function bundleSingleFile(
  root: string,
  entry: string,
  format: "es" | "iife" = "es",
): Promise<{ js: string; css: string }> {
  // The nested build inherits the parent's process.env.NODE_ENV (e.g.
  // "development" during `vite dev`), which makes @vitejs/plugin-react pick
  // the dev JSX runtime (jsxDEV) - it crashes in a real browser. `mode:
  // "production"` alone does NOT reset an already-set NODE_ENV, so force it.
  const previousNodeEnv = process.env.NODE_ENV;
  process.env.NODE_ENV = "production";
  try {
    const out = await build({
      configFile: false,
      root,
      logLevel: "warn",
      mode: "production",
      publicDir: false,
      plugins: [react()],
      resolve: {
        alias: { "@": path.resolve(root, "src") },
      },
      define: { "process.env.NODE_ENV": JSON.stringify("production") },
      build: {
        write: false,
        minify: true,
        sourcemap: false,
        cssCodeSplit: false,
        assetsInlineLimit: Number.MAX_SAFE_INTEGER,
        lib: {
          entry,
          formats: [format],
          // Required by rolldown when the format is "iife" (unused for "es").
          name: "TripExportWorker",
          fileName: () => "bundle.js",
        },
      },
      rolldownOptions: { output: { codeSplitting: false } },
    });
    const outputs = (Array.isArray(out) ? out : [out]).map((o) => {
      if (!o || typeof o !== "object" || !("output" in o)) {
        throw new Error("trip-export-bundle: unexpected watcher output");
      }
      return o.output;
    });
    let js = "";
    const cssParts: string[] = [];
    let chunkCount = 0;
    for (const item of outputs.flat()) {
      if (item.type === "chunk") {
        chunkCount += 1;
        if (chunkCount > 1) {
          throw new Error(
            `trip-export-bundle: expected a single JS chunk for ${entry}, got ${chunkCount}`,
          );
        }
        js = item.code;
      } else if (item.type === "asset" && item.fileName.endsWith(".css")) {
        cssParts.push(String(item.source));
      }
    }
    if (chunkCount === 0) {
      throw new Error(`trip-export-bundle: no JS chunk produced for ${entry}`);
    }
    return { js, css: cssParts.join("\n") };
  } finally {
    if (previousNodeEnv === undefined) {
      delete process.env.NODE_ENV;
    } else {
      process.env.NODE_ENV = previousNodeEnv;
    }
  }
}

/** Plugin providing `virtual:trip-export-bundle` (see module docstring). */
export function tripExportBundle(root: string): Plugin {
  // ponytail: the cached promise lives for the process (dev-server) lifetime,
  // so editing the viewer source requires a dev-server restart; add
  // `this.addWatchFile` invalidation if that becomes annoying.
  let cached: Promise<{
    viewerJs: string;
    viewerCss: string;
    workerJs: string;
  }> | null = null;

  return {
    name: "trip-export-bundle",
    resolveId(id) {
      if (id === VIRTUAL_ID) return RESOLVED_ID;
      return undefined;
    },
    load(id) {
      if (id !== RESOLVED_ID) return undefined;
      cached ??= Promise.all([
        bundleSingleFile(
          root,
          path.resolve(root, "src/export/viewer/main.tsx"),
        ),
        // IIFE (classic worker script): module workers from blob URLs fail on
        // file:// pages (opaque origin - WebKit and Chrome block the load,
        // and the failure is ASYNC, so MapLibre's try/catch around
        // `new Worker(url, { type: "module" })` never falls back to classic;
        // see `Dw` in maplibre-gl.mjs). The viewer appends "#.cjs" to the
        // blob URL so MapLibre's `!url.endsWith(".cjs")` heuristic picks the
        // classic worker directly. An IIFE has no imports, so classic works.
        bundleSingleFile(
          root,
          path.resolve(
            root,
            "node_modules/maplibre-gl/dist/maplibre-gl-worker.mjs",
          ),
          "iife",
        ),
      ]).then(([viewer, worker]) => ({
        viewerJs: viewer.js,
        viewerCss: viewer.css,
        workerJs: worker.js,
      }));
      return cached.then(
        (bundle) => `export default ${JSON.stringify(bundle)};`,
      );
    },
  };
}
