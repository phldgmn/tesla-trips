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
      fahrzeugprofil: vehicleProfile,
      startSocPct: 80,
      zielSocPct: 20,
    });

    expect(payload.alle_faehren_vermeiden).toBe(false);
    expect(payload.vermiedene_faehren).toEqual([]);
  });

  it("passes through explicit ferry avoidance values", () => {
    const payload = buildTripRequestPayload({
      stops: makeStops(),
      fahrzeugprofil: vehicleProfile,
      startSocPct: 80,
      zielSocPct: 20,
      alleFaehrenVermeiden: true,
      vermiedeneFaehren: [
        {
          name: "Rødby (DK) - Puttgarden (D)",
          bbox_sw: [54.5, 11.22],
          bbox_no: [54.66, 11.36],
        },
      ],
    });

    expect(payload.alle_faehren_vermeiden).toBe(true);
    expect(payload.vermiedene_faehren).toHaveLength(1);
    expect(payload.vermiedene_faehren[0].name).toBe(
      "Rødby (DK) - Puttgarden (D)",
    );
  });
});

describe("buildTripRequestPayload faehr_zeitfenster/ladedauer_vorgaben fields", () => {
  it("defaults faehr_zeitfenster and ladedauer_vorgaben to empty arrays", () => {
    const payload = buildTripRequestPayload({
      stops: makeStops(),
      fahrzeugprofil: vehicleProfile,
      startSocPct: 80,
      zielSocPct: 20,
    });

    expect(payload.faehr_zeitfenster).toEqual([]);
    expect(payload.ladedauer_vorgaben).toEqual([]);
  });

  it("passes through explicit ferry time windows and duration overrides", () => {
    const payload = buildTripRequestPayload({
      stops: makeStops(),
      fahrzeugprofil: vehicleProfile,
      startSocPct: 80,
      zielSocPct: 20,
      faehrZeitfenster: [
        {
          name: "Rødby (DK) - Puttgarden (D)",
          bbox_sw: [54.5, 11.22],
          bbox_no: [54.66, 11.36],
          abfahrt: "2026-08-15T10:00:00",
          ankunft: "2026-08-15T11:09:00",
        },
      ],
      ladedauerVorgaben: [{ station_id: "station-1", ladedauer_s: 1800 }],
    });

    expect(payload.faehr_zeitfenster).toHaveLength(1);
    expect(payload.faehr_zeitfenster[0].abfahrt).toBe("2026-08-15T10:00:00");
    expect(payload.ladedauer_vorgaben).toEqual([
      { station_id: "station-1", ladedauer_s: 1800 },
    ]);
  });
});

describe("buildTripRequestPayload wetter_beruecksichtigen/baustellen_beruecksichtigen fields", () => {
  it("defaults wetter_beruecksichtigen and baustellen_beruecksichtigen to true", () => {
    const payload = buildTripRequestPayload({
      stops: makeStops(),
      fahrzeugprofil: vehicleProfile,
      startSocPct: 80,
      zielSocPct: 20,
    });

    expect(payload.wetter_beruecksichtigen).toBe(true);
    expect(payload.baustellen_beruecksichtigen).toBe(true);
  });

  it("passes through explicit false values to disable providers", () => {
    const payload = buildTripRequestPayload({
      stops: makeStops(),
      fahrzeugprofil: vehicleProfile,
      startSocPct: 80,
      zielSocPct: 20,
      wetterBeruecksichtigen: false,
      baustellenBeruecksichtigen: false,
    });

    expect(payload.wetter_beruecksichtigen).toBe(false);
    expect(payload.baustellen_beruecksichtigen).toBe(false);
  });
});
