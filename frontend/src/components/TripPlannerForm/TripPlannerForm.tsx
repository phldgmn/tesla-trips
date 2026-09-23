import { useState, useEffect, useRef, useCallback } from "react";
import {
  usePersistentState,
  migrateConsiderWeather,
} from "../../utils/persistent-state";
import { Modal } from "../Modal";
import { Popover } from "../Popover";

import type {
  Stop,
  VehicleProfileInput,
  TripRequestPayload,
  FerryExclusion,
  FerryTimeWindow,
  ChargingDurationSpecification,
  WeatherDetailLevel,
  HighwayPreferenceLevel,
} from "../../types/trip-request";
import {
  buildTripRequestPayload,
  createEmptyStop,
  TripRequestBuildError,
} from "../../types/trip-request";
import {
  VEHICLE_PROFILE_PRESETS,
  DEFAULT_VEHICLE_PROFILE_PRESET_ID,
} from "../../data/vehicleProfiles";
import { ChargingStationPicker } from "../ChargingStationPicker";
import { searchAddress, reverseGeocode } from "../../api/geocoding";
import {
  splitIsoToDateTime,
  formatDayMonth,
  isDayChange,
  combineDateTimeToIso,
} from "../../utils/datetime-utils";
import type { ChargingStop, FerrySegment } from "../../types";
import { buildRouteEntries } from "@/utils/route-entries";
import type { SimulationFrame } from "../../types";
import {
  Zap,
  Ship,
  Crosshair,
  BatteryCharging,
  Battery,
  CloudSun,
  Construction,
  Route,
  SignalZero,
  SignalLow,
  SignalMedium,
  SignalHigh,
  X,
} from "lucide-react";

import {
  getStopRole,
  getStopTimelineIcon,
  differsAsTime,
  validateForm,
  swapStops,
  isUnresolvedAddress,
  formatChargingStationName,
} from "./form-helpers";
import {
  sameFerryExclusion as sameFerryExclusion,
  toggleFerryExclusion,
  setFerryTimeWindowFor,
  setChargingDurationPresetFor,
  ferryKey,
} from "./ferry-helpers";
import {
  TimelineRow,
  TimeBadge,
  DaySeparator,
  DriveSegmentRow,
} from "./timeline-rows";

