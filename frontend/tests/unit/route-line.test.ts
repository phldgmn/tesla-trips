import { describe, it, expect } from "vitest";
import {
  buildSplicedRoute,
  splitRouteIntoLegs,
  projectDistanceAlongLineM,
  findNearestRouteSample,
  type RouteSample,
} from "@/utils/route-line";
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

  it("splits a waypoint stop (no detour geometry, on-route position) into its own leg too", () => {
    // Regressionstest: `Map.tsx` speist Zwischenstopps (waypoint_stops, KEIN
    // echter Ladehalt-Abstecher) als Detour OHNE eigene Geometrie
    // (`detourGeometrie: []`, `routeIndexVor/Nach: null`) ein, statt sie nur
    // als SoC-Stuetzpunkt in den Fahr-Frames zu kodieren - sonst bliebe der
    // Zwischenstopp Teil EINER gemeinsamen, ueber hunderte km reichenden
    // Leg, deren feste 256-Texel `line-gradient`-Textur den SoC-Sprung an
    // der Stopp-Position verschmiert (unabhaengig davon, wie exakt die
    // zugrundeliegenden SoC-Werte sind - sichtbar als falsche Kartenfarbe
    // rund um den Zwischenstopp trotz korrekter Backend-Daten).
    const route: [number, number][] = [
      [50.0, 8.0],
      [50.5, 8.0],
      [51.0, 8.0],
      [51.5, 8.0],
      [52.0, 8.0],
    ];
    const waypointPosition: [number, number] = [51.0, 8.0];
    const distanzM =
      haversineDistanceM(route[0], route[1]) +
      haversineDistanceM(route[1], route[2]);

    const spliced = buildSplicedRoute(
      route,
      [
        {
          position: waypointPosition,
          distanzM,
          detourGeometrie: [],
          stationIndex: null,
          routeIndexVor: null,
          routeIndexNach: null,
          ankunftsSocPct: 25,
          zielSocPct: 100,
        },
      ],
      [],
    );

    const legs = splitRouteIntoLegs(spliced);

    expect(legs).toHaveLength(2);
    for (const leg of legs) {
      expect(leg.totalDistanceM).toBeLessThan(spliced.totalDistanceM * 0.6);
    }
    const leg1Critical = legs[0].samples.filter((s) => s.critical);
    const leg2Critical = legs[1].samples.filter((s) => s.critical);
    expect(leg1Critical.map((s) => s.socPct)).toEqual([25]);
    expect(leg2Critical.map((s) => s.socPct)).toEqual([100]);
  });
});

