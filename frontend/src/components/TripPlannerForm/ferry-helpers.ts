import type {
  FerryExclusion,
  FerryTimeWindow,
  ChargingDurationTarget,
} from "@/types/trip-request";

import { sameFerryExclusion as sameFerryExclusionF } from "./form-helpers";

export { sameFerryExclusionF as sameFaehrAusschluss };

/** Ergänzt oder entfernt eine Fährverbindung aus der Ausschlussliste. */
export function toggleFerryExclusion(
  list: FerryExclusion[],
  faehre: FerryExclusion,
  avoid: boolean,
): FerryExclusion[] {
  const alreadyExists = list.some((f) => sameFerryExclusionF(f, faehre));
  if (avoid) {
    return alreadyExists ? list : [...list, faehre];
  }
  return list.filter((f) => !sameFerryExclusionF(f, faehre));
}

/** Setzt oder entfernt das Zeitfenster für eine Fährverbindung. `zeitfenster`
 *  wird entfernt, wenn `abfahrt`/`ankunft` beide leer sind. */
export function setFerryTimeWindowFor(
  list: FerryTimeWindow[],
  entry: FerryExclusion,
  abfahrt: string,
  ankunft: string,
): FerryTimeWindow[] {
  const rest = list.filter((f) => !sameFerryExclusionF(f, entry));
  if (!abfahrt || !ankunft) return rest;
  return [...rest, { ...entry, abfahrt, ankunft }];
}

/** Setzt oder entfernt die Ladedauer-Vorgabe für eine Station. Die Vorgabe
 *  wird entfernt, wenn `ladedauerMin` nicht positiv ist. */
export function setChargingDurationPresetFor(
  list: ChargingDurationTarget[],
  stationId: string,
  chargingDurationMin: number,
): ChargingDurationTarget[] {
  const rest = list.filter((v) => v.stationId !== stationId);
  if (!(chargingDurationMin > 0)) return rest;
  return [
    ...rest,
    {
      stationId: stationId,
      chargingDurationS: Math.round(chargingDurationMin * 60),
    },
  ];
}

/** Stabiler Identitäts-Schlüssel für eine Fährverbindung (Name + Bounding Box),
 *  zur Indizierung von React-State und -Listen abseits von Array-Index. */
export function ferryKey(entry: FerryExclusion): string {
  return `${entry.name}|${entry.bboxSw.join(",")}|${entry.bboxNe.join(",")}`;
}
