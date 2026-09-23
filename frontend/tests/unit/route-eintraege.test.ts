import { describe, it, expect } from "vitest";
import { buildRouteEntries } from "@/utils/route-eintraege";
import { cumulativeDistancesKm } from "@/utils/timing-utils";
import type { Stop } from "@/types/trip-request";
import type { TripSimulationResult, ChargingStop, FerrySegment } from "@/types";

function makeFrame(
  timestamp: string,
  lat: number,
  lon: number,
): TripSimulationResult["frames"][number] {
  return {
    timestamp,
    position: [lat, lon],
    socPct: 80,
    state: "FAHREN",
    speedKmh: 100,
  };
}

function makeStop(
  id: string,
  address: string,
  position: [number, number] | null,
  leaveAt?: string,
): Stop {
  return {
    id,
    address,
    position,
    ...(leaveAt !== undefined ? { leaveAt } : {}),
  };
}

function makeChargingStop(overrides: Partial<ChargingStop> = {}): ChargingStop {
  return {
    name: "Ladestation",
    stationId: "station_1",
    position: [52.52, 13.405],
    arrivalSocPct: 60,
    targetSocPct: 80,
    chargingDurationS: 1800,
    energyChargedKwh: 15,
    arrivalTime: "2025-01-01T08:45:00",
    departureTime: "2025-01-01T09:00:00",
    ...overrides,
  };
}

function makeFaehre(overrides: Partial<FerrySegment> = {}): FerrySegment {
  return {
    name: "Fähre Hela",
    lengthM: 5000,
    bboxSw: [52.51, 13.39],
    bboxNe: [52.54, 13.42],
    abfahrt: null,
    ankunft: null,
    ...overrides,
  };
}

function makeVermiedeneFaehre(
  name: string,
  bboxSw: [number, number],
  bboxNe: [number, number],
) {
  return {
    name,
    bboxSw,
    bboxNe,
  };
}

