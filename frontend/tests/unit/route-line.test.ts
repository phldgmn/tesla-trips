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

  it("replaces the range [routeIndexVor, routeIndexNach] with the real routed detour geometry", () => {
    const route: [number, number][] = [
      [52.5, 13.4], // 0
      [52.6, 13.5], // 1 <- routeIndexVor
      [52.65, 13.55], // 2 - replaced, must NOT appear in output
      [52.7, 13.6], // 3 <- routeIndexNach
      [52.8, 13.7], // 4
    ];
    const station: [number, number] = [52.63, 13.52];
    const detourGeometrie: [number, number][] = [
      [52.6, 13.5], // == route[1] (routeIndexVor)
      [52.615, 13.51],
      station,
      [52.625, 13.53],
      [52.7, 13.6], // == route[3] (routeIndexNach)
    ];
    const rawRouteLength =
      haversineDistanceM(route[0], route[1]) +
      haversineDistanceM(route[1], route[2]) +
      haversineDistanceM(route[2], route[3]) +
      haversineDistanceM(route[3], route[4]);

    const result = buildSplicedRoute(
      route,
      [
        {
          position: station,
          distanzM: 1000,
          detourGeometrie,
          routeIndexVor: 1,
          routeIndexNach: 3,
          ankunftsSocPct: 25,
          zielSocPct: 80,
        },
      ],
      [],
    );

    expect(result.coordinates).toEqual([
      [13.4, 52.5],
      [13.5, 52.6],
      [13.51, 52.615],
      [13.52, 52.63],
      [13.53, 52.625],
      [13.6, 52.7],
      [13.7, 52.8],
    ]);

    // Der Abstecher verlaengert die gesplicete Linie ueber die reine
    // Routenlaenge hinaus (Umweg zur Station statt der direkten route[1]->route[3]-Strecke).
    expect(result.totalDistanceM).toBeGreaterThan(rawRouteLength);

    const critical = result.samples.filter((s) => s.critical);
    expect(critical).toHaveLength(2);
    expect(critical[0].socPct).toBe(25);
    expect(critical[1].socPct).toBe(80);
    expect(critical[1].distanzM).toBeGreaterThan(critical[0].distanzM);
  });

  it("places the arrival->departure SoC jump right at the station instead of smearing it over the return leg", () => {
    const route: [number, number][] = [
      [52.5, 13.4], // 0
      [52.6, 13.5], // 1 <- routeIndexVor
      [52.65, 13.55], // 2 - replaced, must NOT appear in output
      [52.7, 13.6], // 3 <- routeIndexNach
      [52.8, 13.7], // 4
    ];
    const station: [number, number] = [52.63, 13.52];
    const detourGeometrie: [number, number][] = [
      [52.6, 13.5], // == route[1] (routeIndexVor)
      [52.615, 13.51],
      station,
      [52.625, 13.53],
      [52.7, 13.6], // == route[3] (routeIndexNach)
    ];

    const result = buildSplicedRoute(
      route,
      [
        {
          position: station,
          distanzM: 1000,
          detourGeometrie,
          routeIndexVor: 1,
          routeIndexNach: 3,
          ankunftsSocPct: 18,
          zielSocPct: 80,
        },
      ],
      [],
    );

    const critical = result.samples.filter((s) => s.critical);
    expect(critical).toHaveLength(2);
    const jumpSpan = critical[1].distanzM - critical[0].distanzM;
    // Der Sprung von Ankunfts- zu Ziel-SoC muss quasi am selben Punkt (der
    // Ladestation) passieren statt ueber die gesamte Rueckfahrt der
    // Detour-Schleife verschmiert zu werden.
    expect(jumpSpan).toBeGreaterThan(0);
    expect(jumpSpan).toBeLessThan(1);

    const returnLegLength =
      haversineDistanceM(station, detourGeometrie[3]) +
      haversineDistanceM(detourGeometrie[3], detourGeometrie[4]);
    expect(jumpSpan).toBeLessThan(returnLegLength);
  });

  it("falls back to a straight there-and-back detour when routeIndexVor/Nach are null", () => {
    const route: [number, number][] = [
      [52.5, 13.4],
      [52.6, 13.5],
      [52.7, 13.6],
    ];
    const station: [number, number] = [52.65, 13.7];
    const distanzM = haversineDistanceM(route[0], route[1]);

    const result = buildSplicedRoute(
      route,
      [
        {
          position: station,
          distanzM,
          detourGeometrie: [],
          routeIndexVor: null,
          routeIndexNach: null,
          ankunftsSocPct: 30,
          zielSocPct: 90,
        },
      ],
      [],
    );

    // Fallback: naechstliegender Routenpunkt (per distanzM) -> Station ->
    // derselbe Routenpunkt, bevor die Route fortgesetzt wird.
    expect(result.coordinates).toEqual([
      [13.4, 52.5],
      [13.5, 52.6],
      [13.7, 52.65],
      [13.5, 52.6],
      [13.6, 52.7],
    ]);
    const critical = result.samples.filter((s) => s.critical);
    expect(critical.map((s) => s.socPct)).toEqual([30, 90]);
  });

  it("sorts charging stops by distanzM regardless of input order", () => {
    const route: [number, number][] = [
      [52.0, 13.0], // 0
      [52.1, 13.1], // 1
      [52.2, 13.2], // 2
      [52.3, 13.3], // 3
    ];
    const d1 = haversineDistanceM(route[0], route[1]);
    const d2 = d1 + haversineDistanceM(route[1], route[2]);

    const result = buildSplicedRoute(
      route,
      [
        {
          position: [52.31, 13.31],
          distanzM: d2,
          detourGeometrie: [],
          routeIndexVor: null,
          routeIndexNach: null,
          ankunftsSocPct: 15,
          zielSocPct: 85,
        },
        {
          position: [52.11, 13.11],
          distanzM: d1,
          detourGeometrie: [],
          routeIndexVor: null,
          routeIndexNach: null,
          ankunftsSocPct: 40,
          zielSocPct: 95,
        },
      ],
      [],
    );

    const critical = result.samples.filter((s) => s.critical);
    // Erster Ladehalt (distanzM=d1, nahe route[1]) zuerst, zweiter (distanzM=d2, nahe route[2]) danach.
    expect(critical.map((s) => s.socPct)).toEqual([40, 95, 15, 85]);
  });

  it("skips an overlapping detour instead of corrupting the spliced line", () => {
    const route: [number, number][] = [
      [52.0, 13.0], // 0
      [52.1, 13.1], // 1
      [52.2, 13.2], // 2
      [52.3, 13.3], // 3
      [52.4, 13.4], // 4
    ];
    const stationA: [number, number] = [52.15, 13.16];
    const stationB: [number, number] = [52.18, 13.19];

    const result = buildSplicedRoute(
      route,
      [
        {
          // Ueberlappt mit dem ersten Abstecher (Bereich [1,3] vs [0,2]).
          position: stationB,
          distanzM: 2,
          detourGeometrie: [route[1], stationB, route[3]],
          routeIndexVor: 1,
          routeIndexNach: 3,
          ankunftsSocPct: 50,
          zielSocPct: 60,
        },
        {
          position: stationA,
          distanzM: 1,
          detourGeometrie: [route[0], stationA, route[2]],
          routeIndexVor: 0,
          routeIndexNach: 2,
          ankunftsSocPct: 10,
          zielSocPct: 20,
        },
      ],
      [],
    );

    // Nur der erste (per distanzM sortierte) Abstecher wird gespleisst; der
    // ueberlappende zweite wird sicher uebersprungen statt die Linie zu
    // beschaedigen oder eine Endlosschleife zu erzeugen.
    expect(result.coordinates).toEqual([
      [13.0, 52.0],
      [13.16, 52.15],
      [13.2, 52.2],
      [13.3, 52.3],
      [13.4, 52.4],
    ]);
    const critical = result.samples.filter((s) => s.critical);
    expect(critical.map((s) => s.socPct)).toEqual([10, 20]);
  });
});
