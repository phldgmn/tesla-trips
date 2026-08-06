import { useState, useEffect, useRef, useCallback } from "react";

import type {
  Stop,
  VehicleProfileInput,
  TripRequestPayload,
} from "../types/trip-request";
import {
  buildTripRequestPayload,
  createEmptyStop,
  validateStops,
  TripRequestBuildError,
} from "../types/trip-request";
import {
  VEHICLE_PROFILE_PRESETS,
  DEFAULT_VEHICLE_PROFILE_PRESET_ID,
} from "../data/vehicleProfiles";
import { ChargingStationPicker } from "./ChargingStationPicker";
import { searchAddress, reverseGeocode } from "../api/geocoding";
import {
  combineDateTimeToIso,
  splitIsoToDateTime,
} from "../utils/datetime-utils";

// ============================================================================
// Types
// ============================================================================

export interface TripPlannerFormProps {
  stops: Stop[];
  onStopsChange: (stops: Stop[]) => void;
  pickingStopId: string | null;
  onRequestPick: (stopId: string | null) => void;
  onSubmit: (payload: TripRequestPayload) => void;
  isSubmitting: boolean;
  submitError?: string | null;
}

export interface GeocodingState {
  /** Der Text, FÜR DEN aktuell Suchergebnisse angezeigt werden (leer = geschlossen). */
  query: string;
  /** Aktuelles Dropdown-Ergebnis. */
  suggestions: GeocodeSuggestionDisplay[];
  /** Lade-Indikator. */
  loading: boolean;
}

export interface GeocodeSuggestionDisplay {
  label: string;
  position: [number, number];
}

// ============================================================================
// Pure Helper Functions (exported for unit testing)
// ============================================================================

/** Erzeugt den Rollen-Namen eines Stopps anhand seiner Position im Array. */
export function getStopRole(stops: Stop[], index: number): string {
  if (stops.length === 0) return "";
  if (index === 0) return "Start";
  if (index === stops.length - 1) return "Ziel";
  return `Zwischenstopp ${index}`;
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
  zielSoc: number;
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
    typeof args.zielSoc !== "number" ||
    isNaN(args.zielSoc) ||
    args.zielSoc < 0 ||
    args.zielSoc > 100
  ) {
    errors.push("Ziel-SoC muss zwischen 0 und 100 % liegen.");
  }

  return errors;
}

// ============================================================================
// Component
// ============================================================================

const GEOCODING_DEBOUNCE_MS = 400;
const GEOCODING_MIN_QUERY_LENGTH = 3;

