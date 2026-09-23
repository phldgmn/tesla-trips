import type { LucideIcon } from "lucide-react";

import type { Stop, FerryExclusion } from "@/types/trip-request";

import { validateStops } from "@/types/trip-request";

import { formatTime } from "@/utils/datetime-utils";

import { Flag, MapPin, Milestone } from "lucide-react";

/** Erzeugt den Rollen-Namen eines Stopps anhand seiner Position im Array. */
export function getStopRole(stops: Stop[], index: number): string {
  if (stops.length === 0) return "";
  if (index === 0) return "Start";
  if (index === stops.length - 1) return "Ziel";
  return `Stop ${index}`;
}

/** Prüft, ob ein Address-String wie ein roher Koordinaten-String aussieht
 *  (fallback-Format, das von reverseGeocode erzeugt wird, z. B. "52.5200, 13.4050"). */
export function isRawCoordinateLabel(address: string): boolean {
  return /^-?\d+\.?\d*,\s*-?\d+\.?\d*$/.test(address.trim());
}

/** Prüft, ob eine Adresse als "nicht aufgelöst" gilt (leer oder roh-Koordinaten-Format). */
export function isUnresolvedAddress(stop: Stop): boolean {
  return (
    !stop.position || stop.address === "" || isRawCoordinateLabel(stop.address)
  );
}

/** Tauscht zwei Elemente im Array an den gegebenen Indizes. */
export function swapStops(stops: Stop[], from: number, to: number): Stop[] {
  if (
    from < 0 ||
    to < 0 ||
    from >= stops.length ||
    to >= stops.length ||
    from === to
  ) {
    return stops;
  }
  const updated = [...stops];
  // Array-Destructuring-Tausch
  const tmp = updated[from];
  updated[from] = updated[to];
  updated[to] = tmp;
  return updated;
}

/** Validiert die Formulardaten (Stops + SoC) und gibt Fehlermeldungen zurück. */
export function validateForm(args: {
  stops: Stop[];
  startSoc: number;
  minArrivalSocPct: number;
  minChargingTimeMin: number;
  maxChargeSocPct?: number;
  targetSoc: number;
}): string[] {
  const errors: string[] = [];

  // Stop-spezifische Validierung (aus dem Vertrag)
  errors.push(...validateStops(args.stops));

  // SoC-Validierung
  if (
    typeof args.startSoc !== "number" ||
    isNaN(args.startSoc) ||
    args.startSoc < 0 ||
    args.startSoc > 100
  ) {
    errors.push("Start-SoC muss zwischen 0 und 100 % liegen.");
  }
  if (
    typeof args.targetSoc !== "number" ||
    isNaN(args.targetSoc) ||
    args.targetSoc < 0 ||
    args.targetSoc > 100
  ) {
    errors.push("Ziel-SoC muss zwischen 0 und 100 % liegen.");
  }
  if (
    typeof args.minChargingTimeMin !== "number" ||
    isNaN(args.minChargingTimeMin) ||
    args.minChargingTimeMin < 0 ||
    args.minChargingTimeMin > 30
  ) {
    errors.push("Min. Ladedauer muss zwischen 0 und 30 Minuten liegen.");
  }
  if (
    typeof args.minArrivalSocPct !== "number" ||
    isNaN(args.minArrivalSocPct) ||
    args.minArrivalSocPct < 0 ||
    args.minArrivalSocPct > 100
  ) {
    errors.push("Min. SoC an Ladestationen muss zwischen 0 und 100 % liegen.");
  }
  if (
    args.maxChargeSocPct !== undefined &&
    (typeof args.maxChargeSocPct !== "number" ||
      isNaN(args.maxChargeSocPct) ||
      args.maxChargeSocPct < 0 ||
      args.maxChargeSocPct > 100)
  ) {
    errors.push("Max. Lade-SoC muss zwischen 0 und 100 % liegen.");
  }

  return errors;
}

/** Vergleicht zwei FaehrAusschluss-Einträge auf inhaltliche Gleichheit. */
export function sameFerryExclusion(
  a: FerryExclusion,
  b: FerryExclusion,
): boolean {
  return (
    a.name === b.name &&
    a.bboxSw[0] === b.bboxSw[0] &&
    a.bboxSw[1] === b.bboxSw[1] &&
    a.bboxNe[0] === b.bboxNe[0] &&
    a.bboxNe[1] === b.bboxNe[1]
  );
}