export interface TripPlannerFormProps {
  stops: Stop[];
  onStopsChange: (stops: Stop[]) => void;
  pickingStopId: string | null;
  onRequestPick: (stopId: string | null) => void;
  onSubmit: (payload: TripRequestPayload) => void;
  isSubmitting: boolean;
  submitError?: string | null;
  /** Fährverbindungen, die in der zuletzt berechneten Route erkannt wurden
   *  (aus `TripSimulationResult.detected_ferries`), zur Anzeige als
   *  "vermeiden"-Checkboxen. `undefined`/leer, solange noch keine Route
   *  berechnet wurde. */
  detectedFerries?: FerrySegment[];
  /** Ladehalte des zuletzt berechneten Ladeplans (aus
   *  `TripSimulationResult.charging_stops`), zur Anzeige mit editierbarer
   *  Ladedauer-Vorgabe. `undefined`/leer, solange noch keine Route berechnet
   *  wurde. */
  chargingStops?: ChargingStop[];
  /** Simulationsframes, zur zeitlichen Einordnung von Stops, Ladehalten und
   *  Fähren (für `buildRouteEntries`). `undefined`/leer, solange noch keine
   *  Route berechnet wurde. */
  frames?: SimulationFrame[];
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
  detectedFerries: detectedFerries,
  chargingStops,
  frames,
}: TripPlannerFormProps) {
  // --- Local State (Fahrzeug, SoC, Picker) ---
  const [vehicleProfile, setVehicleProfile] =
    usePersistentState<VehicleProfileInput>(
      "vehicle-profile",
      () =>
        VEHICLE_PROFILE_PRESETS.find(
          (p) => p.id === DEFAULT_VEHICLE_PROFILE_PRESET_ID,
        )?.profile ?? VEHICLE_PROFILE_PRESETS[0].profile,
    );
  const [selectedPresetId, setSelectedPresetId] = usePersistentState<
    string | null
  >("vehicle-profile-preset-id", DEFAULT_VEHICLE_PROFILE_PRESET_ID);
  const [showAdvancedVehicle, setShowAdvancedVehicle] = usePersistentState(
    "show-advanced-vehicle",
    false,
  );
  const [startSoc, setStartSoc] = usePersistentState("start-soc", 80);
  const [minArrivalSocPct, setMinArrivalSocPct] = usePersistentState(
    "min-arrival-soc",
    5,
  );
  const [targetSoc, setTargetSoc] = usePersistentState("target-soc", 20);
  const [minChargeTimeMin, setMinChargeTimeMin] = usePersistentState(
    "min-charging-time-s",
    10,
  );
  const [maxChargeSocPct, setMaxChargeSocPct] = usePersistentState(
    "max-charge-soc",
    100,
  );
  const [avoidAllFerries, setAvoidAllFerries] = usePersistentState(
    "avoid-all-ferries",
    false,
  );
  const [autobahnPraeferenz, setHighwayPreference] =
    usePersistentState<HighwayPreferenceLevel>("highway-preference", "off");
  const [weatherDetailLevel, setWeatherDetailLevel] =
    usePersistentState<WeatherDetailLevel>("weather-detail-level", () =>
      migrateConsiderWeather(),
    );
  const [considerRoadworks, setConsiderRoadworks] = usePersistentState(
    "consider-construction-sites",
    true,
  );
  const [avoidedFerries, setAvoidedFerries] = usePersistentState<
    FerryExclusion[]
  >("avoided-ferries", []);
  const [ferryTimeWindows, setFerryTimeWindows] = usePersistentState<
    FerryTimeWindow[]
  >("faehr-zeitfenster", []);
  const [chargingDurationPresets, setChargingDurationPresets] =
    usePersistentState<ChargingDurationSpecification[]>(
      "charging-duration-specifications",
      [],
    );
  // Haelt den Zwischenstand der Fährfahrplan-Eingabe (Datum + Zeit je Feld
  // separat eingegeben), solange noch nicht beide Felder (Abfahrt UND
  // Ankunft) vollständig ausgefüllt sind - `ferryTimeWindow` speichert
  // absichtlich NUR vollständige Zeitfenster (siehe `setFerryTimeWindowFor`),
  // ohne diesen separaten Entwurfs-State würde ein kontrolliertes Eingabefeld
  // nach jedem Tastendruck auf "" zurückspringen, solange das jeweils andere
  // Feld noch leer ist.
  const [ferryTimeWindowDraft, setFerryTimeWindowDraft] = useState<
    Record<string, { departure: string; arrival: string }>
  >({});

  // --- "Ignorierte Fähren"- und "Fahrzeug & Ladestand"-Modal-Sichtbarkeit
  //     (bewusst nicht persistent - reiner UI-Zustand) ---
  const [isIgnoredFerriesModalOpen, setIsIgnoredFerriesModalOpen] =
    useState(false);
  const [isVehicleModalOpen, setIsVehicleModalOpen] = useState(false);
  // Stop-ID, für die aktuell der SoC-Inline-Editor (Start-/Ziel-SoC-Button
  // in der Kopfzeile der Start-/Ziel-Karte) geöffnet ist, oder `null`.
  const [socEditingStopId, setSocEditingStopId] = useState<string | null>(null);

  // --- Geocoding State (per stop) ---
  const [geocodingStates, setGeocodingStates] = useState<
    Record<string, GeocodingState>
  >({});

  // --- Charging Station Picker State ---
  const [pickerTargetId, setPickerTargetId] = useState<string | null>(null);

  // AbortController-Ref für laufende Geocoding-Requests
  const geocodingAbortRef = useRef<AbortController | null>(null);

  // Timeout-Ref für das Debounce des Geocoding-Requests. MUSS bei jedem
  // Tastenanschlag den vorherigen, noch ausstehenden Timeout löschen -
  // andernfalls (siehe Bugfix unten) plant jeder Tastenanschlag einen
  // eigenen, unabhängigen 400ms-Timer, der garantiert feuert, egal wie
  // schnell weitergetippt wird: bei normaler Tippgeschwindigkeit löst das
  // EINEN Nominatim-Request PRO ZEICHEN aus statt nur einen nach Tippende -
  // verletzt die 1-req/s-Nutzungsrichtlinie und führt zu HTTP 429.
  const geocodingTimeoutRef = useRef<number | null>(null);

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

      // Vorherigen Debounce-Timer UND laufende Geocoding-Request abbrechen.
      // Ohne `clearTimeout` hier würde jeder Tastenanschlag einen eigenen,
      // unabhängigen Timer planen, der garantiert feuert (siehe Ref-
      // Deklaration oben) - EIN Request pro Zeichen statt einer pro
      // Tippende-Pause.
      if (geocodingTimeoutRef.current !== null) {
        clearTimeout(geocodingTimeoutRef.current);
        geocodingTimeoutRef.current = null;
      }
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
      geocodingTimeoutRef.current = setTimeout(async () => {
        geocodingTimeoutRef.current = null;
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

  const handleChargingPowerChange = (stopId: string, value: string) => {
    const parsed = value.trim() === "" ? undefined : Number(value);
    onStopsChange(
      stops.map((s) => {
        if (s.id !== stopId) return s;
        if (parsed === undefined || Number.isNaN(parsed)) {
          const rest = { ...s };
          delete rest.chargingPowerKw;
          return rest;
        }
        return { ...s, chargingPowerKw: parsed };
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

  const buildAndSubmit = (
    allFerries: boolean,
    avoided: FerryExclusion[],
    timeWindows: FerryTimeWindow[],
    chargingDurations: ChargingDurationSpecification[],
  ) => {
    const errors = validateForm({
      stops,
      startSoc,
      targetSoc: targetSoc,
      minArrivalSocPct: minArrivalSocPct,
      minChargingTimeMin: minChargeTimeMin,
      maxChargeSocPct: maxChargeSocPct,
    });

    if (errors.length > 0) {
      return;
    }

    try {
      const payload = buildTripRequestPayload({
        stops,
        vehicleProfile: vehicleProfile,
        startSocPct: startSoc,
        targetSocPct: targetSoc,
        minChargeDurationS: minChargeTimeMin * 60,
        minArrivalSocPct,
        maxChargeSocPct,
        preferences: {},
        avoidAllFerries: allFerries,
        highwayPreference: autobahnPraeferenz,
        avoidedFerries: avoided,
        ferryTimeWindows: timeWindows,
        chargingDurationSpecifications: chargingDurations,
        weatherDetailLevel: weatherDetailLevel,
        considerConstructionSites: considerRoadworks,
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

  const handleSubmit = () =>
    buildAndSubmit(
      avoidAllFerries,
      avoidedFerries,
      ferryTimeWindows,
      chargingDurationPresets,
    );

  // --- Validation ---

  const validationErrors = validateForm({
    stops,
    startSoc,
    targetSoc: targetSoc,
    minChargingTimeMin: minChargeTimeMin,
    minArrivalSocPct: minArrivalSocPct,
    maxChargeSocPct: maxChargeSocPct,
  });

  // --- Route-Liste: chronologisch sortierte Stopps, Ladehalte und
  //     (nicht ignorierte) Fähren, siehe utils/route-entries.ts ---
  const routeEntries = buildRouteEntries({
    stops,
    frames,
    chargingStops,
    detectedFerries,
    avoidedFerries: avoidedFerries,
  });

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
              value={v.massKg}
              onChange={(e) =>
                handleVehicleFieldChange("massKg", parseFloat(e.target.value))
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
              value={v.dragCoefficient}
              onChange={(e) =>
                handleVehicleFieldChange(
                  "dragCoefficient",
                  parseFloat(e.target.value),
                )
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
              value={v.frontalAreaM2}
              onChange={(e) =>
                handleVehicleFieldChange(
                  "frontalAreaM2",
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
              value={v.rollingResistanceCoefficient}
              onChange={(e) =>
                handleVehicleFieldChange(
                  "rollingResistanceCoefficient",
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
              value={v.batteryCapacityKwh}
              onChange={(e) =>
                handleVehicleFieldChange(
                  "batteryCapacityKwh",
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
              value={v.auxiliaryBaselineKw}
              onChange={(e) =>
                handleVehicleFieldChange(
                  "auxiliaryBaselineKw",
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
              value={v.tireType}
              onChange={(e) =>
                handleVehicleFieldChange(
                  "tireType",
                  e.target.value as VehicleProfileInput["tireType"],
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
              checked={v.roofBox}
              onChange={(e) =>
                handleVehicleFieldChange("roofBox", e.target.checked)
              }
              id="roofBox-checkbox"
            />
            <label htmlFor="roofBox-checkbox">Dachbox</label>
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

      {/* 1. Fahrzeug & Ladestand */}
      <button
        type="button"
        onClick={() => setIsVehicleModalOpen(true)}
        style={{
          width: "100%",
          padding: "0.75rem",
          marginBottom: "0.75rem",
          background: "white",
          border: "1px solid #e5e7eb",
          borderRadius: "6px",
          cursor: "pointer",
          textAlign: "left",
        }}
      >
        <div style={{ fontWeight: 600 }}>Fahrzeug & Ladestand</div>
        <div
          style={{ fontSize: "0.8rem", color: "#6b7280", marginTop: "0.2rem" }}
        >
          {VEHICLE_PROFILE_PRESETS.find((p) => p.id === selectedPresetId)
            ?.label ?? "Benutzerdefiniert"}
        </div>
      </button>

      <Modal
        open={isVehicleModalOpen}
        onClose={() => setIsVehicleModalOpen(false)}
        title="Fahrzeug & Ladestand"
      >
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

        <div style={{ display: "flex", gap: "0.75rem", marginTop: "1rem" }}>
          <div style={{ flex: 1 }}>
            <label style={{ display: "block", marginBottom: "0.25rem" }}>
              Min. SoC an Ladestationen (%)
            </label>
            <input
              type="number"
              min="0"
              max="100"
              step="1"
              value={minArrivalSocPct}
              onChange={(e) =>
                setMinArrivalSocPct(parseInt(e.target.value, 10) || 0)
              }
              style={{ width: "100%", padding: "0.4rem" }}
            />
            <small
              style={{
                display: "block",
                fontSize: "0.75rem",
                color: "#6b7280",
                marginTop: "0.25rem",
                lineHeight: "1.3",
              }}
            >
              Ermöglicht, den SoC an Ladestationen niedriger sinken zu lassen
              als die allgemeine Sicherheitsreserve, um die schnellere
              Ladeleistung im unteren SoC-Bereich zu nutzen.
            </small>
          </div>
          <div style={{ flex: 1 }}>
            <label style={{ display: "block", marginBottom: "0.25rem" }}>
              Min. Ladedauer (min)
            </label>
            <input
              type="number"
              min="0"
              max="30"
              step="1"
              value={minChargeTimeMin}
              onChange={(e) =>
                setMinChargeTimeMin(parseInt(e.target.value, 10) || 0)
              }
              style={{ width: "100%", padding: "0.4rem" }}
            />
            <small
              style={{
                display: "block",
                fontSize: "0.75rem",
                color: "#6b7280",
                marginTop: "0.25rem",
                lineHeight: "1.3",
              }}
            >
              Verhindert unnötig kurze Ladehalte - ein Halt dauert entweder gar
              nicht oder mindestens so lange.
            </small>
          </div>
          <div style={{ flex: 1 }}>
            <label style={{ display: "block", marginBottom: "0.25rem" }}>
              Max. Lade-SoC (%)
            </label>
            <input
              type="number"
              min="0"
              max="100"
              step="1"
              value={maxChargeSocPct}
              onChange={(e) =>
                setMaxChargeSocPct(parseInt(e.target.value, 10) || 0)
              }
              style={{ width: "100%", padding: "0.4rem" }}
            />
            <small
              style={{
                display: "block",
                fontSize: "0.75rem",
                color: "#6b7280",
                marginTop: "0.25rem",
                lineHeight: "1.3",
              }}
            >
              Obergrenze für den Ladeziel-SoC an regulären Ladestopps - 100
              deaktiviert die Begrenzung. Laden an Zwischenstopps ist nicht
              betroffen.
            </small>
          </div>
        </div>
      </Modal>

      {/* 2. Wetter-, Baustellen- und Fähren-Kontrollen in einer (bei Bedarf
          umbrechenden) Zeile im selben Pill-Stil. Die Fähren-Pille steht
          bewusst am Ende und ist zweigeteilt: links ein Toggle (invertierte
          Bedeutung: an = Fähren erlaubt), rechts - durch einen dünnen
          Trenner abgesetzt - ein reiner Zähler-Button, der nur das Modal
          für dauerhaft ignorierte Fähren öffnet (kein eigener Toggle-Status). */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "0.4rem",
          marginBottom: "1rem",
        }}
      >
        <Popover
          content={
            <>
              <div style={{ fontWeight: 600, marginBottom: "0.25rem" }}>
                Wetter: {weatherDetailLevel === "off" && "Aus"}
                {weatherDetailLevel === "low" && "Niedrig"}
                {weatherDetailLevel === "medium" && "Mittel"}
                {weatherDetailLevel === "high" && "Hoch"}
              </div>
              {weatherDetailLevel === "off"
                ? "Wetterdaten werden ignoriert (klicken: Niedrig → Mittel → Hoch → Aus)"
                : "Durchklicken: nächste Stufe (Niedrig → Mittel → Hoch → Aus)"}
            </>
          }
        >
          <button
            type="button"
            onClick={() => {
              const levels: WeatherDetailLevel[] = [
                "off",
                "low",
                "medium",
                "high",
              ];
              const idx = levels.indexOf(weatherDetailLevel);
              setWeatherDetailLevel(levels[(idx + 1) % levels.length]);
            }}
            disabled={isSubmitting}
            aria-pressed={weatherDetailLevel !== "off"}
            aria-label={`Wetterberücksichtigung: ${weatherDetailLevel}`}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.2rem",
              padding: "0.35rem 0.55rem",
              background: weatherDetailLevel !== "off" ? "#eff6ff" : "#f9fafb",
              border: `1px solid ${
                weatherDetailLevel !== "off" ? "#93c5fd" : "#e5e7eb"
              }`,
              borderRadius: "999px",
              cursor: isSubmitting ? "not-allowed" : "pointer",
              fontSize: "0.78rem",
              color: weatherDetailLevel !== "off" ? "#1d4ed8" : "#6b7280",
            }}
          >
            <CloudSun size={13} />
            {weatherDetailLevel === "off" && <SignalZero size={13} />}
            {weatherDetailLevel === "low" && <SignalLow size={13} />}
            {weatherDetailLevel === "medium" && <SignalMedium size={13} />}
            {weatherDetailLevel === "high" && <SignalHigh size={13} />}
          </button>
        </Popover>
        <Popover
          content={
            considerRoadworks
              ? "Baustellen werden bei der Berechnung berücksichtigt (klicken zum Deaktivieren)"
              : "Baustellen werden bei der Berechnung ignoriert (klicken zum Aktivieren)"
          }
        >
          <button
            type="button"
            onClick={() => setConsiderRoadworks(!considerRoadworks)}
            disabled={isSubmitting}
            aria-pressed={considerRoadworks}
            aria-label="Baustellen berücksichtigen"
            style={{
              display: "flex",
              alignItems: "center",
              padding: "0.35rem 0.55rem",
              background: considerRoadworks ? "#eff6ff" : "#f9fafb",
              border: `1px solid ${considerRoadworks ? "#93c5fd" : "#e5e7eb"}`,
              borderRadius: "999px",
              cursor: isSubmitting ? "not-allowed" : "pointer",
              fontSize: "0.78rem",
              color: considerRoadworks ? "#1d4ed8" : "#6b7280",
            }}
          >
            <Construction size={13} />
          </button>
        </Popover>
        <Popover
          content={
            <>
              <div style={{ fontWeight: 600, marginBottom: "0.25rem" }}>
                Autobahn: {autobahnPraeferenz === "off" && "Aus"}
                {autobahnPraeferenz === "low" && "Niedrig"}
                {autobahnPraeferenz === "medium" && "Mittel"}
                {autobahnPraeferenz === "high" && "Hoch"}
              </div>
              {autobahnPraeferenz === "off"
                ? "Autobahnen werden nicht bevorzugt (klicken: Niedrig → Mittel → Hoch → Aus)"
                : "Durchklicken: nächste Stufe (Niedrig → Mittel → Hoch → Aus)"}
            </>
          }
        >
          <button
            type="button"
            onClick={() => {
              const levels: HighwayPreferenceLevel[] = [
                "off",
                "low",
                "medium",
                "high",
              ];
              const idx = levels.indexOf(autobahnPraeferenz);
              setHighwayPreference(levels[(idx + 1) % levels.length]);
            }}
            disabled={isSubmitting}
            aria-pressed={autobahnPraeferenz !== "off"}
            aria-label={`Autobahnpräferenz: ${autobahnPraeferenz}`}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.2rem",
              padding: "0.35rem 0.55rem",
              background: autobahnPraeferenz !== "off" ? "#eff6ff" : "#f9fafb",
              border: `1px solid ${
                autobahnPraeferenz !== "off" ? "#93c5fd" : "#e5e7eb"
              }`,
              borderRadius: "999px",
              cursor: isSubmitting ? "not-allowed" : "pointer",
              fontSize: "0.78rem",
              color: autobahnPraeferenz !== "off" ? "#1d4ed8" : "#6b7280",
            }}
          >
            <Route size={13} />
            {autobahnPraeferenz === "off" && <SignalZero size={13} />}
            {autobahnPraeferenz === "low" && <SignalLow size={13} />}
            {autobahnPraeferenz === "medium" && <SignalMedium size={13} />}
            {autobahnPraeferenz === "high" && <SignalHigh size={13} />}
          </button>
        </Popover>
        <div
          style={{
            display: "flex",
            alignItems: "stretch",
            borderRadius: "999px",
            border: `1px solid ${!avoidAllFerries ? "#93c5fd" : "#e5e7eb"}`,
            overflow: "hidden",
          }}
        >
          <Popover
            content={
              avoidAllFerries
                ? "Fähren werden bei der Berechnung vermieden (klicken zum Erlauben)"
                : "Fähren sind bei der Berechnung erlaubt (klicken zum Vermeiden)"
            }
          >
            <button
              type="button"
              onClick={() => setAvoidAllFerries(!avoidAllFerries)}
              disabled={isSubmitting}
              aria-pressed={!avoidAllFerries}
              aria-label="Fähren erlauben"
              style={{
                display: "flex",
                alignItems: "center",
                padding: "0.35rem 0.5rem 0.35rem 0.55rem",
                background: !avoidAllFerries ? "#eff6ff" : "#f9fafb",
                border: "none",
                cursor: isSubmitting ? "not-allowed" : "pointer",
                fontSize: "0.78rem",
                color: !avoidAllFerries ? "#1d4ed8" : "#6b7280",
              }}
            >
              <Ship size={13} />
            </button>
          </Popover>
          <div
            style={{
              width: "1px",
              background: !avoidAllFerries ? "#93c5fd" : "#e5e7eb",
            }}
          />
          <Popover content="Ignorierte Fähren verwalten">
            <button
              type="button"
              onClick={() => setIsIgnoredFerriesModalOpen(true)}
              disabled={isSubmitting}
              style={{
                display: "flex",
                alignItems: "center",
                padding: "0.35rem 0.6rem 0.35rem 0.5rem",
                background: avoidedFerries.length > 0 ? "#fffbeb" : "#f9fafb",
                border: "none",
                cursor: isSubmitting ? "not-allowed" : "pointer",
                fontSize: "0.78rem",
                fontWeight: avoidedFerries.length > 0 ? 600 : 400,
                color: avoidedFerries.length > 0 ? "#b45309" : "#6b7280",
              }}
            >
              {avoidedFerries.length}
            </button>
          </Popover>
        </div>
      </div>

      {/* 3. Route (dynamische Stopp-/Ladehalt-/Fähren-Timeline) */}
      <ol
        style={{
          listStyle: "none",
          margin: 0,
          marginBottom: "0.75rem",
          padding: 0,
          marginLeft: "0.9rem",
          borderLeft: "2px solid #e5e7eb",
        }}
      >
        {routeEntries.map((entry, entryIdx) => {
          if (entry.art === "Stopp") {
            const { stop, stopIndex: idx } = entry;
            const role = getStopRole(stops, idx);
            const isLast = idx === stops.length - 1;
            const geoState = geocodingStates[stop.id];
            const showSuggestions = geoState && geoState.suggestions.length > 0;

            // leaveAt aufsplitten für date/time-Inputs
            const leaveAtParts = stop.leaveAt
              ? splitIsoToDateTime(stop.leaveAt)
              : null;
            const showLeaveAt = !isLast;

            const { Icon, background } = getStopTimelineIcon(stops, idx);

            // Tageswechsel INNERHALB dieses Eintrags (Ankunft und Abfahrt an
            // unterschiedlichen Kalendertagen, z. B. ein Zwischenstopp über
            // Mitternacht) - Abfahrts-Badge bekommt dann zusätzlich das
            // Datum (siehe `TagestrennerEintrag`-Docstring in
            // `route-entries.ts`).
            const departureDateShort =
              entry.timing.departure !== null &&
              isDayChange(entry.timing.arrival, entry.timing.departure)
                ? formatDayMonth(entry.timing.departure)
                : undefined;

            return (
              <TimelineRow key={stop.id} icon={Icon} background={background}>
                <div
                  style={{
                    position: "relative",
                    border: "1px solid #e5e7eb",
                    borderRadius: "6px",
                    padding: "0.75rem",
                    background: "white",
                    display: "flex",
                    flexDirection: "column",
                    gap: "0.5rem",
                  }}
                >
                  {entry.timing.arrival !== null && (
                    <TimeBadge
                      edge="oben"
                      iso={entry.timing.arrival}
                      socPct={entry.timing.arrivalSocPct ?? undefined}
                    />
                  )}
                  {entry.timing.departure !== null &&
                    differsAsTime(
                      entry.timing.arrival,
                      entry.timing.departure,
                    ) && (
                      <TimeBadge
                        edge="unten"
                        iso={entry.timing.departure}
                        socPct={entry.timing.departureSocPct ?? undefined}
                        shortDate={departureDateShort}
                      />
                    )}
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
                          idx === 0
                            ? "#e0e7ff"
                            : isLast
                              ? "#fef3c7"
                              : "#f3f4f6",
                        color:
                          idx === 0
                            ? "#3730a3"
                            : isLast
                              ? "#92400e"
                              : "#4b5563",
                      }}
                    >
                      {role}
                    </span>
                    <div style={{ display: "flex", gap: "0.25rem" }}>
                      {(idx === 0 || isLast) && (
                        <button
                          type="button"
                          onClick={() =>
                            setSocEditingStopId(
                              socEditingStopId === stop.id ? null : stop.id,
                            )
                          }
                          disabled={isSubmitting}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: "0.15rem",
                            padding: "0.2rem 0.4rem",
                            fontSize: "0.7rem",
                            fontWeight: 600,
                            background:
                              socEditingStopId === stop.id
                                ? "#7c3aed"
                                : "#f5f3ff",
                            border: `1px solid ${socEditingStopId === stop.id ? "#7c3aed" : "#c4b5fd"}`,
                            borderRadius: "4px",
                            color:
                              socEditingStopId === stop.id
                                ? "white"
                                : "#6d28d9",
                            cursor: isSubmitting ? "not-allowed" : "pointer",
                            opacity: isSubmitting ? 0.5 : 1,
                          }}
                          title={
                            idx === 0
                              ? "Start-SoC festlegen"
                              : "Ziel-SoC festlegen"
                          }
                          aria-label={
                            idx === 0
                              ? "Start-SoC festlegen"
                              : "Ziel-SoC festlegen"
                          }
                        >
                          <Battery size={12} />
                          {idx === 0 ? startSoc : targetSoc}%
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() =>
                          onRequestPick(
                            pickingStopId === stop.id ? null : stop.id,
                          )
                        }
                        disabled={isSubmitting}
                        style={{
                          display: "flex",
                          padding: "0.2rem",
                          background:
                            pickingStopId === stop.id ? "#2563eb" : "#eff6ff",
                          border: `1px solid ${pickingStopId === stop.id ? "#2563eb" : "#93c5fd"}`,
                          borderRadius: "4px",
                          color:
                            pickingStopId === stop.id ? "white" : "#1d4ed8",
                          cursor: isSubmitting ? "not-allowed" : "pointer",
                          opacity: isSubmitting ? 0.5 : 1,
                        }}
                        title={
                          pickingStopId === stop.id
                            ? "Kartenauswahl abbrechen"
                            : "Auf Karte wählen"
                        }
                        aria-label={
                          pickingStopId === stop.id
                            ? "Kartenauswahl abbrechen"
                            : "Auf Karte wählen"
                        }
                      >
                        {pickingStopId === stop.id ? (
                          <X size={14} />
                        ) : (
                          <Crosshair size={14} />
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={() =>
                          setPickerTargetId(
                            pickerTargetId === stop.id ? null : stop.id,
                          )
                        }
                        disabled={isSubmitting}
                        style={{
                          display: "flex",
                          padding: "0.2rem",
                          background:
                            pickerTargetId === stop.id ? "#166534" : "#dcfce7",
                          border: `1px solid ${pickerTargetId === stop.id ? "#166534" : "#86efac"}`,
                          borderRadius: "4px",
                          color:
                            pickerTargetId === stop.id ? "white" : "#166534",
                          cursor: isSubmitting ? "not-allowed" : "pointer",
                          opacity: isSubmitting ? 0.5 : 1,
                        }}
                        title={
                          pickerTargetId === stop.id
                            ? "Ladestationsauswahl abbrechen"
                            : "Ladestation statt Adresse wählen"
                        }
                        aria-label={
                          pickerTargetId === stop.id
                            ? "Ladestationsauswahl abbrechen"
                            : "Ladestation statt Adresse wählen"
                        }
                      >
                        {pickerTargetId === stop.id ? (
                          <X size={14} />
                        ) : (
                          <BatteryCharging size={14} />
                        )}
                      </button>
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
                            idx === 0 || isSubmitting
                              ? "not-allowed"
                              : "pointer",
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

                  {/* Inline-Editor für Start-/Ziel-SoC, geöffnet über den
                      Prozent-Button in der Kopfzeile. */}
                  {socEditingStopId === stop.id && (idx === 0 || isLast) && (
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "0.4rem",
                      }}
                    >
                      <label style={{ fontSize: "0.8rem", fontWeight: 500 }}>
                        {idx === 0 ? "Start-SoC (%)" : "Ziel-SoC (%)"}
                      </label>
                      <input
                        type="number"
                        min="0"
                        max="100"
                        step="1"
                        autoFocus
                        value={idx === 0 ? startSoc : targetSoc}
                        onChange={(e) => {
                          const value = parseInt(e.target.value, 10) || 0;
                          if (idx === 0) {
                            setStartSoc(value);
                          } else {
                            setTargetSoc(value);
                          }
                        }}
                        onBlur={() => setSocEditingStopId(null)}
                        style={{
                          width: "5rem",
                          padding: "0.3rem",
                          fontSize: "0.85rem",
                        }}
                      />
                    </div>
                  )}

                  {/* Address Input + Geocoding */}
                  <div style={{ position: "relative" }}>
                    <input
                      type="text"
                      placeholder="Adresse eingeben…"
                      value={stop.address}
                      onChange={(e) =>
                        handleAddressInput(stop.id, e.target.value)
                      }
                      onBlur={() => {
                        // Beim Verlassen die Vorschläge erst nach kurzer Verzögerung schließen,
                        // damit der Klick auf einen Vorschlag noch registriert wird
                        setTimeout(() => handleCloseSuggestions(stop.id), 200);
                      }}
                      onFocus={(e) => {
                        // Bei Fokus und genug Text: Suche neu starten
                        if (
                          e.target.value.trim().length >=
                          GEOCODING_MIN_QUERY_LENGTH
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

                  {pickingStopId === stop.id && (
                    <p
                      style={{
                        margin: 0,
                        fontSize: "0.8rem",
                        color: "#2563eb",
                      }}
                    >
                      Klicken Sie auf die Karte, um den Punkt zu platzieren.
                    </p>
                  )}

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
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "space-between",
                          marginBottom: "0.25rem",
                        }}
                      >
                        <label
                          style={{
                            fontSize: "0.8rem",
                            fontWeight: 500,
                          }}
                        >
                          Abfahrt hier
                        </label>
                        {stop.leaveAt && (
                          <button
                            type="button"
                            onClick={() => handleLeaveAtClear(stop.id)}
                            style={{
                              display: "flex",
                              padding: "0.1rem 0.3rem",
                              fontSize: "0.8rem",
                              lineHeight: 1,
                              background: "none",
                              border: "none",
                              color: "#991b1b",
                              cursor: "pointer",
                            }}
                            title="Abfahrtszeit löschen"
                            aria-label="Abfahrtszeit löschen"
                          >
                            <X size={14} />
                          </button>
                        )}
                      </div>
                      <div style={{ display: "flex", gap: "0.4rem" }}>
                        <input
                          type="date"
                          value={leaveAtParts?.date ?? ""}
                          onChange={(e) => {
                            const newTime = leaveAtParts?.time ?? "12:00";
                            if (e.target.value) {
                              handleLeaveAtChange(
                                stop.id,
                                e.target.value,
                                newTime,
                              );
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
                              handleLeaveAtChange(
                                stop.id,
                                newDate,
                                e.target.value,
                              );
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
                      {idx !== 0 && (
                        <div
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: "0.4rem",
                            marginTop: "0.5rem",
                          }}
                        >
                          <label
                            style={{
                              fontSize: "0.8rem",
                              fontWeight: 500,
                              whiteSpace: "nowrap",
                            }}
                          >
                            Ladeleistung
                          </label>
                          <input
                            type="number"
                            min="0"
                            step="0.1"
                            placeholder="optional"
                            value={stop.chargingPowerKw ?? ""}
                            onChange={(e) =>
                              handleChargingPowerChange(stop.id, e.target.value)
                            }
                            style={{
                              flex: 1,
                              minWidth: 0,
                              padding: "0.4rem",
                              fontSize: "0.85rem",
                            }}
                          />
                          <span
                            style={{
                              fontSize: "0.8rem",
                              color: "#6b7280",
                              whiteSpace: "nowrap",
                            }}
                          >
                            kW
                          </span>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </TimelineRow>
            );
          }

          if (entry.art === "Ladehalt") {
            const stop = entry.chargingStop;
            const target = chargingDurationPresets.find(
              (v) => v.stationId === stop.stationId,
            );
            const chargingDurationMin = Math.round(
              (target?.chargingDurationS ?? stop.chargingDurationS) / 60,
            );
            const departureDateShort = isDayChange(
              stop.arrivalTime,
              stop.departureTime,
            )
              ? formatDayMonth(stop.departureTime)
              : undefined;

            return (
              <TimelineRow key={stop.stationId} icon={Zap} background="#dcfce7">
                <div
                  style={{
                    position: "relative",
                    border: "1px solid #e5e7eb",
                    borderRadius: "6px",
                    padding: "0.5rem 0.75rem",
                    background: "white",
                    fontSize: "0.8rem",
                    display: "grid",
                    gap: "0.3rem",
                  }}
                >
                  <TimeBadge
                    edge="oben"
                    iso={stop.arrivalTime}
                    socPct={stop.arrivalSocPct}
                  />
                  <TimeBadge
                    edge="unten"
                    iso={stop.departureTime}
                    socPct={stop.targetSocPct}
                    shortDate={departureDateShort}
                  />
                  <strong>{formatChargingStationName(stop.name)}</strong>
                  <label
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "0.4rem",
                    }}
                  >
                    Ladedauer (min)
                    <input
                      type="number"
                      min="0"
                      step="5"
                      value={chargingDurationMin}
                      disabled={isSubmitting}
                      onChange={(e) => {
                        const next = setChargingDurationPresetFor(
                          chargingDurationPresets,
                          stop.stationId,
                          parseFloat(e.target.value) || 0,
                        );
                        setChargingDurationPresets(next);
                      }}
                      onBlur={() =>
                        buildAndSubmit(
                          avoidAllFerries,
                          avoidedFerries,
                          ferryTimeWindows,
                          chargingDurationPresets,
                        )
                      }
                      style={{ width: "5rem", padding: "0.3rem" }}
                    />
                  </label>
                </div>
              </TimelineRow>
            );
          }

          if (entry.art === "Tagestrenner") {
            return (
              <DaySeparator
                key={`trenner-${entryIdx}`}
                previousIso={entry.vonIso}
                currentIso={entry.bisIso}
              />
            );
          }

          if (entry.art === "Fahrsegment") {
            // Fahrsegment quer über Mitternacht: Strecke/Zeit/beide Daten
            // in EINER Zeile statt zusätzlich einen separaten
            // Tagestrenner zu rendern (siehe `FahrsegmentEintrag`-
            // Docstring in `route-entries.ts`).
            const dayChange = isDayChange(entry.vonIso, entry.bisIso)
              ? { vonIso: entry.vonIso, bisIso: entry.bisIso }
              : undefined;
            return (
              <DriveSegmentRow
                key={`fahrsegment-${entryIdx}`}
                distanceKm={entry.distanceKm}
                durationMin={entry.durationMin}
                dayChange={dayChange}
              />
            );
          }

          // eintrag.art === "Fähre"
          const ferry = entry.ferry;
          const exclusionEntry: FerryExclusion = {
            name: ferry.name,
            bboxSw: ferry.bboxSw,
            bboxNe: ferry.bboxNe,
          };
          const timeWindowEntry = ferryTimeWindows.find((f) =>
            sameFerryExclusion(f, exclusionEntry),
          );
          const key = ferryKey(exclusionEntry);
          const draft = ferryTimeWindowDraft[key] ?? {
            departure: timeWindowEntry?.departure ?? "",
            arrival: timeWindowEntry?.arrival ?? "",
          };
          const departureParts = draft.departure
            ? splitIsoToDateTime(draft.departure)
            : null;
          const arrivalParts = draft.arrival
            ? splitIsoToDateTime(draft.arrival)
            : null;

          const handleTimeWindowChange = (
            field: "departure" | "arrival",
            date: string,
            time: string,
          ) => {
            const iso = date && time ? combineDateTimeToIso(date, time) : "";
            const nextDraft = {
              departure: field === "departure" ? iso : draft.departure,
              arrival: field === "arrival" ? iso : draft.arrival,
            };
            setFerryTimeWindowDraft((prev) => ({ ...prev, [key]: nextDraft }));
            const next = setFerryTimeWindowFor(
              ferryTimeWindows,
              exclusionEntry,
              nextDraft.departure,
              nextDraft.arrival,
            );
            setFerryTimeWindows(next);
            if (nextDraft.departure && nextDraft.arrival) {
              buildAndSubmit(
                avoidAllFerries,
                avoidedFerries,
                next,
                chargingDurationPresets,
              );
            }
          };

          const handleIgnore = () => {
            const next = toggleFerryExclusion(
              avoidedFerries,
              exclusionEntry,
              true,
            );
            setAvoidedFerries(next);
            buildAndSubmit(
              avoidAllFerries,
              next,
              ferryTimeWindows,
              chargingDurationPresets,
            );
          };

          const departureDateShort =
            entry.timing.departure !== null &&
            isDayChange(entry.timing.arrival, entry.timing.departure)
              ? formatDayMonth(entry.timing.departure)
              : undefined;

          return (
            <TimelineRow key={key} icon={Ship} background="#dbeafe">
              <div
                style={{
                  position: "relative",
                  border: "1px solid #bfdbfe",
                  borderRadius: "6px",
                  padding: "0.5rem 0.75rem",
                  background: "#eff6ff",
                  display: "grid",
                  gap: "0.4rem",
                }}
              >
                {entry.timing.arrival !== null && (
                  <TimeBadge
                    edge="oben"
                    iso={entry.timing.arrival}
                    socPct={entry.timing.arrivalSocPct ?? undefined}
                  />
                )}
                {entry.timing.departure !== null &&
                  differsAsTime(
                    entry.timing.arrival,
                    entry.timing.departure,
                  ) && (
                    <TimeBadge
                      edge="unten"
                      iso={entry.timing.departure}
                      socPct={entry.timing.departureSocPct ?? undefined}
                      shortDate={departureDateShort}
                    />
                  )}
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: "0.5rem",
                  }}
                >
                  <span style={{ fontSize: "0.85rem", fontWeight: 600 }}>
                    ⛴ {ferry.name} ({(ferry.lengthM / 1000).toFixed(1)} km)
                  </span>
                  <button
                    type="button"
                    onClick={handleIgnore}
                    disabled={isSubmitting}
                    style={{
                      padding: "0.2rem 0.5rem",
                      fontSize: "0.75rem",
                      background: "#fef2f2",
                      border: "1px solid #fecaca",
                      borderRadius: "4px",
                      color: "#991b1b",
                      cursor: isSubmitting ? "not-allowed" : "pointer",
                    }}
                  >
                    Ignorieren
                  </button>
                </div>
                <div
                  style={{
                    display: "grid",
                    gap: "0.25rem",
                    fontSize: "0.8rem",
                  }}
                >
                  <span>Fährfahrplan (optional, für die Planung):</span>
                  <div style={{ display: "flex", gap: "0.4rem" }}>
                    <input
                      type="date"
                      value={departureParts?.date ?? ""}
                      disabled={isSubmitting}
                      onChange={(e) =>
                        handleTimeWindowChange(
                          "departure",
                          e.target.value,
                          departureParts?.time ?? "12:00",
                        )
                      }
                      style={{ flex: 1, padding: "0.3rem" }}
                    />
                    <input
                      type="time"
                      value={departureParts?.time ?? ""}
                      disabled={isSubmitting}
                      onChange={(e) =>
                        handleTimeWindowChange(
                          "departure",
                          departureParts?.date ?? "",
                          e.target.value,
                        )
                      }
                      style={{ flex: 1, padding: "0.3rem" }}
                    />
                  </div>
                  <span>Ankunft:</span>
                  <div style={{ display: "flex", gap: "0.4rem" }}>
                    <input
                      type="date"
                      value={arrivalParts?.date ?? ""}
                      disabled={isSubmitting}
                      onChange={(e) =>
                        handleTimeWindowChange(
                          "arrival",
                          e.target.value,
                          arrivalParts?.time ?? "12:00",
                        )
                      }
                      style={{ flex: 1, padding: "0.3rem" }}
                    />
                    <input
                      type="time"
                      value={arrivalParts?.time ?? ""}
                      disabled={isSubmitting}
                      onChange={(e) =>
                        handleTimeWindowChange(
                          "arrival",
                          arrivalParts?.date ?? "",
                          e.target.value,
                        )
                      }
                      style={{ flex: 1, padding: "0.3rem" }}
                    />
                  </div>
                </div>
              </div>
            </TimelineRow>
          );
        })}
      </ol>

      {/* Ignorierte Fähren - Modal, ausgelöst über den Zähler-Teil der
          zweigeteilten "Fähren"-Pille; enthält dauerhaft ausgeschlossene
          Fährverbindungen (unabhängig von der aktuell berechneten Route). */}
      <Modal
        open={isIgnoredFerriesModalOpen}
        onClose={() => setIsIgnoredFerriesModalOpen(false)}
        title="Ignorierte Fähren"
      >
        {avoidedFerries.length === 0 ? (
          <p style={{ margin: 0, color: "#6b7280" }}>
            Keine ignorierten Fähren.
          </p>
        ) : (
          <div style={{ display: "grid", gap: "0.5rem" }}>
            {avoidedFerries.map((entry) => {
              const lengthM =
                (detectedFerries ?? []).find((f) =>
                  sameFerryExclusion(
                    { name: f.name, bboxSw: f.bboxSw, bboxNe: f.bboxNe },
                    entry,
                  ),
                )?.lengthM ?? null;
              return (
                <div
                  key={ferryKey(entry)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: "0.5rem",
                    border: "1px solid #e5e7eb",
                    borderRadius: "6px",
                    padding: "0.5rem 0.75rem",
                  }}
                >
                  <span style={{ fontSize: "0.85rem" }}>
                    {entry.name}
                    {lengthM !== null && ` (${(lengthM / 1000).toFixed(1)} km)`}
                  </span>
                  <button
                    type="button"
                    onClick={() => {
                      const next = toggleFerryExclusion(
                        avoidedFerries,
                        entry,
                        false,
                      );
                      setAvoidedFerries(next);
                      buildAndSubmit(
                        avoidAllFerries,
                        next,
                        ferryTimeWindows,
                        chargingDurationPresets,
                      );
                    }}
                    disabled={isSubmitting}
                    style={{
                      padding: "0.2rem 0.5rem",
                      fontSize: "0.75rem",
                      background: "#dcfce7",
                      border: "1px solid #86efac",
                      borderRadius: "4px",
                      color: "#166534",
                      cursor: isSubmitting ? "not-allowed" : "pointer",
                    }}
                  >
                    Wieder zulassen
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </Modal>

      {/* Add Stop Button */}
      <button
        type="button"
        onClick={handleAddStop}
        disabled={isSubmitting || stops.length < 1}
        style={{
          width: "100%",
          padding: "0.5rem",
          marginBottom: "0.75rem",
          background: "#f3f4f6",
          border: "1px solid #d1d5db",
          borderRadius: "4px",
          cursor: isSubmitting || stops.length < 1 ? "not-allowed" : "pointer",
          opacity: isSubmitting || stops.length < 1 ? 0.6 : 1,
        }}
      >
        + Stopp hinzufügen
      </button>

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