export function TripPlannerForm({
  stops,
  onStopsChange,
  pickingStopId,
  onRequestPick,
  onSubmit,
  isSubmitting,
  submitError,
}: TripPlannerFormProps) {
  // --- Local State (Fahrzeug, SoC, Picker) ---
  const [vehicleProfile, setVehicleProfile] = useState<VehicleProfileInput>(
    () =>
      VEHICLE_PROFILE_PRESETS.find(
        (p) => p.id === DEFAULT_VEHICLE_PROFILE_PRESET_ID,
      )?.profile ?? VEHICLE_PROFILE_PRESETS[0].profile,
  );
  const [selectedPresetId, setSelectedPresetId] = useState<string | null>(
    DEFAULT_VEHICLE_PROFILE_PRESET_ID,
  );
  const [showAdvancedVehicle, setShowAdvancedVehicle] = useState(false);
  const [startSoc, setStartSoc] = useState(80);
  const [zielSoc, setZielSoc] = useState(20);

  // --- Geocoding State (per stop) ---
  const [geocodingStates, setGeocodingStates] = useState<
    Record<string, GeocodingState>
  >({});

  // --- Charging Station Picker State ---
  const [pickerTargetId, setPickerTargetId] = useState<string | null>(null);

  // AbortController-Ref für laufende Geocoding-Requests
  const geocodingAbortRef = useRef<AbortController | null>(null);

  // Reverse-Geocoding-Guard: speichert "{lat},{lon}" → true, sobald einmal aufgelöst
  const resolvedPositionsRef = useRef<Set<string>>(new Set());

  // --- Reverse-Geocoding-Effect ---
  // Sobald sich die position eines Stopps ändert und die Adresse noch nicht
  // menschenlesbar ist, lösen wir das rückwärts auf.
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    stops.map(async (stop) => {
      if (!stop.position) return;
      const posKey = `${stop.position[0]},${stop.position[1]}`;
      if (resolvedPositionsRef.current.has(posKey)) return;
      if (!isUnresolvedAddress(stop)) return;

      // Markieren, damit wir nicht erneut versuchen
      resolvedPositionsRef.current.add(posKey);

      try {
        const label = await reverseGeocode(stop.position, controller.signal);
        if (cancelled) return;
        if (label) {
          onStopsChange(
            stops.map((s) => (s.id === stop.id ? { ...s, address: label } : s)),
          );
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          // abgebrochen, ignorieren
        }
      }
    });

    // Da die Closure über stops und onStopsChange stale werden kann,
    // lassen wir dieses Effect nur feuern, wenn sich die Positions-Keys
    // tatsächlich ändern – siehe dependency: serialisierte Positionsliste.
    return () => {
      cancelled = true;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    stops
      .map((s) => (s.position ? `${s.position[0]},${s.position[1]}` : "null"))
      .join("|"),
  ]);

  // --- Geocoding ---

  const handleAddressInput = useCallback(
    (stopId: string, value: string) => {
      // Adresse im Stop setzen und Position zurücksetzen (Bearbeitung invalidiert Geocoding)
      onStopsChange(
        stops.map((s) =>
          s.id === stopId ? { ...s, address: value, position: null } : s,
        ),
      );

      // Vorherige Geocoding-Request abbrechen
      if (geocodingAbortRef.current) {
        geocodingAbortRef.current.abort();
      }

      if (value.trim().length < GEOCODING_MIN_QUERY_LENGTH) {
        setGeocodingStates((prev) => ({
          ...prev,
          [stopId]: { query: value, suggestions: [], loading: false },
        }));
        return;
      }

      // Status setzen: Ladezustand anzeigen
      setGeocodingStates((prev) => ({
        ...prev,
        [stopId]: {
          query: value,
          suggestions: prev[stopId]?.suggestions ?? [],
          loading: true,
        },
      }));

      // Debounce
      const timeoutId = setTimeout(async () => {
        const controller = new AbortController();
        geocodingAbortRef.current = controller;

        try {
          const results = await searchAddress(value, controller.signal);
          setGeocodingStates((prev) => ({
            ...prev,
            [stopId]: {
              query: value,
              suggestions: results.map((r) => ({
                label: r.label,
                position: r.position,
              })),
              loading: false,
            },
          }));
        } catch (err) {
          if (err instanceof DOMException && err.name === "AbortError") return;
          // Netzwerkfehler: einfach keine Vorschläge
          setGeocodingStates((prev) => ({
            ...prev,
            [stopId]: { query: value, suggestions: [], loading: false },
          }));
        }
      }, GEOCODING_DEBOUNCE_MS);

      // Cleanup: Timeout und AbortController
      return () => {
        clearTimeout(timeoutId);
        if (geocodingAbortRef.current) {
          geocodingAbortRef.current.abort();
        }
      };
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
      setGeocodingStates((prev) => ({
        ...prev,
        [stopId]: { query: suggestion.label, suggestions: [], loading: false },
      }));
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

  // --- Handlers ---

  const handleAddStop = () => {
    // Neuen Stopp VOR dem letzten Element einfügen (Ziel bleibt Ziel)
    if (stops.length < 1) return;
    const newStop = createEmptyStop();
    const insertAt = stops.length - 1;
    onStopsChange([
      ...stops.slice(0, insertAt),
      newStop,
      stops[stops.length - 1],
    ]);
  };

  const handleRemoveStop = (stopId: string) => {
    if (stops.length <= 2) return; // Mindestens Start + Ziel
    onStopsChange(stops.filter((s) => s.id !== stopId));
  };

  const handleMoveUp = (index: number) => {
    if (index <= 0) return;
    onStopsChange(swapStops(stops, index, index - 1));
  };

  const handleMoveDown = (index: number) => {
    if (index >= stops.length - 1) return;
    onStopsChange(swapStops(stops, index, index + 1));
  };

  const handleLeaveAtChange = (stopId: string, date: string, time: string) => {
    const iso = combineDateTimeToIso(date, time);
    onStopsChange(
      stops.map((s) => (s.id === stopId ? { ...s, leaveAt: iso } : s)),
    );
  };

  const handleLeaveAtClear = (stopId: string) => {
    onStopsChange(
      stops.map((s) => {
        if (s.id !== stopId) return s;
        const rest = { ...s };
        delete rest.leaveAt;
        return rest;
      }),
    );
  };

  const handlePresetChange = (presetId: string) => {
    const preset = VEHICLE_PROFILE_PRESETS.find((p) => p.id === presetId);
    if (preset) {
      setVehicleProfile(preset.profile);
      setSelectedPresetId(presetId);
    }
  };

  const handleVehicleFieldChange = <K extends keyof VehicleProfileInput>(
    field: K,
    value: VehicleProfileInput[K],
  ) => {
    setVehicleProfile((prev) => ({ ...prev, [field]: value }));
    setSelectedPresetId(null);
  };

  const handleChargingStationSelect = (station: {
    position: [number, number];
    label: string;
  }) => {
    if (pickerTargetId) {
      onStopsChange(
        stops.map((s) =>
          s.id === pickerTargetId
            ? { ...s, address: station.label, position: station.position }
            : s,
        ),
      );
      setPickerTargetId(null);
    }
  };

  const handleChargingStationCancel = () => {
    setPickerTargetId(null);
  };

  const handleSubmit = () => {
    const errors = validateForm({ stops, startSoc, zielSoc });

    if (errors.length > 0) {
      return;
    }

    try {
      const payload = buildTripRequestPayload({
        stops,
        fahrzeugprofil: vehicleProfile,
        startSocPct: startSoc,
        zielSocPct: zielSoc,
        praeferenzen: {},
      });
      onSubmit(payload);
    } catch (error) {
      if (error instanceof TripRequestBuildError) {
        // Wird als submitError an die Eltern-Komponente übergeben
        // Hier fangen wir es als zusätzliche Inline-Fehlermeldung
        // (die Props-Integration kann das auch über submitError handhaben)
      }
    }
  };

  // --- Validation ---

  const validationErrors = validateForm({ stops, startSoc, zielSoc });

  // --- Render Helpers ---

  const renderVehicleAdvanced = () => {
    const v = vehicleProfile;
    return (
      <details
        open={showAdvancedVehicle}
        onToggle={() => setShowAdvancedVehicle(!showAdvancedVehicle)}
        style={{ marginTop: "1rem" }}
      >
        <summary style={{ cursor: "pointer", fontWeight: 600 }}>
          Erweitert
        </summary>
        <div style={{ marginTop: "0.75rem", display: "grid", gap: "0.75rem" }}>
          <div>
            <label style={{ display: "block", marginBottom: "0.25rem" }}>
              Masse (kg)
            </label>
            <input
              type="number"
              step="1"
              value={v.masse_kg}
              onChange={(e) =>
                handleVehicleFieldChange("masse_kg", parseFloat(e.target.value))
              }
              style={{ width: "100%", padding: "0.4rem" }}
            />
          </div>
          <div>
            <label style={{ display: "block", marginBottom: "0.25rem" }}>
              cW-Wert
            </label>
            <input
              type="number"
              step="0.001"
              value={v.cw_wert}
              onChange={(e) =>
                handleVehicleFieldChange("cw_wert", parseFloat(e.target.value))
              }
              style={{ width: "100%", padding: "0.4rem" }}
            />
          </div>
          <div>
            <label style={{ display: "block", marginBottom: "0.25rem" }}>
              Stirnfläche (m²)
            </label>
            <input
              type="number"
              step="0.01"
              value={v.stirnflaeche_m2}
              onChange={(e) =>
                handleVehicleFieldChange(
                  "stirnflaeche_m2",
                  parseFloat(e.target.value),
                )
              }
              style={{ width: "100%", padding: "0.4rem" }}
            />
          </div>
          <div>
            <label style={{ display: "block", marginBottom: "0.25rem" }}>
              Rollwiderstandsbeiwert
            </label>
            <input
              type="number"
              step="0.0001"
              value={v.rollwiderstandsbeiwert}
              onChange={(e) =>
                handleVehicleFieldChange(
                  "rollwiderstandsbeiwert",
                  parseFloat(e.target.value),
                )
              }
              style={{ width: "100%", padding: "0.4rem" }}
            />
          </div>
          <div>
            <label style={{ display: "block", marginBottom: "0.25rem" }}>
              Batteriekapazität (kWh)
            </label>
            <input
              type="number"
              step="0.1"
              value={v.batteriekapazitaet_kwh}
              onChange={(e) =>
                handleVehicleFieldChange(
                  "batteriekapazitaet_kwh",
                  parseFloat(e.target.value),
                )
              }
              style={{ width: "100%", padding: "0.4rem" }}
            />
          </div>
          <div>
            <label style={{ display: "block", marginBottom: "0.25rem" }}>
              Nebenverbraucher Baseline (kW)
            </label>
            <input
              type="number"
              step="0.01"
              value={v.nebenverbraucher_baseline_kw}
              onChange={(e) =>
                handleVehicleFieldChange(
                  "nebenverbraucher_baseline_kw",
                  parseFloat(e.target.value),
                )
              }
              style={{ width: "100%", padding: "0.4rem" }}
            />
          </div>
          <div>
            <label style={{ display: "block", marginBottom: "0.25rem" }}>
              Reifentyp
            </label>
            <select
              value={v.reifentyp}
              onChange={(e) =>
                handleVehicleFieldChange(
                  "reifentyp",
                  e.target.value as VehicleProfileInput["reifentyp"],
                )
              }
              style={{ width: "100%", padding: "0.5rem" }}
            >
              <option value="standard">Standard</option>
              <option value="winter">Winter</option>
              <option value="low_rolling_resistance">
                Low Rolling Resistance
              </option>
              <option value="performance">Performance</option>
            </select>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <input
              type="checkbox"
              checked={v.dachbox}
              onChange={(e) =>
                handleVehicleFieldChange("dachbox", e.target.checked)
              }
              id="dachbox-checkbox"
            />
            <label htmlFor="dachbox-checkbox">Dachbox</label>
          </div>
        </div>
      </details>
    );
  };

  // --- Main Render ---
  return (
    <div
      style={{
        width: "380px",
        maxHeight: "100vh",
        overflowY: "auto",
        padding: "1.5rem",
        background: "#fafafa",
        borderRight: "1px solid #e5e7eb",
        fontFamily: "system-ui, -apple-system, sans-serif",
        fontSize: "0.9rem",
        lineHeight: "1.5",
      }}
    >
      {/* Submit Error Banner */}
      {submitError && (
        <div
          style={{
            marginBottom: "1rem",
            padding: "0.75rem",
            background: "#fef2f2",
            border: "1px solid #fecaca",
            borderRadius: "4px",
            color: "#991b1b",
            fontSize: "0.85rem",
          }}
        >
          {submitError}
        </div>
      )}

      {/* Validation Errors */}
      {validationErrors.length > 0 && (
        <div
          style={{
            marginBottom: "1rem",
            padding: "0.75rem",
            background: "#fffbeb",
            border: "1px solid #fde68a",
            borderRadius: "4px",
            color: "#92400e",
            fontSize: "0.85rem",
          }}
        >
          <ul style={{ margin: "0 0 0 1rem", padding: 0 }}>
            {validationErrors.map((err, i) => (
              <li key={i}>{err}</li>
            ))}
          </ul>
        </div>
      )}

      {/* 1. Route (dynamische Stopp-Liste) */}
      <fieldset
        style={{
          marginBottom: "1.5rem",
          border: "1px solid #e5e7eb",
          borderRadius: "6px",
          padding: "1rem",
        }}
      >
        <legend style={{ fontWeight: 600, padding: "0 0.5rem" }}>Route</legend>

        {stops.map((stop, idx) => {
          const role = getStopRole(stops, idx);
          const isLast = idx === stops.length - 1;
          const geoState = geocodingStates[stop.id];
          const showSuggestions = geoState && geoState.suggestions.length > 0;

          // leaveAt aufsplitten für date/time-Inputs
          const leaveAtParts = stop.leaveAt
            ? splitIsoToDateTime(stop.leaveAt)
            : null;
          const showLeaveAt = !isLast;

          return (
            <div
              key={stop.id}
              style={{
                border: "1px solid #e5e7eb",
                borderRadius: "6px",
                padding: "0.75rem",
                marginBottom: "0.75rem",
                background: "white",
                display: "flex",
                flexDirection: "column",
                gap: "0.5rem",
              }}
            >
              {/* Header: Role Badge + Move/Remove */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: "0.5rem",
                }}
              >
                <span
                  style={{
                    padding: "0.15rem 0.5rem",
                    borderRadius: "999px",
                    fontSize: "0.7rem",
                    fontWeight: 600,
                    background:
                      idx === 0 ? "#e0e7ff" : isLast ? "#fef3c7" : "#f3f4f6",
                    color:
                      idx === 0 ? "#3730a3" : isLast ? "#92400e" : "#4b5563",
                  }}
                >
                  {role}
                </span>
                <div style={{ display: "flex", gap: "0.25rem" }}>
                  <button
                    type="button"
                    onClick={() => handleMoveUp(idx)}
                    disabled={idx === 0 || isSubmitting}
                    style={{
                      padding: "0.2rem 0.5rem",
                      fontSize: "0.75rem",
                      background: "#f3f4f6",
                      border: "1px solid #d1d5db",
                      borderRadius: "4px",
                      cursor:
                        idx === 0 || isSubmitting ? "not-allowed" : "pointer",
                      opacity: idx === 0 || isSubmitting ? 0.5 : 1,
                    }}
                    aria-label="Nach oben verschieben"
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    onClick={() => handleMoveDown(idx)}
                    disabled={isLast || isSubmitting}
                    style={{
                      padding: "0.2rem 0.5rem",
                      fontSize: "0.75rem",
                      background: "#f3f4f6",
                      border: "1px solid #d1d5db",
                      borderRadius: "4px",
                      cursor:
                        isLast || isSubmitting ? "not-allowed" : "pointer",
                      opacity: isLast || isSubmitting ? 0.5 : 1,
                    }}
                    aria-label="Nach unten verschieben"
                  >
                    ↓
                  </button>
                  <button
                    type="button"
                    onClick={() => handleRemoveStop(stop.id)}
                    disabled={stops.length <= 2 || isSubmitting}
                    style={{
                      padding: "0.2rem 0.5rem",
                      fontSize: "0.75rem",
                      background: "#fef2f2",
                      border: "1px solid #fecaca",
                      borderRadius: "4px",
                      color: "#991b1b",
                      cursor:
                        stops.length <= 2 || isSubmitting
                          ? "not-allowed"
                          : "pointer",
                      opacity: stops.length <= 2 || isSubmitting ? 0.5 : 1,
                    }}
                    aria-label="Entfernen"
                  >
                    ✕
                  </button>
                </div>
              </div>

              {/* Address Input + Geocoding */}
              <div style={{ position: "relative" }}>
                <input
                  type="text"
                  placeholder="Adresse eingeben…"
                  value={stop.address}
                  onChange={(e) => handleAddressInput(stop.id, e.target.value)}
                  onBlur={() => {
                    // Beim Verlassen die Vorschläge erst nach kurzer Verzögerung schließen,
                    // damit der Klick auf einen Vorschlag noch registriert wird
                    setTimeout(() => handleCloseSuggestions(stop.id), 200);
                  }}
                  onFocus={(e) => {
                    // Bei Fokus und genug Text: Suche neu starten
                    if (
                      e.target.value.trim().length >= GEOCODING_MIN_QUERY_LENGTH
                    ) {
                      handleAddressInput(stop.id, e.target.value);
                    }
                  }}
                  disabled={isSubmitting}
                  style={{
                    width: "100%",
                    padding: "0.5rem",
                    border: "1px solid #d1d5db",
                    borderRadius: "4px",
                    fontSize: "0.85rem",
                    boxSizing: "border-box",
                  }}
                />
                {geoState?.loading && (
                  <span
                    style={{
                      position: "absolute",
                      right: "0.5rem",
                      top: "0.6rem",
                      fontSize: "0.75rem",
                      color: "#6b7280",
                    }}
                  >
                    Suche…
                  </span>
                )}
                {showSuggestions && (
                  <ul
                    style={{
                      position: "absolute",
                      top: "100%",
                      left: 0,
                      right: 0,
                      zIndex: 10,
                      background: "white",
                      border: "1px solid #d1d5db",
                      borderTop: "none",
                      borderRadius: "0 0 4px 4px",
                      listStyle: "none",
                      margin: 0,
                      padding: 0,
                      maxHeight: "200px",
                      overflowY: "auto",
                      boxShadow: "0 2px 6px rgba(0,0,0,0.1)",
                    }}
                  >
                    {geoState.suggestions.map((sugg, sIdx) => (
                      <li
                        key={sIdx}
                        onMouseDown={(e) => {
                          e.preventDefault();
                          handleSuggestionSelect(stop.id, sugg);
                        }}
                        style={{
                          padding: "0.5rem",
                          cursor: "pointer",
                          fontSize: "0.85rem",
                          borderBottom:
                            sIdx < geoState.suggestions.length - 1
                              ? "1px solid #f3f4f6"
                              : "none",
                        }}
                        onMouseEnter={(e) => {
                          e.currentTarget.style.background = "#f3f4f6";
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.background = "white";
                        }}
                      >
                        {sugg.label}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {/* Map Pick Button */}
              <button
                type="button"
                onClick={() =>
                  onRequestPick(pickingStopId === stop.id ? null : stop.id)
                }
                disabled={isSubmitting}
                style={{
                  width: "100%",
                  padding: "0.4rem",
                  background: pickingStopId === stop.id ? "#2563eb" : "#3b82f6",
                  color: "white",
                  border: "none",
                  borderRadius: "4px",
                  cursor: isSubmitting ? "not-allowed" : "pointer",
                  opacity: isSubmitting ? 0.6 : 1,
                }}
              >
                {pickingStopId === stop.id
                  ? "Abbrechen (Klicken Sie auf die Karte…)"
                  : "Auf Karte wählen"}
              </button>

              {pickingStopId === stop.id && (
                <p
                  style={{
                    margin: "0.4rem 0 0",
                    fontSize: "0.8rem",
                    color: "#2563eb",
                  }}
                >
                  Klicken Sie auf die Karte, um den Punkt zu platzieren.
                </p>
              )}

              {/* Ladestation-Picker Button */}
              <button
                type="button"
                onClick={() =>
                  setPickerTargetId(pickerTargetId === stop.id ? null : stop.id)
                }
                disabled={isSubmitting}
                style={{
                  width: "100%",
                  padding: "0.4rem",
                  background: "#dcfce7",
                  border: "1px solid #86efac",
                  borderRadius: "4px",
                  color: "#166534",
                  cursor: isSubmitting ? "not-allowed" : "pointer",
                  opacity: isSubmitting ? 0.6 : 1,
                  fontSize: "0.85rem",
                }}
              >
                {pickerTargetId === stop.id
                  ? "Abbrechen"
                  : "Ladestation statt Adresse wählen"}
              </button>

              {/* Charging Station Picker (inline, nur für dieses Ziel) */}
              {pickerTargetId === stop.id && (
                <ChargingStationPicker
                  onSelect={handleChargingStationSelect}
                  onCancel={handleChargingStationCancel}
                />
              )}

              {/* Abfahrt hier (nur für nicht-letzte Stopps) */}
              {showLeaveAt && (
                <div>
                  <label
                    style={{
                      display: "block",
                      fontSize: "0.8rem",
                      marginBottom: "0.25rem",
                      fontWeight: 500,
                    }}
                  >
                    Abfahrt hier
                  </label>
                  <div style={{ display: "flex", gap: "0.4rem" }}>
                    <input
                      type="date"
                      value={leaveAtParts?.date ?? ""}
                      onChange={(e) => {
                        const newTime = leaveAtParts?.time ?? "12:00";
                        if (e.target.value) {
                          handleLeaveAtChange(stop.id, e.target.value, newTime);
                        } else {
                          handleLeaveAtClear(stop.id);
                        }
                      }}
                      style={{
                        flex: 1,
                        padding: "0.4rem",
                        fontSize: "0.85rem",
                      }}
                    />
                    <input
                      type="time"
                      value={leaveAtParts?.time ?? ""}
                      onChange={(e) => {
                        const newDate = leaveAtParts?.date ?? "";
                        if (e.target.value) {
                          handleLeaveAtChange(stop.id, newDate, e.target.value);
                        } else {
                          handleLeaveAtClear(stop.id);
                        }
                      }}
                      style={{
                        flex: 1,
                        padding: "0.4rem",
                        fontSize: "0.85rem",
                      }}
                    />
                  </div>
                  {stop.leaveAt && (
                    <button
                      type="button"
                      onClick={() => handleLeaveAtClear(stop.id)}
                      style={{
                        marginTop: "0.25rem",
                        padding: "0.2rem 0.4rem",
                        fontSize: "0.75rem",
                        background: "none",
                        border: "none",
                        color: "#991b1b",
                        cursor: "pointer",
                        textDecoration: "underline",
                      }}
                    >
                      Abfahrtszeit löschen
                    </button>
                  )}
                </div>
              )}
            </div>
          );
        })}

        {/* Add Stop Button */}
        <button
          type="button"
          onClick={handleAddStop}
          disabled={isSubmitting || stops.length < 1}
          style={{
            width: "100%",
            padding: "0.5rem",
            background: "#f3f4f6",
            border: "1px solid #d1d5db",
            borderRadius: "4px",
            cursor:
              isSubmitting || stops.length < 1 ? "not-allowed" : "pointer",
            opacity: isSubmitting || stops.length < 1 ? 0.6 : 1,
          }}
        >
          + Stopp hinzufügen
        </button>
      </fieldset>

      {/* 2. Fahrzeug */}
      <fieldset
        style={{
          marginBottom: "1.5rem",
          border: "1px solid #e5e7eb",
          borderRadius: "6px",
          padding: "1rem",
        }}
      >
        <legend style={{ fontWeight: 600, padding: "0 0.5rem" }}>
          Fahrzeug
        </legend>
        <select
          value={selectedPresetId ?? ""}
          onChange={(e) => handlePresetChange(e.target.value)}
          style={{ width: "100%", padding: "0.5rem", marginBottom: "0.5rem" }}
        >
          {VEHICLE_PROFILE_PRESETS.map((preset) => (
            <option key={preset.id} value={preset.id}>
              {preset.label}
            </option>
          ))}
          <option value="">— Benutzerdefiniert —</option>
        </select>

        {renderVehicleAdvanced()}
      </fieldset>

      {/* 3. Ladestand */}
      <fieldset
        style={{
          marginBottom: "1.5rem",
          border: "1px solid #e5e7eb",
          borderRadius: "6px",
          padding: "1rem",
        }}
      >
        <legend style={{ fontWeight: 600, padding: "0 0.5rem" }}>
          Ladestand
        </legend>
        <div style={{ display: "grid", gap: "0.75rem" }}>
          <div>
            <label style={{ display: "block", marginBottom: "0.25rem" }}>
              Start-SoC (%)
            </label>
            <input
              type="number"
              min="0"
              max="100"
              step="1"
              value={startSoc}
              onChange={(e) => setStartSoc(parseInt(e.target.value, 10) || 0)}
              style={{ width: "100%", padding: "0.4rem" }}
            />
          </div>
          <div>
            <label style={{ display: "block", marginBottom: "0.25rem" }}>
              Ziel-SoC (%)
            </label>
            <input
              type="number"
              min="0"
              max="100"
              step="1"
              value={zielSoc}
              onChange={(e) => setZielSoc(parseInt(e.target.value, 10) || 0)}
              style={{ width: "100%", padding: "0.4rem" }}
            />
          </div>
        </div>
      </fieldset>

      {/* 4. Submit */}
      <button
        type="button"
        onClick={handleSubmit}
        disabled={isSubmitting || validationErrors.length > 0}
        style={{
          width: "100%",
          padding: "0.75rem",
          fontSize: "1rem",
          fontWeight: 600,
          background:
            validationErrors.length > 0 || isSubmitting ? "#9ca3af" : "#2563eb",
          color: "white",
          border: "none",
          borderRadius: "6px",
          cursor:
            validationErrors.length > 0 || isSubmitting
              ? "not-allowed"
              : "pointer",
        }}
      >
        {isSubmitting ? "Berechne…" : "Route berechnen"}
      </button>
    </div>
  );
}

export default TripPlannerForm;
