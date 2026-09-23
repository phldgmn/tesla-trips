import type {
  FerryExclusion,
  FerryTimeWindow,
  ChargingDurationSpecification,
} from "@/types/trip-request";

import { sameFerryExclusion as sameFerryExclusionF } from "./form-helpers";

export { sameFerryExclusionF as sameFerryExclusion };

/** Ergänzt oder entfernt eine Fährverbindung aus der Ausschlussliste. */
export function toggleFerryExclusion(
  list: FerryExclusion[],
  ferry: FerryExclusion,
  avoid: boolean,
): FerryExclusion[] {
  const alreadyExists = list.some((f) => sameFerryExclusionF(f, ferry));
  if (avoid) {
    return alreadyExists ? list : [...list, ferry];
  }
  return list.filter((f) => !sameFerryExclusionF(f, ferry));
}

/** Setzt oder entfernt das Zeitfenster für eine Fährverbindung. `zeitfenster`
 *  wird entfernt, wenn `departure`/`arrival` beide leer sind. */
export function setFerryTimeWindowFor(
  list: FerryTimeWindow[],
  entry: FerryExclusion,
  departure: string,
  arrival: string,
): FerryTimeWindow[] {
  const rest = list.filter((f) => !sameFerryExclusionF(f, entry));
  if (!departure || !arrival) return rest;
  return [...rest, { ...entry, departure, arrival }];
}

/** Setzt oder entfernt die Ladedauer-Vorgabe für eine Station. Die Vorgabe
 *  wird entfernt, wenn `chargingDurationMin` nicht positiv ist. */
export function setChargingDurationPresetFor(
  list: ChargingDurationSpecification[],
  stationId: string,
  chargingDurationMin: number,
): ChargingDurationSpecification[] {
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
