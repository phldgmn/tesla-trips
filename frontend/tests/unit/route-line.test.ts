import { describe, it, expect } from "vitest";
import { buildSplicedRoute } from "@/utils/route-line";
import { haversineDistanceM } from "@/utils/geo-utils";

describe("buildSplicedRoute", () => {
  it("returns an empty result for an empty route geometry", () => {
    const result = buildSplicedRoute([], [], []);
    expect(result).toEqual({ coordinates: [], totalDistanceM: 0, samples: [] });
  });

  it("converts (lat, lon) route points to [lng, lat] with no charging stops", () => {
    const route: [number, number][] = [
      [52.5, 13.4],
      [52.6, 13.5],
      [52.7, 13.6],
    ];
    const result = buildSplicedRoute(route, [], []);
    expect(result.coordinates).toEqual([
      [13.4, 52.5],
      [13.5, 52.6],
      [13.6, 52.7],
    ]);
  });

  it("computes totalDistanceM as the route length when there are no detours", () => {
    const route: [number, number][] = [
      [52.5, 13.4],
      [52.6, 13.5],
      [52.7, 13.6],
    ];
    const expected =
      haversineDistanceM(route[0], route[1]) +
      haversineDistanceM(route[1], route[2]);
    const result = buildSplicedRoute(route, [], []);
    expect(result.totalDistanceM).toBeCloseTo(expected, 3);
  });

  it("positions FAHREN frame samples by their route distance, uncritical", () => {
    const route: [number, number][] = [
      [52.5, 13.4],
      [52.6, 13.5],
      [52.7, 13.6],
    ];
    const d1 = haversineDistanceM(route[0], route[1]);
    const frames = [
      { distanzM: 0, socPct: 90 },
      { distanzM: d1, socPct: 70 },
    ];
    const result = buildSplicedRoute(route, [], frames);
    expect(result.samples).toEqual([
      { distanzM: 0, socPct: 90 },
      { distanzM: d1, socPct: 70 },
    ]);
    expect(result.samples.every((s) => !s.critical)).toBe(true);
  });

  it("splices a real routed detour into the line and lengthens the total distance", () => {
    const route: [number, number][] = [
      [52.5, 13.4],
      [52.6, 13.5],
      [52.7, 13.6],
    ];
    const station: [number, number] = [52.61, 13.55];
    // Ein echter (grob simulierter) Hin-und-zurueck-Pfad ueber die Station.
    const detourGeometrie: [number, number][] = [
      [52.6, 13.5],
      [52.605, 13.52],
      station,
      [52.605, 13.52],
      [52.6, 13.5],
    ];
    const rawRouteLength =
      haversineDistanceM(route[0], route[1]) +
      haversineDistanceM(route[1], route[2]);

    const result = buildSplicedRoute(
      route,
      [
        {
          position: station,
          distanzM: haversineDistanceM(route[0], route[1]),
          detourGeometrie,
          ankunftsSocPct: 25,
          zielSocPct: 80,
        },
      ],
      [],
    );

    // Die Detour-Punkte (minus dem Startpunkt, der bereits der Routenpunkt
    // ist) muessen zwischen den beiden Routenpunkten eingefuegt sein.
    expect(result.coordinates).toEqual([
      [13.4, 52.5],
      [13.5, 52.6],
      [13.52, 52.605],
      [13.55, 52.61],
      [13.52, 52.605],
      [13.5, 52.6],
      [13.6, 52.7],
    ]);

    // Der Abstecher verlaengert die gesplicete Linie ueber die reine
    // Routenlaenge hinaus.
    expect(result.totalDistanceM).toBeGreaterThan(rawRouteLength);

    // Zwei kritische SoC-Stuetzpunkte (Ankunft -> Ziel) mit strikt
    // aufsteigender Distanz und den richtigen Werten.
    const critical = result.samples.filter((s) => s.critical);
    expect(critical).toHaveLength(2);
    expect(critical[0].socPct).toBe(25);
    expect(critical[1].socPct).toBe(80);
    expect(critical[1].distanzM).toBeGreaterThan(critical[0].distanzM);
  });

  it("falls back to a straight there-and-back detour when detourGeometrie is empty", () => {
    const route: [number, number][] = [
      [52.5, 13.4],
      [52.6, 13.5],
    ];
    const station: [number, number] = [52.65, 13.7];

    const result = buildSplicedRoute(
      route,
      [
        {
          position: station,
          distanzM: 0,
          detourGeometrie: [],
          ankunftsSocPct: 30,
          zielSocPct: 90,
        },
      ],
      [],
    );

    // Fallback: Routenpunkt -> Station -> Routenpunkt, bevor die Route
    // fortgesetzt wird.
    expect(result.coordinates).toEqual([
      [13.4, 52.5],
      [13.7, 52.65],
      [13.4, 52.5],
      [13.5, 52.6],
    ]);
    const critical = result.samples.filter((s) => s.critical);
    expect(critical.map((s) => s.socPct)).toEqual([30, 90]);
  });

  it("sorts charging stops by distanzM regardless of input order", () => {
    const route: [number, number][] = [
      [52.0, 13.0],
      [52.1, 13.1],
      [52.2, 13.2],
      [52.3, 13.3],
    ];
    const d1 = haversineDistanceM(route[0], route[1]);
    const d3 =
      d1 +
      haversineDistanceM(route[1], route[2]) +
      haversineDistanceM(route[2], route[3]);

    const result = buildSplicedRoute(
      route,
      [
        {
          position: [52.31, 13.31],
          distanzM: d3,
          detourGeometrie: [],
          ankunftsSocPct: 15,
          zielSocPct: 85,
        },
        {
          position: [52.11, 13.11],
          distanzM: d1,
          detourGeometrie: [],
          ankunftsSocPct: 40,
          zielSocPct: 95,
        },
      ],
      [],
    );

    const critical = result.samples.filter((s) => s.critical);
    // Erster Ladehalt (bei d1) zuerst, zweiter (bei d3) danach.
    expect(critical.map((s) => s.socPct)).toEqual([40, 95, 15, 85]);
  });

  it("appends a charging stop whose distanzM exceeds the last route vertex (safety net)", () => {
    const route: [number, number][] = [
      [52.0, 13.0],
      [52.1, 13.1],
    ];
    const totalRouteLength = haversineDistanceM(route[0], route[1]);

    const result = buildSplicedRoute(
      route,
      [
        {
          position: [52.11, 13.11],
          distanzM: totalRouteLength + 10_000, // jenseits des letzten Punkts
          detourGeometrie: [],
          ankunftsSocPct: 20,
          zielSocPct: 90,
        },
      ],
      [],
    );

    const critical = result.samples.filter((s) => s.critical);
    expect(critical).toHaveLength(2);
    expect(result.coordinates[result.coordinates.length - 1]).toEqual([
      13.1, 52.1,
    ]);
  });
});
