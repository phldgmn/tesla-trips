import { describe, it, expect } from "vitest";
import { buildTimePlan } from "@/components/TripSummary";
import { createEmptyStop } from "@/types/trip-request";
import type { Stop } from "@/types/trip-request";
import type { TripSimulationResult, ChargingStop, FaehrSegment } from "@/types";

/** Minimales TripSimulationResult mit den fuer buildTimePlan relevanten Feldern. */
function makeResult(
  overrides: Partial<TripSimulationResult> = {},
): TripSimulationResult {
  return {
    frames: [
      {
        zeitpunkt: "2026-08-15T08:00:00",
        position: [52.52, 13.405],
        soc_pct: 80,
        zustand: "FAHREN",
        geschwindigkeit_kmh: 110,
      },
      {
        zeitpunkt: "2026-08-15T14:00:00",
        position: [53.551, 9.993],
        soc_pct: 40,
        zustand: "FAHREN",
        geschwindigkeit_kmh: 110,
      },
    ],
    gesamt_distanz_km: 300,
    gesamt_fahrzeit_min: 360,
    gesamt_ladezeit_min: 0,
    gesamt_wartezeit_min: 0,
    start_soc_pct: 80,
    ziel_soc_pct: 40,
    charging_stops: [],
    waypoint_stops: [],
    erkannte_faehren: [],
    total_charging_cost: [],
    charging_stops_missing_pricing: 0,
    ...overrides,
  };
}

function makeChargingStop(overrides: Partial<ChargingStop> = {}): ChargingStop {
  return {
    name: "Tesla Supercharger - Dresden",
    station_id: "dresden-stop",
    position: [51.05, 13.74],
    ankunfts_soc_pct: 40,
    ziel_soc_pct: 80,
    ladedauer_s: 1500,
    energie_geladen_kwh: 25,
    ankunftszeit: "2026-08-15T10:00:00",
    abfahrtszeit: "2026-08-15T10:25:00",
    price_per_kwh: null,
    currency: null,
    estimated_cost: null,
    pricing_updated_utc: null,
    ...overrides,
  };
}

function makeFaehre(overrides: Partial<FaehrSegment> = {}): FaehrSegment {
  return {
    name: "Rødby (DK) - Puttgarden (D)",
    laenge_m: 22000,
    bbox_sw: [54.5, 11.22],
    bbox_no: [54.66, 11.36],
    abfahrt: null,
    ankunft: null,
    ...overrides,
  };
}

function makeStops(): Stop[] {
  return [
    { ...createEmptyStop(), address: "Berlin", position: [52.52, 13.405] },
    { ...createEmptyStop(), address: "Hamburg", position: [53.551, 9.993] },
  ];
}

