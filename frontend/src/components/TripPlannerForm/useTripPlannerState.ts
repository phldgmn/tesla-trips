import { useState } from "react";
import {
  usePersistentState,
  migrateConsiderWeather,
} from "../../utils/persistent-state";
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
import { combineDateTimeToIso } from "../../utils/datetime-utils";
import { validateForm, swapStops } from "./form-helpers";

/** Overrides for a single submission; anything omitted uses the current state.
 *  Needed when a handler submits right after a state update, before React has
 *  re-rendered with the new value. */
export interface SubmitOverrides {
  avoidedFerries?: FerryExclusion[];
  ferryTimeWindows?: FerryTimeWindow[];
  chargingDurations?: ChargingDurationSpecification[];
}

interface UseTripPlannerStateArgs {
  stops: Stop[];
  onStopsChange: (stops: Stop[]) => void;
  onSubmit: (payload: TripRequestPayload) => void;
}

/** All form state of the trip planner: persisted settings (vehicle, SoC,
 *  routing options, ferry and charging overrides), stop-list editing,
 *  validation and building/submitting the request payload. */
export function useTripPlannerState({
  stops,
  onStopsChange,
  onSubmit,
}: UseTripPlannerStateArgs) {
  // --- Vehicle & SoC (persisted) ---
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

  // --- Routing options (persisted) ---
  const [avoidAllFerries, setAvoidAllFerries] = usePersistentState(
    "avoid-all-ferries",
    false,
  );
  const [highwayPreference, setHighwayPreference] =
    usePersistentState<HighwayPreferenceLevel>("highway-preference", "off");
  const [weatherDetailLevel, setWeatherDetailLevel] =
    usePersistentState<WeatherDetailLevel>("weather-detail-level", () =>
      migrateConsiderWeather(),
    );
  const [considerRoadworks, setConsiderRoadworks] = usePersistentState(
    "consider-construction-sites",
    true,
  );

  // --- Ferry and charging overrides (persisted) ---
  const [avoidedFerries, setAvoidedFerries] = usePersistentState<
    FerryExclusion[]
  >("avoided-ferries", []);
  const [ferryTimeWindows, setFerryTimeWindows] = usePersistentState<
    FerryTimeWindow[]
  >("ferry-time-windows", []);
  const [chargingDurations, setChargingDurations] = usePersistentState<
    ChargingDurationSpecification[]
  >("charging-duration-specifications", []);
  // Draft of the ferry timetable inputs (date and time entered separately)
  // while departure and arrival are not both complete. `ferryTimeWindows`
  // deliberately stores ONLY complete windows (see `setFerryTimeWindowFor`);
  // without this draft a controlled input would snap back to "" after each
  // keystroke while the other field is still empty.
  const [ferryTimeWindowDraft, setFerryTimeWindowDraft] = useState<
    Record<string, { departure: string; arrival: string }>
  >({});

  // --- Stop list editing ---
  const updateStop = (stopId: string, update: (stop: Stop) => Stop) =>
    onStopsChange(stops.map((s) => (s.id === stopId ? update(s) : s)));

  const addStop = () => {
    // Insert BEFORE the last item so the destination stays the destination.
    if (stops.length < 1) return;
    const insertAt = stops.length - 1;
    onStopsChange([
      ...stops.slice(0, insertAt),
      createEmptyStop(),
      stops[stops.length - 1],
    ]);
  };

  const removeStop = (stopId: string) => {
    if (stops.length <= 2) return; // keep at least start + destination
    onStopsChange(stops.filter((s) => s.id !== stopId));
  };

  const moveStopUp = (index: number) => {
    if (index <= 0) return;
    onStopsChange(swapStops(stops, index, index - 1));
  };

  const moveStopDown = (index: number) => {
    if (index >= stops.length - 1) return;
    onStopsChange(swapStops(stops, index, index + 1));
  };

  const setLeaveAt = (stopId: string, date: string, time: string) =>
    updateStop(stopId, (s) => ({
      ...s,
      leaveAt: combineDateTimeToIso(date, time),
    }));

  const clearLeaveAt = (stopId: string) =>
    updateStop(stopId, (s) => {
      const rest = { ...s };
      delete rest.leaveAt;
      return rest;
    });

  const setChargingPower = (stopId: string, value: string) => {
    const parsed = value.trim() === "" ? undefined : Number(value);
    updateStop(stopId, (s) => {
      if (parsed === undefined || Number.isNaN(parsed)) {
        const rest = { ...s };
        delete rest.chargingPowerKw;
        return rest;
      }
      return { ...s, chargingPowerKw: parsed };
    });
  };

  const setStopLocation = (
    stopId: string,
    location: { position: [number, number]; label: string },
  ) =>
    updateStop(stopId, (s) => ({
      ...s,
      address: location.label,
      position: location.position,
    }));

  // --- Vehicle ---
  const selectPreset = (presetId: string) => {
    const preset = VEHICLE_PROFILE_PRESETS.find((p) => p.id === presetId);
    if (preset) {
      setVehicleProfile(preset.profile);
      setSelectedPresetId(presetId);
    }
  };

  const setVehicleField = <K extends keyof VehicleProfileInput>(
    field: K,
    value: VehicleProfileInput[K],
  ) => {
    setVehicleProfile((prev) => ({ ...prev, [field]: value }));
    setSelectedPresetId(null);
  };

  // --- Validation & submit ---
  const validationErrors = validateForm({
    stops,
    startSoc,
    targetSoc,
    minChargingTimeMin: minChargeTimeMin,
    minArrivalSocPct,
    maxChargeSocPct,
  });

  const submit = (overrides: SubmitOverrides = {}) => {
    if (validationErrors.length > 0) return;
    try {
      onSubmit(
        buildTripRequestPayload({
          stops,
          vehicleProfile,
          startSocPct: startSoc,
          targetSocPct: targetSoc,
          minChargeDurationS: minChargeTimeMin * 60,
          minArrivalSocPct,
          maxChargeSocPct,
          preferences: {},
          avoidAllFerries,
          highwayPreference,
          avoidedFerries: overrides.avoidedFerries ?? avoidedFerries,
          ferryTimeWindows: overrides.ferryTimeWindows ?? ferryTimeWindows,
          chargingDurationSpecifications:
            overrides.chargingDurations ?? chargingDurations,
          weatherDetailLevel,
          considerConstructionSites: considerRoadworks,
        }),
      );
    } catch (error) {
      // Unresolved stops are already reported by `validationErrors`; the
      // builder re-checks them defensively, so nothing more to show here.
      if (!(error instanceof TripRequestBuildError)) throw error;
    }
  };

  return {
    vehicleProfile,
    selectedPresetId,
    selectPreset,
    setVehicleField,
    showAdvancedVehicle,
    setShowAdvancedVehicle,
    startSoc,
    setStartSoc,
    targetSoc,
    setTargetSoc,
    minArrivalSocPct,
    setMinArrivalSocPct,
    minChargeTimeMin,
    setMinChargeTimeMin,
    maxChargeSocPct,
    setMaxChargeSocPct,
    avoidAllFerries,
    setAvoidAllFerries,
    highwayPreference,
    setHighwayPreference,
    weatherDetailLevel,
    setWeatherDetailLevel,
    considerRoadworks,
    setConsiderRoadworks,
    avoidedFerries,
    setAvoidedFerries,
    ferryTimeWindows,
    setFerryTimeWindows,
    ferryTimeWindowDraft,
    setFerryTimeWindowDraft,
    chargingDurations,
    setChargingDurations,
    addStop,
    removeStop,
    moveStopUp,
    moveStopDown,
    setLeaveAt,
    clearLeaveAt,
    setChargingPower,
    setStopLocation,
    validationErrors,
    submit,
  };
}

export type TripPlannerState = ReturnType<typeof useTripPlannerState>;
