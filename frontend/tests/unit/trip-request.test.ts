import { describe, it, expect } from "vitest";
import { buildTripRequestPayload, createEmptyStop } from "@/types/trip-request";
import type { Stop, VehicleProfileInput } from "@/types/trip-request";

const vehicleProfile: VehicleProfileInput = {
  masse_kg: 1706,
  cw_wert: 0.23,
  stirnflaeche_m2: 2.22,
  rollwiderstandsbeiwert: 0.011,
  batteriekapazitaet_kwh: 62.5,
  nebenverbraucher_baseline_kw: 0.34,
  reifentyp: "standard",
  dachbox: false,
};

function makeStops(): Stop[] {
  return [
    { ...createEmptyStop(), address: "Berlin", position: [52.52, 13.405] },
    { ...createEmptyStop(), address: "Hamburg", position: [53.551, 9.993] },
  ];
}

describe("buildTripRequestPayload ferry fields", () => {
  it("defaults alle_faehren_vermeiden to false and vermiedene_faehren to empty", () => {
    const payload = buildTripRequestPayload({
      stops: makeStops(),
      vehicleProfile: vehicleProfile,
      startSocPct: 80,
      zielSocPct: 20,
    });

    expect(payload.avoidAllFerries).toBe(false);
    expect(payload.avoidedFerries).toEqual([]);
  });

  it("passes through explicit ferry avoidance values", () => {
    const payload = buildTripRequestPayload({
      stops: makeStops(),
      vehicleProfile: vehicleProfile,
      startSocPct: 80,
      zielSocPct: 20,
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

describe("buildTripRequestPayload faehr_zeitfenster/ladedauer_vorgaben fields", () => {
  it("defaults faehr_zeitfenster and ladedauer_vorgaben to empty arrays", () => {
    const payload = buildTripRequestPayload({
      stops: makeStops(),
      vehicleProfile: vehicleProfile,
      startSocPct: 80,
      zielSocPct: 20,
    });

    expect(payload.ferryTimeWindows).toEqual([]);
    expect(payload.chargingDurationSpecifications).toEqual([]);
  });

  it("passes through explicit ferry time windows and duration overrides", () => {
    const payload = buildTripRequestPayload({
      stops: makeStops(),
      vehicleProfile: vehicleProfile,
      startSocPct: 80,
      zielSocPct: 20,
      ferryTimeWindows: [
        {
          name: "Rødby (DK) - Puttgarden (D)",
          bboxSw: [54.5, 11.22],
          bboxNe: [54.66, 11.36],
          abfahrt: "2026-08-15T10:00:00",
          ankunft: "2026-08-15T11:09:00",
        },
      ],
      chargingDurationTargets: [
        { stationId: "station-1", chargingDurationS: 1800 },
      ],
    });

    expect(payload.ferryTimeWindows).toHaveLength(1);
    expect(payload.ferryTimeWindows[0].abfahrt).toBe("2026-08-15T10:00:00");
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
      zielSocPct: 20,
    });

    expect(payload.weatherDetailLevel).toBe("high");
    expect(payload.considerConstructionSites).toBe(true);
  });

  it("passes through explicit wetter_detailgrad and baustellen false value", () => {
    const payload = buildTripRequestPayload({
      stops: makeStops(),
      vehicleProfile: vehicleProfile,
      startSocPct: 80,
      zielSocPct: 20,
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
      zielSocPct: 20,
      weatherDetailLevel: "medium",
    });

    expect(payload.weatherDetailLevel).toBe("medium");
  });
});

describe("buildTripRequestPayload max_lade_soc_pct field", () => {
  it("defaults max_lade_soc_pct to 100 (uncapped)", () => {
    const payload = buildTripRequestPayload({
      stops: makeStops(),
      vehicleProfile: vehicleProfile,
      startSocPct: 80,
      zielSocPct: 20,
    });

    expect(payload.maxChargeSocPct).toBe(100);
  });

  it("passes through an explicit maxLadeSocPct value", () => {
    const payload = buildTripRequestPayload({
      stops: makeStops(),
      vehicleProfile: vehicleProfile,
      startSocPct: 80,
      zielSocPct: 20,
      maxLadeSocPct: 80,
    });

    expect(payload.maxChargeSocPct).toBe(80);
  });
});
