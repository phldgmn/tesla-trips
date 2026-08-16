import { describe, it, expect } from "vitest";
import { buildSplicedRoute, splitRouteIntoLegs } from "@/utils/route-line";
import { haversineDistanceM } from "@/utils/geo-utils";

describe("buildSplicedRoute", () => {
  it("returns an empty result for an empty route geometry", () => {
    const result = buildSplicedRoute([], [], []);
    expect(result).toEqual({
      coordinates: [],
      totalDistanceM: 0,
      samples: [],
      legBoundaries: [],
    });
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
          stationIndex: 2,
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
          stationIndex: 2,
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

  it("uses the backend-provided stationIndex instead of a nearest-point search that a nearby ramp could fool", () => {
    // Realistischer Autobahnkreuz-Fall: eine andere, ueberlagernde Rampe
    // (Index 1) liegt in der 2D-Projektion NAEHER an der Ladestation als der
    // tatsaechliche Anschlusspunkt (Index 3), an dem der Detour die Station
    // wirklich erreicht (siehe `_step_lade_detours_routen` - zwei separat
    // geroutete Beine statt Naechster-Punkt-Heuristik).
    const route: [number, number][] = [
      [52.5, 13.4], // 0
      [52.6, 13.5], // 1 <- routeIndexVor
      [52.65, 13.55], // 2 - replaced
      [52.7, 13.6], // 3 <- routeIndexNach
      [52.8, 13.7], // 4
    ];
    const station: [number, number] = [52.6101, 13.5201];
    const decoyRampPoint: [number, number] = [52.61005, 13.52005];
    const echterStationsAnschluss: [number, number] = [52.611, 13.521];
    const detourGeometrie: [number, number][] = [
      [52.6, 13.5], // 0 == route[1]
      decoyRampPoint, // 1 - geometrisch naeher, aber NICHT der echte Anschluss
      [52.63, 13.54], // 2
      echterStationsAnschluss, // 3 - echter Anschlusspunkt laut Backend
      [52.7, 13.6], // 4 == route[3]
    ];
    // Sanity-Check der Testannahme: die Deko-Rampe liegt tatsaechlich naeher
    // an der Station als der echte Anschlusspunkt.
    expect(haversineDistanceM(decoyRampPoint, station)).toBeLessThan(
      haversineDistanceM(echterStationsAnschluss, station),
    );

    const stop = {
      position: station,
      distanzM: 1000,
      detourGeometrie,
      routeIndexVor: 1,
      routeIndexNach: 3,
      ankunftsSocPct: 18,
      zielSocPct: 80,
    };

    const explicit = buildSplicedRoute(
      route,
      [{ ...stop, stationIndex: 3 }],
      [],
    );
    const fallback = buildSplicedRoute(
      route,
      [{ ...stop, stationIndex: null }],
      [],
    );

    const explicitJumpAt = explicit.samples.filter((s) => s.critical)[0]
      .distanzM;
    const fallbackJumpAt = fallback.samples.filter((s) => s.critical)[0]
      .distanzM;

    // Mit explizitem stationIndex liegt der Sprung am ECHTEN Anschlusspunkt
    // (weiter entlang der Detour-Geometrie) statt an der naeher liegenden,
    // aber falschen Deko-Rampe, auf die die Naechster-Punkt-Suche hereinfallen
    // wuerde.
    expect(explicitJumpAt).toBeGreaterThan(fallbackJumpAt);
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
          stationIndex: null,
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
          stationIndex: null,
          routeIndexVor: null,
          routeIndexNach: null,
          ankunftsSocPct: 15,
          zielSocPct: 85,
        },
        {
          position: [52.11, 13.11],
          distanzM: d1,
          detourGeometrie: [],
          stationIndex: null,
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
          stationIndex: 1,
          routeIndexVor: 1,
          routeIndexNach: 3,
          ankunftsSocPct: 50,
          zielSocPct: 60,
        },
        {
          position: stationA,
          distanzM: 1,
          detourGeometrie: [route[0], stationA, route[2]],
          stationIndex: 1,
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

describe("splitRouteIntoLegs", () => {
  it("returns a single leg spanning the whole route when there are no charging stops", () => {
    const route: [number, number][] = [
      [52.5, 13.4],
      [52.6, 13.5],
      [52.7, 13.6],
    ];
    const spliced = buildSplicedRoute(route, [], []);
    const legs = splitRouteIntoLegs(spliced);

    expect(legs).toHaveLength(1);
    expect(legs[0].coordinates).toEqual(spliced.coordinates);
    expect(legs[0].totalDistanceM).toBeCloseTo(spliced.totalDistanceM, 6);
  });

  it("splits at each charging stop so every leg gets its own, much shorter total distance", () => {
    // Zwei weit auseinanderliegende Ladehalte auf einer langen Route -
    // reproduziert den Bug, dass eine gemeinsame MapLibre-line-gradient-
    // Texture (256 Texel) ueber die GESAMTE Route den an jedem Ladehalt auf
    // <1 m kollabierten SoC-Sprung nicht mehr darstellen kann, sobald die
    // Gesamtroute viele hundert km lang ist (siehe `splitRouteIntoLegs`-
    // Docstring). Mit dem Split muss jeder Leg eine viel kuerzere
    // `totalDistanceM` als die Gesamtroute haben.
    const route: [number, number][] = [
      [50.0, 8.0], // 0
      [50.5, 8.0], // 1 <- routeIndexVor Station A
      [51.0, 8.0], // 2 - ersetzt
      [51.5, 8.0], // 3 <- routeIndexNach Station A
      [52.0, 8.0], // 4
      [52.5, 8.0], // 5 <- routeIndexVor Station B
      [53.0, 8.0], // 6 - ersetzt
      [53.5, 8.0], // 7 <- routeIndexNach Station B
      [54.0, 8.0], // 8
    ];
    const stationA: [number, number] = [50.75, 8.02];
    const detourA: [number, number][] = [
      [50.5, 8.0],
      [50.6, 8.01],
      stationA,
      [50.9, 8.01],
      [51.5, 8.0],
    ];
    const stationB: [number, number] = [52.75, 8.02];
    const detourB: [number, number][] = [
      [52.5, 8.0],
      [52.6, 8.01],
      stationB,
      [52.9, 8.01],
      [53.5, 8.0],
    ];

    const spliced = buildSplicedRoute(
      route,
      [
        {
          position: stationA,
          distanzM: 1000,
          detourGeometrie: detourA,
          stationIndex: 2,
          routeIndexVor: 1,
          routeIndexNach: 3,
          ankunftsSocPct: 18,
          zielSocPct: 80,
        },
        {
          position: stationB,
          distanzM: 2000,
          detourGeometrie: detourB,
          stationIndex: 2,
          routeIndexVor: 5,
          routeIndexNach: 7,
          ankunftsSocPct: 22,
          zielSocPct: 80,
        },
      ],
      [],
    );

    const legs = splitRouteIntoLegs(spliced);

    expect(legs).toHaveLength(3);
    for (const leg of legs) {
      // Jeder Leg ist ein echter Bruchteil der Gesamtroute, nicht die
      // gesamte (verschmierte) Streckenlaenge.
      expect(leg.totalDistanceM).toBeLessThan(spliced.totalDistanceM * 0.6);
    }

    // Leg 1 endet mit dem Ankunfts-SoC von Station A bei voller Distanz
    // (progress = 1), Leg 2 beginnt mit dem Ziel-SoC von Station A bei
    // Distanz ~0 (progress = 0) - der Sprung liegt an der LEG-GRENZE, ohne
    // Duplikat des Ankunfts-Stuetzpunkts (siehe `isFirstLeg`-Sonderfall in
    // `splitRouteIntoLegs`). Leg 2 endet seinerseits mit dem Ankunfts-SoC
    // von Station B.
    const leg1Critical = legs[0].samples.filter((s) => s.critical);
    const leg2Critical = legs[1].samples.filter((s) => s.critical);
    const leg3Critical = legs[2].samples.filter((s) => s.critical);
    expect(leg1Critical.map((s) => s.socPct)).toEqual([18]);
    expect(leg1Critical[0].distanzM).toBeCloseTo(legs[0].totalDistanceM, 3);
    expect(leg2Critical.map((s) => s.socPct)).toEqual([80, 22]);
    expect(leg2Critical[0].distanzM).toBeLessThan(1);
    expect(leg2Critical[1].distanzM).toBeCloseTo(legs[1].totalDistanceM, 3);
    expect(leg3Critical.map((s) => s.socPct)).toEqual([80]);
    expect(leg3Critical[0].distanzM).toBeLessThan(1);
  });
});
