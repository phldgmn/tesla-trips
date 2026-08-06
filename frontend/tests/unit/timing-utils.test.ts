import { describe, it, expect } from "vitest";
import {
  estimateWaypointTimings,
  type WaypointTiming,
} from "@/utils/timing-utils";
import type { TripSimulationResult } from "@/types";
import type { Stop } from "@/types/trip-request";

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

describe("estimateWaypointTimings", () => {
  describe("3-Stopp-Route mit Zwischenstopp auf der Route", () => {
    const start = makeStop("start", "Berlin", [52.52, 13.405]);
    const stopover = makeStop("stop", "Dresden", [51.05, 13.74]);
    const end = makeStop("end", "München", [48.14, 11.58]);

    // Simuliere eine lineare Route Berlin → Dresden → München
    const frames = [
      // Berlin (Start) – Nahbereich
      makeFrame("2025-06-01T08:00:00", 52.519, 13.404),
      makeFrame("2025-06-01T08:05:00", 52.4, 13.45),
      makeFrame("2025-06-01T08:30:00", 51.9, 13.6),
      // Dresden – Nahbereich (~0.5 km)
      makeFrame("2025-06-01T09:00:00", 51.048, 13.739),
      makeFrame("2025-06-01T09:05:00", 51.047, 13.738),
      // Dresden verlassen
      makeFrame("2025-06-01T09:30:00", 51.2, 13.2),
      makeFrame("2025-06-01T10:00:00", 50.5, 12.5),
      makeFrame("2025-06-01T10:30:00", 49.5, 12.0),
      makeFrame("2025-06-01T11:00:00", 48.5, 11.8),
      // München – Nahbereich
      makeFrame("2025-06-01T11:30:00", 48.14, 11.58),
    ];

    const result: TripSimulationResult = {
      frames,
      gesamt_distanz_km: 585,
      gesamt_fahrzeit_min: 210,
      gesamt_ladezeit_min: 0,
      start_soc_pct: 95,
      ziel_soc_pct: 15,
    };

    const timings = estimateWaypointTimings(result, [start, stopover, end]);

    it("soll 3 Einträge zurückgeben", () => {
      expect(timings).toHaveLength(3);
    });

    it("soll am Start arrival=null und departure=ersten Frame setzen", () => {
      expect(timings[0].stopId).toBe("start");
      expect(timings[0].arrival).toBeNull();
      expect(timings[0].departure).toBe("2025-06-01T08:00:00");
    });

    it("soll am Zwischenstopp nicht-null arrival/departure liefern", () => {
      expect(timings[1].stopId).toBe("stop");
      expect(timings[1].arrival).not.toBeNull();
      expect(timings[1].departure).not.toBeNull();
      // Der Dresden-Cluster umfasst die Frames 3–4 (Index 3–4)
      expect(timings[1].arrival).toBe("2025-06-01T09:00:00");
      expect(timings[1].departure).toBe("2025-06-01T09:05:00");
    });

    it("soll am Ziel departure=null und arrival=letzten Frame setzen", () => {
      expect(timings[2].stopId).toBe("end");
      expect(timings[2].arrival).toBe("2025-06-01T11:30:00");
      expect(timings[2].departure).toBeNull();
    });
  });

  describe("Zwischenstopp weit entfernt von der Route (>2 km)", () => {
    const start = makeStop("start", "Berlin", [52.52, 13.405]);
    const farStop = makeStop("far", "Weit weg", [50.0, 10.0]); // ~350 km von Berlin, weit abseits der Route nach Hamburg
    const end = makeStop("end", "Hamburg", [53.55, 10.0]);

    const frames = [
      makeFrame("2025-06-01T08:00:00", 52.52, 13.405),
      makeFrame("2025-06-01T08:30:00", 52.8, 12.0),
      makeFrame("2025-06-01T09:00:00", 53.0, 11.0),
      makeFrame("2025-06-01T09:30:00", 53.2, 10.5),
      makeFrame("2025-06-01T10:00:00", 53.55, 10.0),
    ];

    const result: TripSimulationResult = {
      frames,
      gesamt_distanz_km: 280,
      gesamt_fahrzeit_min: 120,
      gesamt_ladezeit_min: 0,
      start_soc_pct: 100,
      ziel_soc_pct: 40,
    };

    const timings = estimateWaypointTimings(result, [start, farStop, end]);

    it("soll für den weit entfernten Punkt arrival=null und departure=null liefern", () => {
      expect(timings[1].stopId).toBe("far");
      expect(timings[1].arrival).toBeNull();
      expect(timings[1].departure).toBeNull();
    });

    it("soll Start und Ende korrekt behandeln", () => {
      expect(timings[0].arrival).toBeNull();
      expect(timings[0].departure).toBe("2025-06-01T08:00:00");
      expect(timings[2].arrival).toBe("2025-06-01T10:00:00");
      expect(timings[2].departure).toBeNull();
    });
  });

  describe("Leeres frames-Array", () => {
    it("soll für jeden Stopp null/null liefern und nicht werfen", () => {
      const stops: Stop[] = [
        makeStop("a", "Start", [0, 0]),
        makeStop("b", "Stopp 1", [1, 1]),
        makeStop("c", "Stopp 2", [2, 2]),
        makeStop("d", "Ziel", [3, 3]),
      ];
      const result: TripSimulationResult = {
        frames: [],
        gesamt_distanz_km: 0,
        gesamt_fahrzeit_min: 0,
        gesamt_ladezeit_min: 0,
        start_soc_pct: 100,
        ziel_soc_pct: 100,
      };

      const timings = estimateWaypointTimings(result, stops);
      expect(timings).toHaveLength(4);
      for (const t of timings) {
        expect(t.arrival).toBeNull();
        expect(t.departure).toBeNull();
      }
    });
  });

  describe("Zwischenstopp mit position=null (nicht aufgelöst)", () => {
    it("soll null/null liefern und nicht werfen", () => {
      const start = makeStop("start", "Berlin", [52.52, 13.405]);
      const unresolved = makeStop("mid", "Irgendwo", null);
      const end = makeStop("end", "Hamburg", [53.55, 10.0]);

      const frames = [
        makeFrame("2025-06-01T08:00:00", 52.52, 13.405),
        makeFrame("2025-06-01T08:30:00", 52.8, 12.0),
        makeFrame("2025-06-01T09:00:00", 53.0, 11.0),
        makeFrame("2025-06-01T09:30:00", 53.2, 10.5),
        makeFrame("2025-06-01T10:00:00", 53.55, 10.0),
      ];

      const result: TripSimulationResult = {
        frames,
        gesamt_distanz_km: 280,
        gesamt_fahrzeit_min: 120,
        gesamt_ladezeit_min: 0,
        start_soc_pct: 100,
        ziel_soc_pct: 40,
      };

      const timings = estimateWaypointTimings(result, [start, unresolved, end]);

      expect(timings).toHaveLength(3);
      expect(timings[0].stopId).toBe("start");
      expect(timings[0].arrival).toBeNull();
      expect(timings[0].departure).toBe("2025-06-01T08:00:00");

      // Unaufgelöster Zwischenstopp: beides null
      expect(timings[1].stopId).toBe("mid");
      expect(timings[1].arrival).toBeNull();
      expect(timings[1].departure).toBeNull();

      expect(timings[2].stopId).toBe("end");
      expect(timings[2].arrival).toBe("2025-06-01T10:00:00");
      expect(timings[2].departure).toBeNull();
    });
  });

  describe("Zwischenstopp auf der Route (ehemals charging)", () => {
    it("soll arrival/departure korrekt berechnen", () => {
      const start = makeStop("start", "Start", [48.5, 9.5]);
      const point = makeStop("ch", "Supercharger Stuttgart", [48.78, 9.18]);
      const end = makeStop("end", "Ziel", [48.9, 9.0]);
      const frames = [
        makeFrame("2025-06-01T12:00:00", 48.5, 9.5),
        makeFrame("2025-06-01T12:10:00", 48.7, 9.2),
        makeFrame("2025-06-01T12:15:00", 48.78, 9.18), // exakt am Punkt
        makeFrame("2025-06-01T12:20:00", 48.78, 9.181), // noch nah dran
        makeFrame("2025-06-01T12:30:00", 48.9, 9.0), // verlässt Cluster
      ];
      const result: TripSimulationResult = {
        frames,
        gesamt_distanz_km: 100,
        gesamt_fahrzeit_min: 30,
        gesamt_ladezeit_min: 10,
        start_soc_pct: 90,
        ziel_soc_pct: 60,
      };

      const timings = estimateWaypointTimings(result, [start, point, end]);
      expect(timings[1].stopId).toBe("ch");
      expect(timings[1].arrival).toBe("2025-06-01T12:15:00");
      expect(timings[1].departure).toBe("2025-06-01T12:20:00");
    });
  });
});
