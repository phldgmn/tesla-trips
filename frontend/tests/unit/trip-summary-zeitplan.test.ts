import { describe, it, expect } from "vitest";
import { buildTimePlan } from "@/components/TripSummary";
import { createEmptyStop } from "@/types/trip-request";
import type { Stop } from "@/types/trip-request";
import type { TripSimulationResult, ChargingStop, FerrySegment } from "@/types";

/** Minimales TripSimulationResult mit den fuer buildTimePlan relevanten Feldern. */
function makeResult(
  overrides: Partial<TripSimulationResult> = {},
): TripSimulationResult {
  return {
    frames: [
      {
        timestamp: "2026-08-15T08:00:00",
        position: [52.52, 13.405],
        socPct: 80,
        state: "FAHREN",
        speedKmh: 110,
      },
      {
        timestamp: "2026-08-15T14:00:00",
        position: [53.551, 9.993],
        socPct: 40,
        state: "FAHREN",
        speedKmh: 110,
      },
    ],
    totalDistanceKm: 300,
    totalDrivingTimeMin: 360,
    totalChargingTimeMin: 0,
    totalWaitingTimeMin: 0,
    startSocPct: 80,
    targetSocPct: 40,
    chargingStops: [],
    waypointStops: [],
    detectedFerries: [],
    totalChargingCost: [],
    chargingStopsMissingPricing: 0,
    ...overrides,
  };
}

function makeChargingStop(overrides: Partial<ChargingStop> = {}): ChargingStop {
  return {
    name: "Tesla Supercharger - Dresden",
    stationId: "dresden-stop",
    position: [51.05, 13.74],
    arrivalSocPct: 40,
    targetSocPct: 80,
    chargingDurationS: 1500,
    energyChargedKwh: 25,
    arrivalTime: "2026-08-15T10:00:00",
    departureTime: "2026-08-15T10:25:00",
    pricePerKwh: null,
    currency: null,
    estimatedCost: null,
    pricingUpdatedUtc: null,
    ...overrides,
  };
}