describe("buildRouteEntries", () => {
  describe("ohne frames", () => {
    it("gibt nur Stops in Originalreihenfolge zurueck", () => {
      const stops = [
        makeStop("1", "Start", [52.52, 13.405]),
        makeStop("2", "Ziel", [52.53, 13.41]),
      ];

      const result = buildRouteEntries({
        stops,
        frames: undefined,
        chargingStops: undefined,
        erkannteFaehren: undefined,
        avoidedFerries: [],
      });

      expect(result).toHaveLength(2);
      expect(result[0].art).toBe("Stopp");
      expect(result[0].stop.id).toBe("1");
      expect(result[1].art).toBe("Stopp");
      expect(result[1].stop.id).toBe("2");
      expect(result[0].sortKey).toBeNull();
      expect(result[1].sortKey).toBeNull();
      expect(result[0].timing).toEqual({
        arrival: null,
        departure: null,
        arrivalSocPct: null,
        departureSocPct: null,
      });
      expect(result[1].timing).toEqual({
        arrival: null,
        departure: null,
        arrivalSocPct: null,
        departureSocPct: null,
      });
    });
  });

  describe("mit frames", () => {
    it("sortiert Stops, Ladehalte und Fähren chronologisch", () => {
      const frames = [
        makeFrame("2025-01-01T08:00:00", 52.52, 13.405), // Startposition
        makeFrame("2025-01-01T09:00:00", 52.53, 13.41), // Zielposition
      ];

      const stops = [
        makeStop("1", "Start", [52.52, 13.405]),
        makeStop("2", "Ziel", [52.53, 13.41]),
      ];

      const chargingStops = [
        makeChargingStop({ arrivalTime: "2025-01-01T08:45:00" }),
      ];

      const recognizedFerries = [makeFaehre({ name: "Fähre A" })];

      const result = buildRouteEntries({
        stops,
        frames,
        chargingStops,
        erkannteFaehren: recognizedFerries,
        avoidedFerries: [],
      });

      // 2 Stops + 1 Ladehalt + 1 Fähre = 4 Einträge
      expect(result).toHaveLength(4);

      // Reihenfolge: Start (08:00), Fähre (BBox-Mitte in der Route, ca. 08:30), Ladehalt (08:45), Ziel (09:00)
      expect(result[0].art).toBe("Stopp");
      expect(result[0].stopIndex).toBe(0); // Start

      expect(result[1].art).toBe("Fähre");
      expect(result[1].faehre.name).toBe("Fähre A");

      expect(result[2].art).toBe("Ladehalt");
      expect(result[2].chargingStop.arrivalTime).toBe("2025-01-01T08:45:00");

      expect(result[3].art).toBe("Stopp");
      expect(result[3].stopIndex).toBe(1); // Ziel
    });

    it("schliesst Fähren aus, die in vermiedeneFaehren sind", () => {
      const frames = [
        makeFrame("2025-01-01T08:00:00", 52.52, 13.405),
        makeFrame("2025-01-01T09:00:00", 52.53, 13.41),
      ];

      const stops = [
        makeStop("1", "Start", [52.52, 13.405]),
        makeStop("2", "Ziel", [52.53, 13.41]),
      ];

      const recognizedFerries = [makeFaehre({ name: "Fähre A" })];

      // Die Fähre ist in vermiedeneFaehren
      const vermiedeneFaehren = [
        makeVermiedeneFaehre("Fähre A", [52.51, 13.39], [52.54, 13.42]),
      ];

      const result = buildRouteEntries({
        stops,
        frames,
        chargingStops: undefined,
        erkannteFaehren: recognizedFerries,
        avoidedFerries: vermiedeneFaehren,
      });

      // 2 Stops + 1 DrivingSegment dazwischen (1h Fahrzeit), keine Fähre!
      expect(result).toHaveLength(3);
      expect(result[0].art).toBe("Stopp");
      expect(result[1].art).toBe("Fahrsegment");
      expect(result[2].art).toBe("Stopp");
    });

    it("inkludiert Fähren, die NICHT in vermiedeneFaehren sind", () => {
      const frames = [
        makeFrame("2025-01-01T08:00:00", 52.52, 13.405),
        makeFrame("2025-01-01T09:00:00", 52.53, 13.41),
      ];

      const stops = [
        makeStop("1", "Start", [52.52, 13.405]),
        makeStop("2", "Ziel", [52.53, 13.41]),
      ];

      const recognizedFerries = [makeFaehre({ name: "Fähre A" })];

      // ANDERE Fähre ist vermieden, aber "Fähre A" ist nicht enthalten
      const vermiedeneFaehren = [
        makeVermiedeneFaehre("Andere Fähre", [54.0, 12.0], [54.5, 12.5]),
      ];

      const result = buildRouteEntries({
        stops,
        frames,
        chargingStops: undefined,
        erkannteFaehren: recognizedFerries,
        avoidedFerries: vermiedeneFaehren,
      });

      // 2 Stops + 1 Fähre = 3 Einträge
      expect(result).toHaveLength(3);
      expect(result.find((e) => e.art === "Fähre")?.faehre.name).toBe(
        "Fähre A",
      );
    });
  });

  describe("timing-Feld", () => {
    it("übernimmt Ankunft/Abfahrt für Stopps aus estimateWaypointTimings", () => {
      const frames = [
        makeFrame("2025-01-01T08:00:00", 52.52, 13.405),
        makeFrame("2025-01-01T09:00:00", 53.0, 13.9),
      ];
      const stops = [
        makeStop("1", "Start", [52.52, 13.405]),
        makeStop("2", "Ziel", [53.0, 13.9]),
      ];

      const result = buildRouteEntries({
        stops,
        frames,
        chargingStops: undefined,
        erkannteFaehren: undefined,
        avoidedFerries: [],
      });

      const start = result.find((e) => e.art === "Stopp" && e.stopIndex === 0);
      const ziel = result.find((e) => e.art === "Stopp" && e.stopIndex === 1);
      // Start hat keine Ankunft (erster Frame ist die Abfahrt)
      expect(start?.timing).toEqual({
        arrival: null,
        departure: "2025-01-01T08:00:00",
        arrivalSocPct: null,
        departureSocPct: 80,
      });
      // Ziel hat keine Abfahrt (letzter Frame ist die Ankunft)
      expect(ziel?.timing).toEqual({
        arrival: "2025-01-01T09:00:00",
        departure: null,
        arrivalSocPct: 80,
        departureSocPct: null,
      });
    });

    it("übernimmt Ankunfts-/Abfahrtszeit für Ladehalte 1:1 aus dem ChargingStop", () => {
      const frames = [
        makeFrame("2025-01-01T08:00:00", 52.52, 13.405),
        makeFrame("2025-01-01T09:00:00", 52.53, 13.41),
      ];
      const stops = [
        makeStop("1", "Start", [52.52, 13.405]),
        makeStop("2", "Ziel", [52.53, 13.41]),
      ];
      const chargingStops = [
        makeChargingStop({
          arrivalTime: "2025-01-01T08:30:00",
          departureTime: "2025-01-01T08:50:00",
        }),
      ];

      const result = buildRouteEntries({
        stops,
        frames,
        chargingStops,
        erkannteFaehren: undefined,
        avoidedFerries: [],
      });

      const ladehalt = result.find((e) => e.art === "Ladehalt");
      expect(ladehalt?.timing).toEqual({
        arrival: "2025-01-01T08:30:00",
        departure: "2025-01-01T08:50:00",
        arrivalSocPct: 60,
        departureSocPct: 80,
      });
    });

    it("leitet die Fähren-Timing aus estimatePositionTiming (BBox-Mitte) ab", () => {
      const frames = [
        makeFrame("2025-01-01T08:00:00", 52.52, 13.405),
        makeFrame("2025-01-01T08:30:00", 52.525, 13.405), // nahe der Fähren-BBox-Mitte
        makeFrame("2025-01-01T09:00:00", 52.53, 13.41),
      ];
      const stops = [
        makeStop("1", "Start", [52.52, 13.405]),
        makeStop("2", "Ziel", [52.53, 13.41]),
      ];
      const recognizedFerries = [
        makeFaehre({
          name: "Fähre A",
          bboxSw: [52.51, 13.395],
          bboxNe: [52.54, 13.415],
        }),
      ];

      const result = buildRouteEntries({
        stops,
        frames,
        chargingStops: undefined,
        erkannteFaehren: recognizedFerries,
        avoidedFerries: [],
      });

      const faehre = result.find((e) => e.art === "Fähre");
      expect(faehre?.timing.arrival ?? faehre?.timing.departure).toBe(
        faehre?.sortKey,
      );
    });
  });

  describe("Fahrsegmente zwischen Einträgen", () => {
    it("fügt zwischen zwei Einträgen mit unterschiedlichen Verbindungszeitpunkten ein DrivingSegment ein", () => {
      const frames = [
        makeFrame("2025-01-01T08:00:00", 52.0, 13.0),
        makeFrame("2025-01-01T08:30:00", 52.0, 13.1),
        makeFrame("2025-01-01T09:00:00", 52.0, 13.2),
      ];
      const stops = [
        makeStop("1", "Start", [52.0, 13.0]),
        makeStop("2", "Ziel", [52.0, 13.2]),
      ];

      const result = buildRouteEntries({
        stops,
        frames,
        chargingStops: undefined,
        erkannteFaehren: undefined,
        avoidedFerries: [],
      });

      expect(result).toHaveLength(3);
      expect(result[0].art).toBe("Stopp");
      expect(result[1].art).toBe("Fahrsegment");
      expect(result[2].art).toBe("Stopp");

      const fahrsegment = result[1];
      if (fahrsegment.art !== "Fahrsegment") throw new Error("unreachable");
      expect(fahrsegment.vonIso).toBe("2025-01-01T08:00:00");
      expect(fahrsegment.bisIso).toBe("2025-01-01T09:00:00");
      expect(fahrsegment.sortKey).toBe("2025-01-01T08:00:00");
      expect(fahrsegment.durationMin).toBe(60);
      const erwarteteDistanzKm = cumulativeDistancesKm(frames)[2];
      expect(fahrsegment.distanceKm).toBeCloseTo(erwarteteDistanzKm, 5);
    });

    it("überspringt ein DrivingSegment, wenn beide Verbindungszeitpunkte identisch sind", () => {
      const frames = [
        makeFrame("2025-01-01T08:00:00", 52.0, 13.0),
        makeFrame("2025-01-01T08:30:00", 52.0, 13.1),
        makeFrame("2025-01-01T09:00:00", 52.0, 13.2),
      ];
      const stops = [
        makeStop("1", "Start", [52.0, 13.0]),
        makeStop("2", "Ziel", [52.0, 13.2]),
      ];
      // Ankunft exakt gleich der Start-Abfahrt -> zwischen Start und Ladehalt
      // liegt kein DrivingSegment (von === bis).
      const chargingStops = [
        makeChargingStop({
          arrivalTime: "2025-01-01T08:00:00",
          departureTime: "2025-01-01T08:05:00",
        }),
      ];

      const result = buildRouteEntries({
        stops,
        frames,
        chargingStops,
        erkannteFaehren: undefined,
        avoidedFerries: [],
      });

      // Start, Ladehalt (kein DrivingSegment davor), DrivingSegment, Ziel
      expect(result).toHaveLength(4);
      expect(result.map((e) => e.art)).toEqual([
        "Stopp",
        "Ladehalt",
        "Fahrsegment",
        "Stopp",
      ]);
      const fahrsegment = result[2];
      if (fahrsegment.art !== "Fahrsegment") throw new Error("unreachable");
      expect(fahrsegment.vonIso).toBe("2025-01-01T08:05:00");
      expect(fahrsegment.bisIso).toBe("2025-01-01T09:00:00");
    });

    it("unterdrückt Fahrsegmente unterhalb der Mindestdauer", () => {
      const frames = [
        makeFrame("2025-01-01T08:00:00", 52.0, 13.0),
        makeFrame("2025-01-01T08:00:30", 52.0, 13.0005),
      ];
      const stops = [
        makeStop("1", "Start", [52.0, 13.0]),
        makeStop("2", "Ziel", [52.0, 13.0005]),
      ];

      const result = buildRouteEntries({
        stops,
        frames,
        chargingStops: undefined,
        erkannteFaehren: undefined,
        avoidedFerries: [],
      });

      expect(result).toHaveLength(2);
      expect(result.every((e) => e.art === "Stopp")).toBe(true);
    });
  });

  describe("Tageswechsel-Handling", () => {
    it("fügt einen Tagestrenner ein, wenn der Zeitpunkt-Sprung zu kurz für ein DrivingSegment ist, aber der Tag wechselt", () => {
      const frames = [
        makeFrame("2025-01-01T23:59:50", 52.0, 13.0),
        makeFrame("2025-01-02T00:00:10", 52.0, 13.0002),
      ];
      const stops = [
        makeStop("1", "Start", [52.0, 13.0]),
        makeStop("2", "Ziel", [52.0, 13.0002]),
      ];

      const result = buildRouteEntries({
        stops,
        frames,
        chargingStops: undefined,
        erkannteFaehren: undefined,
        avoidedFerries: [],
      });

      expect(result.map((e) => e.art)).toEqual([
        "Stopp",
        "Tagestrenner",
        "Stopp",
      ]);
      const trenner = result[1];
      if (trenner.art !== "Tagestrenner") throw new Error("unreachable");
      expect(trenner.vonIso).toBe("2025-01-01T23:59:50");
      expect(trenner.bisIso).toBe("2025-01-02T00:00:10");
    });

    it("erzeugt KEINEN separaten Tagestrenner, wenn ein DrivingSegment selbst den Tag wechselt", () => {
      const frames = [
        makeFrame("2025-01-01T23:00:00", 52.0, 13.0),
        makeFrame("2025-01-01T23:30:00", 52.0, 13.1),
        makeFrame("2025-01-02T00:00:00", 52.0, 13.2),
      ];
      const stops = [
        makeStop("1", "Start", [52.0, 13.0]),
        makeStop("2", "Ziel", [52.0, 13.2]),
      ];

      const result = buildRouteEntries({
        stops,
        frames,
        chargingStops: undefined,
        erkannteFaehren: undefined,
        avoidedFerries: [],
      });

      // Nur EIN verbindender Eintrag (DrivingSegment) trägt den Tageswechsel -
      // kein zusätzlicher Tagestrenner daneben.
      expect(result.map((e) => e.art)).toEqual([
        "Stopp",
        "Fahrsegment",
        "Stopp",
      ]);
      const fahrsegment = result[1];
      if (fahrsegment.art !== "Fahrsegment") throw new Error("unreachable");
      expect(fahrsegment.vonIso).toBe("2025-01-01T23:00:00");
      expect(fahrsegment.bisIso).toBe("2025-01-02T00:00:00");
    });

    it("erzeugt keinen Connector-Eintrag für einen Tageswechsel INNERHALB eines Ladehalts", () => {
      // Ladehalt beginnt 23:50 und endet 00:10 (nächster Tag) - der
      // Tageswechsel liegt hier INNERHALB des Ladehalts selbst, nicht
      // zwischen zwei verschiedenen Einträgen. route-eintraege.ts vergleicht
      // für Connectoren nur `verbindungsZeitpunkt`e VERSCHIEDENER Einträge,
      // nie Ankunft/Abfahrt DESSELBEN Eintrags - hier darf also kein
      // Tagestrenner/DrivingSegment entstehen (die Zeit-Badges des Ladehalts
      // selbst zeigen das Datum an, siehe TripPlannerForm.tsx).
      const frames = [
        makeFrame("2025-01-01T23:00:00", 52.0, 13.0),
        makeFrame("2025-01-02T01:00:00", 52.0, 13.2),
      ];
      const stops = [
        makeStop("1", "Start", [52.0, 13.0]),
        makeStop("2", "Ziel", [52.0, 13.2]),
      ];
      const chargingStops = [
        makeChargingStop({
          arrivalTime: "2025-01-01T23:50:00",
          departureTime: "2025-01-02T00:10:00",
        }),
      ];

      const result = buildRouteEntries({
        stops,
        frames,
        chargingStops,
        erkannteFaehren: undefined,
        avoidedFerries: [],
      });

      // Start -[DrivingSegment]-> Ladehalt -[DrivingSegment]-> Ziel, kein
      // zusätzlicher Tagestrenner rund um den Ladehalt selbst.
      expect(result.map((e) => e.art)).toEqual([
        "Stopp",
        "Fahrsegment",
        "Ladehalt",
        "Fahrsegment",
        "Stopp",
      ]);
    });
  });
});
