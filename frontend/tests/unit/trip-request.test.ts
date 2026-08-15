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
