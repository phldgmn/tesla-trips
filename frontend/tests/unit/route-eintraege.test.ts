import { describe, it, expect } from "vitest";
import { buildRouteEintraege } from "@/utils/route-eintraege";
import type { Stop } from "@/types/trip-request";
import type { TripSimulationResult, ChargingStop, FaehrSegment } from "@/types";

function makeFrame(
  zeitpunkt: string,
  lat: number,
  lon: number,
): TripSimulationResult["frames"][number] {
  return {
    zeitpunkt,
    position: [lat, lon],
    soc_pct: 80,
    zustand: "FAHREN",
    geschwindigkeit_kmh: 100,
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
    station_id: "station_1",
    position: [52.52, 13.405],
    ankunfts_soc_pct: 60,
    ziel_soc_pct: 80,
    ladedauer_s: 1800,
    energie_geladen_kwh: 15,
    ankunftszeit: "2025-01-01T08:45:00",
    abfahrtszeit: "2025-01-01T09:00:00",
    ...overrides,
  };
}

function makeFaehre(overrides: Partial<FaehrSegment> = {}): FaehrSegment {
  return {
    name: "Fähre Hela",
    laenge_m: 5000,
    bbox_sw: [52.51, 13.39],
    bbox_no: [52.54, 13.42],
    abfahrt: null,
    ankunft: null,
    ...overrides,
  };
}

function makeVermiedeneFaehre(
  name: string,
  bbox_sw: [number, number],
  bbox_no: [number, number],
) {
  return {
    name,
    bbox_sw,
    bbox_no,
  };
}

describe("buildRouteEintraege", () => {
  describe("ohne frames", () => {
    it("gibt nur Stops in Originalreihenfolge zurueck", () => {
      const stops = [
        makeStop("1", "Start", [52.52, 13.405]),
        makeStop("2", "Ziel", [52.53, 13.41]),
      ];

      const result = buildRouteEintraege({
        stops,
        frames: undefined,
        chargingStops: undefined,
        erkannteFaehren: undefined,
        vermiedeneFaehren: [],
      });

      expect(result).toHaveLength(2);
      expect(result[0].art).toBe("Stopp");
      expect(result[0].stop.id).toBe("1");
      expect(result[1].art).toBe("Stopp");
      expect(result[1].stop.id).toBe("2");
      expect(result[0].sortKey).toBeNull();
      expect(result[1].sortKey).toBeNull();
      expect(result[0].timing).toEqual({ arrival: null, departure: null });
      expect(result[1].timing).toEqual({ arrival: null, departure: null });
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
        makeChargingStop({ ankunftszeit: "2025-01-01T08:45:00" }),
      ];

      const recognizedFerries = [makeFaehre({ name: "Fähre A" })];

      const result = buildRouteEintraege({
        stops,
        frames,
        chargingStops,
        erkannteFaehren: recognizedFerries,
        vermiedeneFaehren: [],
      });

      // 2 Stops + 1 Ladehalt + 1 Fähre = 4 Einträge
      expect(result).toHaveLength(4);

      // Reihenfolge: Start (08:00), Fähre (BBox-Mitte in der Route, ca. 08:30), Ladehalt (08:45), Ziel (09:00)
      expect(result[0].art).toBe("Stopp");
      expect(result[0].stopIndex).toBe(0); // Start

      expect(result[1].art).toBe("Fähre");
      expect(result[1].faehre.name).toBe("Fähre A");

      expect(result[2].art).toBe("Ladehalt");
      expect(result[2].chargingStop.ankunftszeit).toBe("2025-01-01T08:45:00");

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

      const result = buildRouteEintraege({
        stops,
        frames,
        chargingStops: undefined,
        erkannteFaehren: recognizedFerries,
        vermiedeneFaehren,
      });

      // Nur 2 Stops (keine Fähre!)
      expect(result).toHaveLength(2);
      expect(result.every((e) => e.art === "Stopp")).toBe(true);
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

      const result = buildRouteEintraege({
        stops,
        frames,
        chargingStops: undefined,
        erkannteFaehren: recognizedFerries,
        vermiedeneFaehren,
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

      const result = buildRouteEintraege({
        stops,
        frames,
        chargingStops: undefined,
        erkannteFaehren: undefined,
        vermiedeneFaehren: [],
      });

      const start = result.find((e) => e.art === "Stopp" && e.stopIndex === 0);
      const ziel = result.find((e) => e.art === "Stopp" && e.stopIndex === 1);
      // Start hat keine Ankunft (erster Frame ist die Abfahrt)
      expect(start?.timing).toEqual({
        arrival: null,
        departure: "2025-01-01T08:00:00",
      });
      // Ziel hat keine Abfahrt (letzter Frame ist die Ankunft)
      expect(ziel?.timing).toEqual({
        arrival: "2025-01-01T09:00:00",
        departure: null,
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
          ankunftszeit: "2025-01-01T08:30:00",
          abfahrtszeit: "2025-01-01T08:50:00",
        }),
      ];

      const result = buildRouteEintraege({
        stops,
        frames,
        chargingStops,
        erkannteFaehren: undefined,
        vermiedeneFaehren: [],
      });

      const ladehalt = result.find((e) => e.art === "Ladehalt");
      expect(ladehalt?.timing).toEqual({
        arrival: "2025-01-01T08:30:00",
        departure: "2025-01-01T08:50:00",
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
          bbox_sw: [52.51, 13.395],
          bbox_no: [52.54, 13.415],
        }),
      ];

      const result = buildRouteEintraege({
        stops,
        frames,
        chargingStops: undefined,
        erkannteFaehren: recognizedFerries,
        vermiedeneFaehren: [],
      });

      const faehre = result.find((e) => e.art === "Fähre");
      expect(faehre?.timing.arrival ?? faehre?.timing.departure).toBe(
        faehre?.sortKey,
      );
    });
  });
});
