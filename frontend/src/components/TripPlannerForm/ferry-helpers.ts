import type {
  FerryExclusion,
  FaehrZeitfenster,
  LadedauerVorgabe,
} from "@/types/trip-request";

import { sameFaehrAusschluss as sameFaehrAusschlussF } from "./form-helpers";

export { sameFaehrAusschlussF as sameFaehrAusschluss };

/** Ergänzt oder entfernt eine Fährverbindung aus der Ausschlussliste. */
export function toggleFaehrAusschluss(
  liste: FerryExclusion[],
  faehre: FerryExclusion,
  vermeiden: boolean,
): FerryExclusion[] {
  const bereitsVorhanden = liste.some((f) => sameFaehrAusschlussF(f, faehre));
  if (vermeiden) {
    return bereitsVorhanden ? liste : [...liste, faehre];
  }
  return liste.filter((f) => !sameFaehrAusschlussF(f, faehre));
}

/** Setzt oder entfernt das Zeitfenster für eine Fährverbindung. `zeitfenster`
 *  wird entfernt, wenn `abfahrt`/`ankunft` beide leer sind. */
export function setFaehrZeitfensterFuer(
  liste: FaehrZeitfenster[],
  eintrag: FerryExclusion,
  abfahrt: string,
  ankunft: string,
): FaehrZeitfenster[] {
  const rest = liste.filter((f) => !sameFaehrAusschlussF(f, eintrag));
  if (!abfahrt || !ankunft) return rest;
  return [...rest, { ...eintrag, abfahrt, ankunft }];
}

/** Setzt oder entfernt die Ladedauer-Vorgabe für eine Station. Die Vorgabe
 *  wird entfernt, wenn `ladedauerMin` nicht positiv ist. */
export function setLadedauerVorgabeFuer(
  liste: LadedauerVorgabe[],
  stationId: string,
  ladedauerMin: number,
): LadedauerVorgabe[] {
  const rest = liste.filter((v) => v.station_id !== stationId);
  if (!(ladedauerMin > 0)) return rest;
  return [
    ...rest,
    { station_id: stationId, ladedauer_s: Math.round(ladedauerMin * 60) },
  ];
}

/** Stabiler Identitäts-Schlüssel für eine Fährverbindung (Name + Bounding Box),
 *  zur Indizierung von React-State und -Listen abseits von Array-Index. */
export function faehrKey(eintrag: FerryExclusion): string {
  return `${eintrag.name}|${eintrag.bbox_sw.join(",")}|${eintrag.bbox_no.join(",")}`;
}