describe("projectDistanceAlongLineM", () => {
  it("returns 0 for an empty line", () => {
    expect(projectDistanceAlongLineM([], [10, 50])).toBe(0);
  });

  it("returns 0 for a point at the start of the line", () => {
    const coords: [number, number][] = [
      [10, 50],
      [10.1, 50],
      [10.2, 50],
    ];
    expect(projectDistanceAlongLineM(coords, [10, 50])).toBeCloseTo(0, 3);
  });

  it("returns the cumulative distance to a later vertex the point sits on", () => {
    const coords: [number, number][] = [
      [10, 50],
      [10.1, 50],
      [10.2, 50],
    ];
    const d1 = haversineDistanceM([50, 10], [50, 10.1]);
    expect(
      Math.abs(projectDistanceAlongLineM(coords, [10.1, 50]) - d1) / d1,
    ).toBeLessThan(0.01);
  });

  it("projects a point near the middle of a segment onto that segment, not onto a vertex", () => {
    const coords: [number, number][] = [
      [10, 50],
      [10.2, 50],
    ];
    const dHalf = projectDistanceAlongLineM(coords, [10.1, 50]);
    const dFull = haversineDistanceM([50, 10], [50, 10.2]);
    expect(dHalf).toBeGreaterThan(0);
    expect(dHalf).toBeLessThan(dFull);
    expect(Math.abs(dHalf - dFull / 2) / dFull).toBeLessThan(0.01);
  });

  it("stays accurate for a point far along a route spanning many degrees of latitude", () => {
    // Regression: die Distanz-entlang-der-Linie wurde zuvor mit einer am
    // ABFRAGEPUNKT verankerten cos(lat)-Naeherung fuer JEDES Segment
    // akkumuliert - bei einer langen, viele Breitengrade umspannenden Route
    // (hier ueber 10 Grad, ca. 1.100 km) drifteten diese Fehler bis zum
    // fernen Ende um mehrere Kilometer auseinander, obwohl die lokale
    // Segment-Projektion selbst korrekt war.
    const coords: [number, number][] = [];
    const steps = 200;
    for (let i = 0; i <= steps; i++) {
      coords.push([10, 50 + (10 * i) / steps]);
    }
    let expected = 0;
    for (let i = 1; i < coords.length; i++) {
      expected += haversineDistanceM(
        [coords[i - 1][1], coords[i - 1][0]],
        [coords[i][1], coords[i][0]],
      );
    }
    const farPoint = coords[coords.length - 1];
    const distAlongM = projectDistanceAlongLineM(coords, farPoint);
    expect(Math.abs(distAlongM - expected) / expected).toBeLessThan(0.001);
  });

  it("prefers the later-occurring segment when two segments coincide exactly (overlapping detour legs)", () => {
    // Ein Ladehalt-Abstecher routet Hin- und Rueckweg oft als zwei separat
    // geroutete Beine ueber dieselbe (einzige) Zufahrtsstrasse - auf diesem
    // gemeinsamen Stueck sind Hin- und Rueckweg-Koordinaten pixelgenau
    // identisch. Ein Hover auf diesem Stueck muss den SPAETER in
    // `coordinates` liegenden (Rueckweg-)Punkt liefern, nicht den ersten
    // gefundenen (Hinweg-) Treffer.
    const shared: [number, number][] = [
      [10, 50],
      [10.01, 50.01],
    ];
    const coords: [number, number][] = [
      [9.9, 49.9],
      ...shared, // Hinweg-Anteil
      [10.1, 50.05], // Station
      ...shared, // Rueckweg-Anteil, identisch zum Hinweg
      [10.2, 50.1],
    ];
    const distAlongM = projectDistanceAlongLineM(coords, shared[1]);
    // Muss der SPAETEREN (Rueckweg-)Position entsprechen, nicht der
    // frueheren (Hinweg-)Position mit kleinerer kumulierter Distanz.
    const earlyMatchIdx = 2; // shared[1] beim Hinweg
    const lateMatchIdx = 5; // shared[1] beim Rueckweg
    const early = projectDistanceAlongLineM(
      coords.slice(0, earlyMatchIdx + 1),
      shared[1],
    );
    const late = projectDistanceAlongLineM(
      coords.slice(0, lateMatchIdx + 1),
      shared[1],
    );
    expect(distAlongM).toBeCloseTo(late, 3);
    expect(distAlongM).toBeGreaterThan(early);
  });
});

describe("findNearestRouteSample", () => {
  const samples: RouteSample[] = [
    { distanzM: 0, socPct: 90, timestamp: "2026-08-16T18:00:00" },
    { distanzM: 1000, socPct: 70, timestamp: "2026-08-16T18:10:00" },
    { distanzM: 5000, socPct: 40, timestamp: "2026-08-16T18:30:00" },
  ];

  it("returns undefined for an empty sample list", () => {
    expect(findNearestRouteSample([], 1000)).toBeUndefined();
  });

  it("returns the exact match when distanzM lines up with a sample", () => {
    expect(findNearestRouteSample(samples, 1000)).toBe(samples[1]);
  });

  it("picks the closer of the two surrounding samples", () => {
    expect(findNearestRouteSample(samples, 1200)).toBe(samples[1]);
    expect(findNearestRouteSample(samples, 3500)).toBe(samples[2]);
  });

  it("clamps to the first/last sample beyond the line's ends", () => {
    expect(findNearestRouteSample(samples, -500)).toBe(samples[0]);
    expect(findNearestRouteSample(samples, 9000)).toBe(samples[2]);
  });
});