/** Ergänzt oder entfernt eine Fährverbindung aus der Ausschlussliste. */
export function toggleFerryExclusion(
  list: FerryExclusion[],
  faehre: FerryExclusion,
  avoid: boolean,
): FerryExclusion[] {
  const alreadyExists = list.some((f) => sameFerryExclusion(f, faehre));
  if (avoid) {
    return alreadyExists ? list : [...list, faehre];
  }
  return list.filter((f) => !sameFerryExclusion(f, faehre));
}

/** Stabiler Identitäts-Schlüssel für eine Fährverbindung (Name + Bounding Box),
 *  zur Indizierung von React-State und -Listen abseits von Array-Index. */
export function ferryKey(entry: FerryExclusion): string {
  return `${entry.name}|${entry.bboxSw.join(",")}|${entry.bboxNe.join(",")}`;
}

/** Icon + Hintergrundfarbe des Timeline-Markers für einen Stopp, abhängig
 *  von dessen Rolle (Start/Zwischenstopp/Ziel). */
export function getStopTimelineIcon(
  stops: Stop[],
  index: number,
): { Icon: LucideIcon; background: string } {
  if (index === 0) return { Icon: Flag, background: "#e0e7ff" };
  if (index === stops.length - 1) {
    return { Icon: MapPin, background: "#fef3c7" };
  }
  return { Icon: Milestone, background: "#f3f4f6" };
}

/** Prüft, ob zwei Zeitpunkte als angezeigte Uhrzeit (HH:mm) unterscheidbar
 *  sind. Unterdrückt ein redundantes zweites Zeit-Badge, wenn Ankunft und
 *  Abfahrt eines Routen-Eintrags auf dieselbe Minute fallen - insbesondere
 *  bei Fähren, deren Zeitpunkt mangels simulierter Fahrt "auf dem Wasser"
 *  nur als einzelner Positions-Cluster geschätzt wird (siehe
 *  `estimatePositionTiming` in `timing-utils.ts`), Ankunft und Abfahrt dort
 *  also identisch sein können. `null` gilt immer als unterscheidbar (der
 *  Aufrufer entscheidet anhand von `null` bereits, ob überhaupt ein Badge
 *  gerendert wird). */
export function differsAsTime(a: string | null, b: string | null): boolean {
  if (a === null || b === null) return true;
  return formatTime(a) !== formatTime(b);
}

/** Präfix, unter dem alle Tesla-Supercharger-Stationen in `data/superchargers.ts`
 *  benannt sind (z. B. "Tesla Supercharger - Berlin Alexanderplatz"). In der
 *  kompakten Routen-Timeline redundant, da das Zap-Icon des Ladehalts bereits
 *  eindeutig als Ladestopp erkennbar ist. */
const SUPERCHARGER_NAME_PREFIX = "Tesla Supercharger - ";

/** Kürzt den Anzeigenamen einer Ladestation um den redundanten
 *  "Tesla Supercharger - "-Präfix (siehe `SUPERCHARGER_NAME_PRAEFIX`). Namen
 *  ohne diesen Präfix (z. B. andere Anbieter) bleiben unverändert. */
export function formatChargingStationName(name: string): string {
  return name.startsWith(SUPERCHARGER_NAME_PREFIX)
    ? name.slice(SUPERCHARGER_NAME_PREFIX.length)
    : name;
}

/** Formatiert eine Fahrsegment-Distanz in km, eine Nachkommastelle,
 *  deutsches Zahlenformat (Komma statt Punkt). */
export function formatDrivingSegmentDistance(distanceKm: number): string {
  return `${distanceKm.toLocaleString("de-DE", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })} km`;
}

/** Formatiert eine Fahrsegment-Dauer in Minuten als "Xh Ymin" bzw. "Ymin"
 *  (gleiches Format wie `formatChargingDuration` in `Map.tsx`). */
export function formatDrivingSegmentDuration(durationMin: number): string {
  const totalMinutes = Math.round(durationMin);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours > 0 ? `${hours}h ${minutes}min` : `${minutes}min`;
}
