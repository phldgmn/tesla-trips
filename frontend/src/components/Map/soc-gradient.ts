import type { RouteSample } from "../../utils/route-line";

// Farbpalette für den kontinuierlichen SoC-Verlauf: rot (≤5%) → orange (5-15%) → gelb (15-25%) → grün (25-75%) → blau (>75%).
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

/** Bildet einen SoC-Wert (0–100) linear auf eine Farbe entlang der Palette
 * `SOC_COLOR_STOPS` ab. Anders als eine Bucket-Funktion (feste Farbe je
 * Wertebereich) liefert dies für jeden SoC-Wert eine eigene, kontinuierlich
 * zwischen den Nachbar-Stützfarben interpolierte Farbe - Voraussetzung dafür,
 * dass der `line-gradient` in `buildSocGradientExpression` tatsächlich
 * stufenlos verläuft statt aus flachen Farbplateaus mit kurzen, abrupten
 * Übergängen an den alten Bucket-Grenzen zu bestehen (siehe dortiger
 * Docstring).
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

/** Baut die MapLibre `line-gradient`-Expression für den SoC-Farbverlauf entlang
 * der gesamten (gespliceten) Route aus einer einzigen Linie (statt vieler
 * einzelner Segment-Layer – siehe `MapVisualization`). `samples` kommt aus
 * `buildSplicedRoute()` und enthält sowohl Fahr-Stützpunkte (per `distanz_m`
 * positioniert) als auch `critical`-markierte Ladehalt-Ankunfts-/Abfahrts-
 * Stützpunkte, die beim Downsampling nie übersprungen werden - sonst wäre der
 * SoC-Sprung an einer Ladestation (niedriger Ankunfts- zu höherem Ziel-SoC)
 * nicht sichtbar. Alle Distanzwerte sind relativ zur gespliceten Linie
 * (inkl. Ladehalt-Abstecher), passend zu `line-progress`. Reguläre
 * Stützpunkte werden auf maximal `maxStops - critical.length` gleichmässig
 * heruntergesampelt (vermeidet riesige Expressions bei langen Trips mit
 * tausenden Frames); Stützpunkte mit identischer Distanz (z. B. während
 * eines Ladehalts) werden bereinigt, da `interpolate`-Stops strikt
 * aufsteigend sein müssen.
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
