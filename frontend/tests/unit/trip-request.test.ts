import { describe, it, expect } from "vitest";
import { buildTripRequestPayload, createEmptyStop } from "@/types/trip-request";
import type { Stop, VehicleProfileInput } from "@/types/trip-request";

const vehicleProfile: VehicleProfileInput = {
  massKg: 1706,
  dragCoefficient: 0.23,
  frontalAreaM2: 2.22,
  rollingResistanceCoefficient: 0.011,
  batteryCapacityKwh: 62.5,
  auxiliaryBaselineKw: 0.34,
  tireType: "standard",
  roofBox: false,
};

function makeStops(): Stop[] {
  return [
    { ...createEmptyStop(), address: "Berlin", position: [52.52, 13.405] },
    { ...createEmptyStop(), address: "Hamburg", position: [53.551, 9.993] },
  ];
}

describe("buildTripRequestPayload ferry fields", () => {
  it("defaults avoid_all_ferries to false and avoided_ferries to empty", () => {
    const payload = buildTripRequestPayload({
      stops: makeStops(),
      vehicleProfile: vehicleProfile,
      startSocPct: 80,
      targetSocPct: 20,
    });

    expect(payload.avoidAllFerries).toBe(false);
    expect(payload.avoidedFerries).toEqual([]);
  });

  it("passes through explicit ferry avoidance values", () => {
    const payload = buildTripRequestPayload({
      stops: makeStops(),
      vehicleProfile: vehicleProfile,
      startSocPct: 80,
      targetSocPct: 20,
      avoidAllFerries: true,
      avoidedFerries: [
        {
          name: "Rødby (DK) - Puttgarden (D)",
          bboxSw: [54.5, 11.22],
          bboxNe: [54.66, 11.36],
        },
      ],
    });

    expect(payload.avoidAllFerries).toBe(true);
    expect(payload.avoidedFerries).toHaveLength(1);
    expect(payload.avoidedFerries[0].name).toBe("Rødby (DK) - Puttgarden (D)");
  });
});

describe("buildTripRequestPayload ferry_time_windows/charging_duration_specifications fields", () => {
  it("defaults ferry_time_windows and charging_duration_specifications to empty arrays", () => {
    const payload = buildTripRequestPayload({
      stops: makeStops(),
      vehicleProfile: vehicleProfile,
      startSocPct: 80,
      targetSocPct: 20,
    });

    expect(payload.ferryTimeWindows).toEqual([]);
    expect(payload.chargingDurationSpecifications).toEqual([]);
  });

  it("passes through explicit ferry time windows and duration overrides", () => {
    const payload = buildTripRequestPayload({
      stops: makeStops(),
      vehicleProfile: vehicleProfile,
      startSocPct: 80,
      targetSocPct: 20,
      ferryTimeWindows: [
        {
          name: "Rødby (DK) - Puttgarden (D)",
          bboxSw: [54.5, 11.22],
          bboxNe: [54.66, 11.36],
          departure: "2026-08-15T10:00:00",
          arrival: "2026-08-15T11:09:00",
        },
      ],
      chargingDurationSpecifications: [
        { stationId: "station-1", chargingDurationS: 1800 },
      ],
    });

    expect(payload.ferryTimeWindows).toHaveLength(1);
    expect(payload.ferryTimeWindows[0].departure).toBe("2026-08-15T10:00:00");
    expect(payload.chargingDurationSpecifications).toEqual([
      { stationId: "station-1", chargingDurationS: 1800 },
    ]);
  });
});

describe("buildTripRequestPayload wetter_detailgrad/baustellen_beruecksichtigen fields", () => {
  it("defaults wetter_detailgrad to high and baustellen_beruecksichtigen to true", () => {
    const payload = buildTripRequestPayload({
      stops: makeStops(),
      vehicleProfile: vehicleProfile,
      startSocPct: 80,
      targetSocPct: 20,
    });

    expect(payload.weatherDetailLevel).toBe("high");
    expect(payload.considerConstructionSites).toBe(true);
  });

  it("passes through explicit wetter_detailgrad and baustellen false value", () => {
    const payload = buildTripRequestPayload({
      stops: makeStops(),
      vehicleProfile: vehicleProfile,
      startSocPct: 80,
      targetSocPct: 20,
      weatherDetailLevel: "off",
      considerConstructionSites: false,
    });

    expect(payload.weatherDetailLevel).toBe("off");
    expect(payload.considerConstructionSites).toBe(false);
  });

  it("passes through wetter_detailgrad medium value", () => {
    const payload = buildTripRequestPayload({
      stops: makeStops(),
      vehicleProfile: vehicleProfile,
      startSocPct: 80,
      targetSocPct: 20,
      weatherDetailLevel: "medium",
    });

    expect(payload.weatherDetailLevel).toBe("medium");
  });
});

describe("buildTripRequestPayload max_charge_soc_pct field", () => {
  it("defaults max_charge_soc_pct to 100 (uncapped)", () => {
    const payload = buildTripRequestPayload({
      stops: makeStops(),
      vehicleProfile: vehicleProfile,
      startSocPct: 80,
      targetSocPct: 20,
    });

    expect(payload.maxChargeSocPct).toBe(100);
  });

  it("passes through an explicit maxChargeSocPct value", () => {
    const payload = buildTripRequestPayload({
      stops: makeStops(),
      vehicleProfile: vehicleProfile,
      startSocPct: 80,
      targetSocPct: 20,
      maxChargeSocPct: 80,
    });

    expect(payload.maxChargeSocPct).toBe(80);
  });
});
