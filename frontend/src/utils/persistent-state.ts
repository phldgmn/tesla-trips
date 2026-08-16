import { useEffect, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";

/** Präfix für alle in `localStorage` abgelegten App-Zustände, versioniert,
 *  damit ein zukünftiger inkompatibler Schema-Wechsel nicht an alten,
 *  nicht mehr passenden Werten aus `localStorage` scheitert (siehe
 *  `readPersistedState` – ein Parse-/Validierungsfehler fällt einfach auf
 *  `initialValue` zurück, statt die App abstürzen zu lassen). */
const STORAGE_PREFIX = "tesla-trips:v1:";

function readPersistedState<T>(key: string): T | undefined {
  if (typeof window === "undefined") return undefined;
  try {
    const raw = window.localStorage.getItem(STORAGE_PREFIX + key);
    if (raw === null) return undefined;
    return JSON.parse(raw) as T;
  } catch {
    // Beschädigter/fremder Wert (z. B. nach manueller Bearbeitung der
    // DevTools oder einem inkompatiblen Schema-Wechsel) - ignorieren und
    // stattdessen den Default verwenden, statt die App zu blockieren.
    return undefined;
  }
}

/** Wie `useState`, aber der Wert wird zusätzlich unter `key` in
 *  `localStorage` gespiegelt und beim Mount von dort wiederhergestellt.
 *  Fällt bei fehlendem/beschädigtem/nicht verfügbarem `localStorage`
 *  (z. B. privater Modus mit deaktiviertem Storage, SSR) transparent auf
 *  reinen In-Memory-State zurück - `initialValue` wird dann bei jedem
 *  Laden neu verwendet. */
export function usePersistentState<T>(
  key: string,
  initialValue: T | (() => T),
): [T, Dispatch<SetStateAction<T>>] {
  const [state, setState] = useState<T>(() => {
    const persisted = readPersistedState<T>(key);
    if (persisted !== undefined) return persisted;
    return initialValue instanceof Function ? initialValue() : initialValue;
  });

  // Verhindert, dass der allererste Schreibvorgang (identisch mit dem
  // gerade gelesenen/initialen Wert) unnötig `localStorage` anfasst.
  const isFirstRender = useRef(true);

  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }
    try {
      window.localStorage.setItem(STORAGE_PREFIX + key, JSON.stringify(state));
    } catch {
      // z. B. Speicherkontingent überschritten oder Storage deaktiviert -
      // Persistenz ist ein Komfortfeature, ein Fehlschlag darf die
      // eigentliche Interaktion nicht stören.
    }
  }, [key, state]);

  return [state, setState];
}
