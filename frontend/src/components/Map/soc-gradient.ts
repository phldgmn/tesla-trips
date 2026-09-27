import type { RouteSample } from "../../utils/route-line";

// Color palette for the continuous SoC gradient: red (≤5%) → orange (5-15%) → yellow (15-25%) → green (25-75%) → blue (>75%).
const SOC_COLOR_STOPS: readonly [
  soc: number,
  r: number,
  g: number,
  b: number,
][] = [
  [0, 0xef, 0x44, 0x44], // 0% - red
  [5, 0xef, 0x44, 0x44], // 5% - red (red only for ≤5%)
  [15, 0xf9, 0x73, 0x16], // 15% - orange (orange for ≤15%)
  [25, 0xea, 0xb3, 0x08], // 25% - yellow
  [75, 0x22, 0xc5, 0x5e], // 75% - green
  [100, 0x3b, 0x82, 0xf6], // 100% - blue
];

function toHex(value: number): string {
  return Math.round(value).toString(16).padStart(2, "0");
}

/** Map an SoC value (0-100) linear to a color along the palette
 * `SOC_COLOR_STOPS`. Unlike a bucket function (fixed color per value range)
 * this provides each SoC value with its own, continuously interpolated color
 * between neighboring reference colors - a prerequisite for
 * the `line-gradient` in `buildSocGradientExpression` to actually
 * flow smoothly instead of consisting of flat color plateaus with short, abrupt
 * transitions at the old bucket boundaries (see its
 * docstring).
 */
export function socToColor(soc: number): string {
  const clamped = Math.min(100, Math.max(0, soc));
  let [socLo, rLo, gLo, bLo] = SOC_COLOR_STOPS[0];
  let [socHi, rHi, gHi, bHi] = SOC_COLOR_STOPS[SOC_COLOR_STOPS.length - 1];
  for (let i = 0; i < SOC_COLOR_STOPS.length - 1; i++) {
    if (
      clamped >= SOC_COLOR_STOPS[i][0] &&
      clamped <= SOC_COLOR_STOPS[i + 1][0]
    ) {
      [socLo, rLo, gLo, bLo] = SOC_COLOR_STOPS[i];
      [socHi, rHi, gHi, bHi] = SOC_COLOR_STOPS[i + 1];
      break;
    }
  }
  const t = socHi === socLo ? 0 : (clamped - socLo) / (socHi - socLo);
  const r = rLo + (rHi - rLo) * t;
  const g = gLo + (gHi - gLo) * t;
  const b = bLo + (bHi - bLo) * t;
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

/** Build the MapLibre `line-gradient` expression for the SoC color gradient along
 * the entire (spliced) route from a single line (instead of many
 * individual segment layers – see `MapVisualization`). `samples` comes from
 * `buildSplicedRoute()` and contains both driving reference points (positioned by `distanz_m`)
 * and `critical`-marked charging stop arrival/departure
 * reference points, which are never skipped during downsampling - otherwise the
 * SoC jump at a charging station (low arrival to higher target SoC)
 * would not be visible. All distance values are relative to the spliced line
 * (including charging detours), matching `line-progress`. Regular
 * reference points are downsampled to at most `maxStops - critical.length` evenly
 * (avoids huge expressions for long trips with
 * thousands of frames); reference points with identical distance (e.g. during
 * a charging stop) are cleaned up since `interpolate` stops must be strictly
 * ascending.
 */
export function buildSocGradientExpression(
  samples: RouteSample[],
  totalDistanceM: number,
  maxStops = 64,
): unknown[] {
  if (samples.length === 0 || totalDistanceM <= 0) {
    return [
      "interpolate",
      ["linear"],
      ["line-progress"],
      0,
      "#3b82f6",
      1,
      "#3b82f6",
    ];
  }

  const sorted = [...samples].sort((a, b) => a.distanceM - b.distanceM);
  const critical = sorted.filter((s) => s.critical);
  const regular = sorted.filter((s) => !s.critical);

  const budget = Math.max(2, maxStops - critical.length);
  const stride = Math.max(1, Math.ceil((regular.length - 1) / (budget - 1)));
  const sampledRegular: RouteSample[] = [];
  for (let i = 0; i < regular.length - 1; i += stride) {
    sampledRegular.push(regular[i]);
  }
  if (regular.length > 0) {
    sampledRegular.push(regular[regular.length - 1]);
  }

  const merged = [...sampledRegular, ...critical].sort(
    (a, b) => a.distanceM - b.distanceM,
  );

  const stops: (number | string)[] = [];
  let lastProgress = -1;
  for (const sample of merged) {
    const progress = Math.min(
      1,
      Math.max(0, sample.distanceM / totalDistanceM),
    );
    if (progress <= lastProgress) continue;
    stops.push(progress, socToColor(sample.socPct));
    lastProgress = progress;
  }
  return ["interpolate", ["linear"], ["line-progress"], ...stops];
}
