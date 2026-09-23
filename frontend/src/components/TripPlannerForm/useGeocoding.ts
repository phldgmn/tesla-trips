import { useState, useEffect, useRef, useCallback } from "react";
import type { Stop } from "../../types/trip-request";
import { searchAddress, reverseGeocode } from "../../api/geocoding";
import { isUnresolvedAddress } from "./form-helpers";

export interface GeocodingState {
  /** The text the current suggestions are FOR (empty = closed). */
  query: string;
  /** Current dropdown results. */
  suggestions: GeocodeSuggestionDisplay[];
  /** Loading indicator. */
  loading: boolean;
}

export interface GeocodeSuggestionDisplay {
  label: string;
  position: [number, number];
}

const GEOCODING_DEBOUNCE_MS = 400;
export const GEOCODING_MIN_QUERY_LENGTH = 3;

/** Address search with debounced suggestions per stop, plus reverse
 *  geocoding of stops whose position is known but whose address is not
 *  human-readable yet (e.g. placed on the map). */
export function useGeocoding(
  stops: Stop[],
  onStopsChange: (stops: Stop[]) => void,
) {
  const [geocodingStates, setGeocodingStates] = useState<
    Record<string, GeocodingState>
  >({});
  const abortRef = useRef<AbortController | null>(null);
  // Debounce timer. MUST be cleared on every keystroke - otherwise each
  // keystroke schedules its own independent timer that always fires, which
  // sends ONE Nominatim request PER CHARACTER instead of one after typing
  // stops, violating the 1 req/s usage policy (HTTP 429).
  const timeoutRef = useRef<number | null>(null);
  // Reverse-geocoding guard: "{lat},{lon}" keys already resolved once.
  const resolvedPositionsRef = useRef<Set<string>>(new Set());

  // Reverse geocoding: runs only when the serialized position list changes,
  // since the `stops`/`onStopsChange` closure may otherwise be stale.
  const positionsKey = stops
    .map((s) => (s.position ? `${s.position[0]},${s.position[1]}` : "null"))
    .join("|");
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    for (const stop of stops) {
      if (!stop.position) continue;
      const posKey = `${stop.position[0]},${stop.position[1]}`;
      if (resolvedPositionsRef.current.has(posKey)) continue;
      if (!isUnresolvedAddress(stop)) continue;
      resolvedPositionsRef.current.add(posKey);

      reverseGeocode(stop.position, controller.signal)
        .then((label) => {
          if (cancelled || !label) return;
          onStopsChange(
            stops.map((s) => (s.id === stop.id ? { ...s, address: label } : s)),
          );
        })
        .catch(() => {
          // Aborted or network error: keep the coordinate label.
        });
    }

    return () => {
      cancelled = true;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [positionsKey]);

  const setState = (stopId: string, state: GeocodingState) =>
    setGeocodingStates((prev) => ({ ...prev, [stopId]: state }));

  const handleAddressInput = useCallback(
    (stopId: string, value: string) => {
      // Editing the address invalidates the previous geocoding result.
      onStopsChange(
        stops.map((s) =>
          s.id === stopId ? { ...s, address: value, position: null } : s,
        ),
      );

      if (timeoutRef.current !== null) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      abortRef.current?.abort();

      if (value.trim().length < GEOCODING_MIN_QUERY_LENGTH) {
        setState(stopId, { query: value, suggestions: [], loading: false });
        return;
      }

      setGeocodingStates((prev) => ({
        ...prev,
        [stopId]: {
          query: value,
          suggestions: prev[stopId]?.suggestions ?? [],
          loading: true,
        },
      }));

      timeoutRef.current = setTimeout(async () => {
        timeoutRef.current = null;
        const controller = new AbortController();
        abortRef.current = controller;
        try {
          const results = await searchAddress(value, controller.signal);
          setState(stopId, {
            query: value,
            suggestions: results.map((r) => ({
              label: r.label,
              position: r.position,
            })),
            loading: false,
          });
        } catch (err) {
          if (err instanceof DOMException && err.name === "AbortError") return;
          // Network error: just no suggestions.
          setState(stopId, { query: value, suggestions: [], loading: false });
        }
      }, GEOCODING_DEBOUNCE_MS);
    },
    [stops, onStopsChange],
  );

  const handleSuggestionSelect = useCallback(
    (stopId: string, suggestion: GeocodeSuggestionDisplay) => {
      onStopsChange(
        stops.map((s) =>
          s.id === stopId
            ? { ...s, address: suggestion.label, position: suggestion.position }
            : s,
        ),
      );
      setState(stopId, {
        query: suggestion.label,
        suggestions: [],
        loading: false,
      });
    },
    [stops, onStopsChange],
  );

  const handleCloseSuggestions = useCallback((stopId: string) => {
    setGeocodingStates((prev) => ({
      ...prev,
      [stopId]: {
        query: prev[stopId]?.query ?? "",
        suggestions: [],
        loading: false,
      },
    }));
  }, []);

  return {
    geocodingStates,
    handleAddressInput,
    handleSuggestionSelect,
    handleCloseSuggestions,
  };
}

export type Geocoding = ReturnType<typeof useGeocoding>;
