import type {
  FerryExclusion,
  FerryTimeWindow,
  ChargingDurationSpecification,
} from "@/types/trip-request";

import { sameFerryExclusion as sameFerryExclusionF } from "./form-helpers";

export { sameFerryExclusionF as sameFerryExclusion };

/** Add or remove a ferry connection from the exclusion list. */
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

/** Set or remove the time window for a ferry connection. The window
 *  is removed when both `departure`/`arrival` are empty. */
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

/** Set or remove the charging duration preset for a station. The preset
 *  is removed when `chargingDurationMin` is not positive. */
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

/** Stable identity key for a ferry connection (name + bounding box),
 *  for indexing React state and lists outside of array index. */
export function ferryKey(entry: FerryExclusion): string {
  return `${entry.name}|${entry.bboxSw.join(",")}|${entry.bboxNe.join(",")}`;
}