describe("buildTimePlan", () => {
  it("includes an entry per user stop with role 'Stopp'", () => {
    const schedule = buildTimePlan(makeResult(), makeStops());
    const stopEintraege = schedule.filter((e) => e.art === "Stopp");
    expect(stopEintraege).toHaveLength(2);
    expect(stopEintraege[0].label).toBe("Berlin");
    expect(stopEintraege[1].label).toBe("Hamburg");
  });

  it("includes a charging stop with its real arrival/departure timestamps", () => {
    const result = makeResult({ charging_stops: [makeChargingStop()] });
    const schedule = buildTimePlan(result, makeStops());
    const ladehalt = schedule.find((e) => e.art === "Ladehalt");
    expect(ladehalt).toBeDefined();
    expect(ladehalt?.label).toBe("Tesla Supercharger - Dresden");
    expect(ladehalt?.arrival).toBe("2026-08-15T10:00:00");
    expect(ladehalt?.departure).toBe("2026-08-15T10:25:00");
  });

  it("propagates estimated cost and currency from a priced charging stop", () => {
    const result = makeResult({
      charging_stops: [
        makeChargingStop({
          price_per_kwh: 0.4,
          currency: "EUR",
          estimated_cost: 10,
          pricing_updated_utc: "2026-08-01T00:00:00",
        }),
      ],
    });
    const schedule = buildTimePlan(result, makeStops());
    const ladehalt = schedule.find((e) => e.art === "Ladehalt");
    expect(ladehalt?.estimatedCost).toBe(10);
    expect(ladehalt?.costCurrency).toBe("EUR");
  });

  it("leaves estimated cost null for a charging stop without cached pricing", () => {
    const result = makeResult({ charging_stops: [makeChargingStop()] });
    const schedule = buildTimePlan(result, makeStops());
    const ladehalt = schedule.find((e) => e.art === "Ladehalt");
    expect(ladehalt?.estimatedCost).toBeNull();
    expect(ladehalt?.costCurrency).toBeNull();
  });

  it("leaves estimated cost null for stop and ferry entries", () => {
    const result = makeResult({ erkannte_faehren: [makeFaehre()] });
    const schedule = buildTimePlan(result, makeStops());
    for (const eintrag of schedule) {
      if (eintrag.art !== "Ladehalt") {
        expect(eintrag.estimatedCost).toBeNull();
        expect(eintrag.costCurrency).toBeNull();
      }
    }
  });

  it("includes a pinned ferry with its scheduled departure/arrival", () => {
    const result = makeResult({
      erkannte_faehren: [
        makeFaehre({
          abfahrt: "2026-08-15T09:00:00",
          ankunft: "2026-08-15T09:45:00",
        }),
      ],
    });
    const schedule = buildTimePlan(result, makeStops());
    const faehre = schedule.find((e) => e.art === "Fähre");
    expect(faehre).toBeDefined();
    expect(faehre?.arrival).toBe("2026-08-15T09:00:00");
    expect(faehre?.departure).toBe("2026-08-15T09:45:00");
  });

  it("includes a detected ferry without a user schedule, with unknown timing if not near a frame", () => {
    const result = makeResult({ erkannte_faehren: [makeFaehre()] });
    const schedule = buildTimePlan(result, makeStops());
    const faehre = schedule.find((e) => e.art === "Fähre");
    expect(faehre).toBeDefined();
    expect(faehre?.arrival).toBeNull();
    expect(faehre?.departure).toBeNull();
  });

  it("includes an unpinned ferry with timing estimated from the nearest simulation frame", () => {
    const result = makeResult({
      erkannte_faehren: [
        makeFaehre({ bbox_sw: [52.5, 13.4], bbox_no: [52.54, 13.41] }),
      ],
    });
    const schedule = buildTimePlan(result, makeStops());
    const faehre = schedule.find((e) => e.art === "Fähre");
    expect(faehre).toBeDefined();
    expect(faehre?.arrival).toBe("2026-08-15T08:00:00");
    expect(faehre?.departure).toBe("2026-08-15T08:00:00");
  });

  it("sorts all entries chronologically by arrival (falling back to departure)", () => {
    const result = makeResult({
      charging_stops: [makeChargingStop()], // arrival 10:00
      erkannte_faehren: [
        makeFaehre({
          abfahrt: "2026-08-15T09:00:00",
          ankunft: "2026-08-15T09:45:00",
        }),
      ],
    });
    const schedule = buildTimePlan(result, makeStops());
    const arten = schedule.map((e) => e.art);
    // Start-Stopp (Abfahrt 08:00, keine Ankunft) -> Fähre (09:00) -> Ladehalt (10:00) -> Ziel-Stopp (14:00)
    expect(arten).toEqual(["Stopp", "Fähre", "Ladehalt", "Stopp"]);
  });
});

describe("buildTimePlan - Strecke/Dauer/SoC/Energie", () => {
  it("liefert am Start-Stopp null für Strecke/Dauer/Ankunfts-SoC, aber Abfahrts-SoC", () => {
    const schedule = buildTimePlan(makeResult(), makeStops());
    const start = schedule.find((e) => e.label === "Berlin");
    expect(start?.distanceSinceLastKm).toBeNull();
    expect(start?.durationSinceLastMin).toBeNull();
    expect(start?.ankunftsSocPct).toBeNull();
    expect(start?.abfahrtsSocPct).toBe(80);
  });

  it("berechnet Strecke/Dauer zum Ziel-Stopp aus den Frames und dessen Ankunfts-SoC", () => {
    const schedule = buildTimePlan(makeResult(), makeStops());
    const ziel = schedule.find((e) => e.label === "Hamburg");
    expect(ziel?.distanceSinceLastKm).toBeGreaterThan(200);
    expect(ziel?.distanceSinceLastKm).toBeLessThan(300);
    expect(ziel?.durationSinceLastMin).toBe(360);
    expect(ziel?.ankunftsSocPct).toBe(40);
    expect(ziel?.abfahrtsSocPct).toBeNull();
  });

  it("übernimmt SoC und geladene Energie eines Ladehalts exakt aus dem ChargingStop", () => {
    const result = makeResult({ charging_stops: [makeChargingStop()] });
    const schedule = buildTimePlan(result, makeStops());
    const ladehalt = schedule.find((e) => e.art === "Ladehalt");
    expect(ladehalt?.ankunftsSocPct).toBe(40);
    expect(ladehalt?.abfahrtsSocPct).toBe(80);
    expect(ladehalt?.energieGeladenKwh).toBe(25);
  });

  it("lässt geladene Energie bei Stopp- und Fähre-Einträgen null", () => {
    const result = makeResult({
      erkannte_faehren: [
        makeFaehre({
          abfahrt: "2026-08-15T09:00:00",
          ankunft: "2026-08-15T09:45:00",
        }),
      ],
    });
    const schedule = buildTimePlan(result, makeStops());
    for (const eintrag of schedule) {
      if (eintrag.art !== "Ladehalt") {
        expect(eintrag.energieGeladenKwh).toBeNull();
      }
    }
  });
});