function makeFerry(overrides: Partial<FerrySegment> = {}): FerrySegment {
  return {
    name: "Rødby (DK) - Puttgarden (D)",
    lengthM: 22000,
    bboxSw: [54.5, 11.22],
    bboxNe: [54.66, 11.36],
    departure: null,
    arrival: null,
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
    const stopEntries = schedule.filter((e) => e.art === "Stopp");
    expect(stopEntries).toHaveLength(2);
    expect(stopEntries[0].label).toBe("Berlin");
    expect(stopEntries[1].label).toBe("Hamburg");
  });

  it("includes a charging stop with its real arrival/departure timestamps", () => {
    const result = makeResult({ chargingStops: [makeChargingStop()] });
    const schedule = buildTimePlan(result, makeStops());
    const ladehalt = schedule.find((e) => e.art === "Ladehalt");
    expect(ladehalt).toBeDefined();
    expect(ladehalt?.label).toBe("Tesla Supercharger - Dresden");
    expect(ladehalt?.arrival).toBe("2026-08-15T10:00:00");
    expect(ladehalt?.departure).toBe("2026-08-15T10:25:00");
  });

  it("propagates estimated cost and currency from a priced charging stop", () => {
    const result = makeResult({
      chargingStops: [
        makeChargingStop({
          pricePerKwh: 0.4,
          currency: "EUR",
          estimatedCost: 10,
          pricingUpdatedUtc: "2026-08-01T00:00:00",
        }),
      ],
    });
    const schedule = buildTimePlan(result, makeStops());
    const ladehalt = schedule.find((e) => e.art === "Ladehalt");
    expect(ladehalt?.estimatedCost).toBe(10);
    expect(ladehalt?.costCurrency).toBe("EUR");
  });

  it("leaves estimated cost null for a charging stop without cached pricing", () => {
    const result = makeResult({ chargingStops: [makeChargingStop()] });
    const schedule = buildTimePlan(result, makeStops());
    const ladehalt = schedule.find((e) => e.art === "Ladehalt");
    expect(ladehalt?.estimatedCost).toBeNull();
    expect(ladehalt?.costCurrency).toBeNull();
  });

  it("leaves estimated cost null for stop and ferry entries", () => {
    const result = makeResult({ detectedFerries: [makeFerry()] });
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
      detectedFerries: [
        makeFerry({
          departure: "2026-08-15T09:00:00",
          arrival: "2026-08-15T09:45:00",
        }),
      ],
    });
    const schedule = buildTimePlan(result, makeStops());
    const ferry = schedule.find((e) => e.art === "Fähre");
    expect(ferry).toBeDefined();
    expect(ferry?.arrival).toBe("2026-08-15T09:00:00");
    expect(ferry?.departure).toBe("2026-08-15T09:45:00");
  });

  it("includes a detected ferry without a user schedule, with unknown timing if not near a frame", () => {
    const result = makeResult({ detectedFerries: [makeFerry()] });
    const schedule = buildTimePlan(result, makeStops());
    const ferry = schedule.find((e) => e.art === "Fähre");
    expect(ferry).toBeDefined();
    expect(ferry?.arrival).toBeNull();
    expect(ferry?.departure).toBeNull();
  });

  it("includes an unpinned ferry with timing estimated from the nearest simulation frame", () => {
    const result = makeResult({
      detectedFerries: [
        makeFerry({ bboxSw: [52.5, 13.4], bboxNe: [52.54, 13.41] }),
      ],
    });
    const schedule = buildTimePlan(result, makeStops());
    const ferry = schedule.find((e) => e.art === "Fähre");
    expect(ferry).toBeDefined();
    expect(ferry?.arrival).toBe("2026-08-15T08:00:00");
    expect(ferry?.departure).toBe("2026-08-15T08:00:00");
  });

  it("sorts all entries chronologically by arrival (falling back to departure)", () => {
    const result = makeResult({
      chargingStops: [makeChargingStop()], // arrival 10:00
      detectedFerries: [
        makeFerry({
          departure: "2026-08-15T09:00:00",
          arrival: "2026-08-15T09:45:00",
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
    expect(start?.arrivalSocPct).toBeNull();
    expect(start?.departureSocPct).toBe(80);
  });

  it("berechnet Strecke/Dauer zum Ziel-Stopp aus den Frames und dessen Ankunfts-SoC", () => {
    const schedule = buildTimePlan(makeResult(), makeStops());
    const destination = schedule.find((e) => e.label === "Hamburg");
    expect(destination?.distanceSinceLastKm).toBeGreaterThan(200);
    expect(destination?.distanceSinceLastKm).toBeLessThan(300);
    expect(destination?.durationSinceLastMin).toBe(360);
    expect(destination?.arrivalSocPct).toBe(40);
    expect(destination?.departureSocPct).toBeNull();
  });

  it("übernimmt SoC und geladene Energie eines Ladehalts exakt aus dem ChargingStop", () => {
    const result = makeResult({ chargingStops: [makeChargingStop()] });
    const schedule = buildTimePlan(result, makeStops());
    const ladehalt = schedule.find((e) => e.art === "Ladehalt");
    expect(ladehalt?.arrivalSocPct).toBe(40);
    expect(ladehalt?.departureSocPct).toBe(80);
    expect(ladehalt?.energyChargedKwh).toBe(25);
  });

  it("lässt geladene Energie bei Stopp- und Fähre-Einträgen null", () => {
    const result = makeResult({
      detectedFerries: [
        makeFerry({
          departure: "2026-08-15T09:00:00",
          arrival: "2026-08-15T09:45:00",
        }),
      ],
    });
    const schedule = buildTimePlan(result, makeStops());
    for (const eintrag of schedule) {
      if (eintrag.art !== "Ladehalt") {
        expect(eintrag.energyChargedKwh).toBeNull();
      }
    }
  });
});