describe("route-hover regression: post-charging SoC/time near a charging stop", () => {
  // Reproduziert den gemeldeten Bug: der Routen-Hover-Tooltip einige
  // Kilometer NACH einem Ladehalt zeigte weiterhin die Vor-Lade-Werte
  // (niedrige SoC, fruehere Uhrzeit) an, weil er den raeumlich naechsten
  // `SimulationFrame` suchte statt den Punkt anhand seiner Distanz entlang
  // der tatsaechlich gezeichneten (gesplicete) Linie zu bestimmen. Ein
  // Autobahnkreuz nahe einer Ladestation bringt Vor- und Nach-Ladehalt-
  // Streckenpunkte raeumlich nah zusammen (siehe `routeIndexVor`/
  // `routeIndexNach` unten), obwohl sie streckenmaessig weit auseinander
  // liegen.
  const route: [number, number][] = [
    [50.0, 10.0], // 0 - Start
    [50.5, 10.0], // 1 - routeIndexVor (Autobahnkreuz-Zufahrt)
    [50.55, 10.0], // 2 - ersetzt, darf nicht im Output auftauchen
    [50.5, 10.0002], // 3 - routeIndexNach, raeumlich nur ~15m von Punkt 1 entfernt
    [51.0, 10.0], // 4 - Ziel, weit entfernt
  ];
  const station: [number, number] = [50.52, 10.1];
  const detourGeometrie: [number, number][] = [
    route[1],
    [50.51, 10.05],
    station,
    [50.51, 10.06],
    route[3],
  ];

  const spliced = buildSplicedRoute(
    route,
    [
      {
        position: station,
        distanzM: 1000,
        detourGeometrie,
        stationIndex: 2,
        routeIndexVor: 1,
        routeIndexNach: 3,
        ankunftsSocPct: 20,
        zielSocPct: 80,
        arrivalTime: "2026-08-16T19:17:00",
        departureTime: "2026-08-16T19:30:00",
      },
    ],
    [],
  );

  it("resolves the departure SoC/time right at the point the detour rejoins the highway", () => {
    // Hover-Punkt: exakt an routeIndexNach (dem raeumlich nah an
    // routeIndexVor liegenden Wiedereinstiegspunkt).
    const hoverPoint: [number, number] = [10.0002, 50.5];
    const distAlongM = projectDistanceAlongLineM(
      spliced.coordinates,
      hoverPoint,
    );
    const sample = findNearestRouteSample(spliced.samples, distAlongM);

    expect(sample).toBeDefined();
    // Muss den Abfahrts- (post-Ladehalt) Zustand liefern, nicht den
    // Ankunfts-Zustand - trotz raeumlicher Naehe zu routeIndexVor.
    expect(sample!.socPct).toBe(80);
    expect(sample!.timestamp).toBe("2026-08-16T19:30:00");
  });

  it("still resolves the arrival SoC/time right before the detour, spatially near the same spot", () => {
    const hoverPoint: [number, number] = [10.0, 50.5];
    const distAlongM = projectDistanceAlongLineM(
      spliced.coordinates,
      hoverPoint,
    );
    const sample = findNearestRouteSample(spliced.samples, distAlongM);

    expect(sample).toBeDefined();
    expect(sample!.socPct).toBe(20);
    expect(sample!.timestamp).toBe("2026-08-16T19:17:00");
  });
});

describe("buildSplicedRoute: FAHREN-Frames innerhalb des margin_m-Puffers", () => {
  // Backend-Klammerpunkte (`_finde_klammerpunkte`, `margin_m = 3000`) liegen
  // bis zu 3 km VOR/NACH dem tatsaechlichen Ladehalt - ein zeitdiskretisierter
  // FAHREN-Frame kurz vor ODER kurz NACH dem Ladehalt kann also durchaus
  // innerhalb von [routeIndexVor, routeIndexNach] liegen. Eine einzige
  // Interpolation ueber den GESAMTEN Puffer (ignoriert, ob der Frame vor
  // oder nach dem Ladehalt liegt) konnte einen Nach-Ladehalt-Frame VOR den
  // SoC-Sprung projizieren - der genau gemeldete Bug (Routen-Hover zeigt
  // Vor-Lade-SoC einige Kilometer nach dem Ladehalt).
  const route: [number, number][] = [
    [50.0, 8.0], // 0
    [50.5, 8.0], // 1 - routeIndexVor
    [50.75, 8.0], // 2 - ersetzt, tatsaechlicher Abzweigpunkt liegt hier in der Naehe
    [51.0, 8.0], // 3 - routeIndexNach
    [51.5, 8.0], // 4
  ];
  const station: [number, number] = [50.76, 8.05];
  const detourGeometrie: [number, number][] = [
    route[1],
    [50.7, 8.02],
    station,
    [50.8, 8.02],
    route[3],
  ];
  const routeCum1 = haversineDistanceM(route[0], route[1]);
  const routeCum3 =
    routeCum1 +
    haversineDistanceM(route[1], route[2]) +
    haversineDistanceM(route[2], route[3]);
  // Tatsaechlicher Abzweigpunkt: mittig im Puffer, wie bei `margin_m=3000`
  // symmetrisch um den echten Ladehalt.
  const chargeDistanzM = (routeCum1 + routeCum3) / 2;

  const result = buildSplicedRoute(
    route,
    [
      {
        position: station,
        distanzM: chargeDistanzM,
        detourGeometrie,
        stationIndex: 2,
        routeIndexVor: 1,
        routeIndexNach: 3,
        ankunftsSocPct: 20,
        zielSocPct: 80,
        arrivalTime: "2026-08-16T19:17:00",
        departureTime: "2026-08-16T19:30:00",
      },
    ],
    [
      // Kurz VOR dem tatsaechlichen Ladehalt, aber noch innerhalb des
      // Puffers [routeIndexVor, routeIndexNach].
      {
        distanzM: chargeDistanzM - 200,
        socPct: 21,
        timestamp: "2026-08-16T19:15:00",
      },
      // Kurz NACH dem Ladehalt, ebenfalls noch innerhalb des Puffers.
      {
        distanzM: chargeDistanzM + 200,
        socPct: 79,
        timestamp: "2026-08-16T19:32:00",
      },
    ],
  );

  it("keeps all samples sorted ascending by distanzM", () => {
    for (let i = 1; i < result.samples.length; i++) {
      expect(result.samples[i].distanzM).toBeGreaterThanOrEqual(
        result.samples[i - 1].distanzM,
      );
    }
  });

  it("positions the pre-charge frame before the arrival jump and the post-charge frame after the departure jump", () => {
    const preFrame = result.samples.find((s) => s.socPct === 21)!;
    const postFrame = result.samples.find((s) => s.socPct === 79)!;
    const arrival = result.samples.find((s) => s.critical && s.socPct === 20)!;
    const departure = result.samples.find(
      (s) => s.critical && s.socPct === 80,
    )!;

    expect(preFrame).toBeDefined();
    expect(postFrame).toBeDefined();
    expect(arrival).toBeDefined();
    expect(departure).toBeDefined();

    expect(preFrame.distanzM).toBeLessThan(arrival.distanzM);
    expect(arrival.distanzM).toBeLessThan(departure.distanzM);
    expect(departure.distanzM).toBeLessThan(postFrame.distanzM);
  });
});
