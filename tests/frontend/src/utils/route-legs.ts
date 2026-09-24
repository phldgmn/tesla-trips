import type { SplicedRoute, RouteSample } from "./route-line";

/** Ein fahrbarer Teilabschnitt der gespliceten Route zwischen zwei
 *  Entscheidungspunkten (Start, Ladehalt, Ziel) - siehe `splitRouteIntoLegs()`. */
export interface RouteLeg {
  /** Koordinaten dieses Teilabschnitts, in [lng, lat] */
  coordinates: [number, number][];
  /** Laenge dieses Teilabschnitts in Metern */
  totalDistanceM: number;
  /** SoC-Stuetzpunkte, `distanzM` relativ zum Beginn DIESES Teilabschnitts
   *  (0 = Start des Legs), nicht zur Gesamtroute. */
  samples: RouteSample[];
}

/** Zerlegt eine gesplicete Route an jedem Ladehalt in einzelne, unabhaengig
 * gerenderte Teilabschnitte ("Legs").
 *
 * MapLibre backt `line-gradient` (siehe `buildSocGradientExpression`) in
 * eine texture mit NUR 256 Texeln ueber die GESAMTE Linienlaenge - bei
 * einer einzigen, alle Ladehalte umfassenden Linie ueber hunderte bis
 * tausende Kilometer entspricht das mehrere Kilometer pro Texel. Ein am
 * Ladehalt technisch korrekt auf <1 m Distanz kollabierter SoC-Sprung
 * (siehe `CHARGE_JUMP_EPSILON_M`) faellt dann bei der Texture-Bake-Aufloesung
 * unter den Tisch, MapLibre kann diesen Sprung schlicht nicht darstellen
 * (dokumentiertes MapLibre/Mapbox-GL-Verhalten, siehe
 * https://github.com/mapbox/mapbox-gl-js/issues/9728) - sichtbar als
 * durchgehend falsch eingefaerbte Strecke nach jedem Ladehalt, obwohl die
 * zugrundeliegenden SoC-Daten korrekt sind. Durch das Aufteilen in einen
 * eigenen MapLibre-Layer PRO Leg (siehe `Map.tsx`) bekommt jeder Leg seine
 * EIGENE 256-Texel-Texture ueber nur seine eigene (viel kuerzere) Laenge -
 * der SoC-Sprung liegt dann exakt an der Leg-Grenze (Ende von Leg N / Start
 * von Leg N+1, ZWEI verschiedene Layer) statt irgendwo mitten in einer
 * gemeinsamen Texture verschmiert zu werden.
 */
export function splitRouteIntoLegs(spliced: SplicedRoute): RouteLeg[] {
  if (spliced.coordinates.length === 0) {
    return [];
  }

  const legs: RouteLeg[] = [];
  let coordStart = 0;
  let distStart = 0;
  let isFirstLeg = true;

  const pushLeg = (coordEndInclusive: number, distEnd: number) => {
    const coordinates = spliced.coordinates.slice(
      coordStart,
      coordEndInclusive + 1,
    );
    // Der Ankunfts-Stuetzpunkt eines Ladehalts liegt distanzM-genau AUF der
    // Leg-Grenze (Ende von Leg N). Ohne den `isFirstLeg`-Sonderfall (untere
    // Schranke inklusive nur beim allerersten Leg) wuerde er per `>=` auch
    // in Leg N+1 auftauchen (dort bei distanzM=0 dupliziert) - der 0.5 m
    // spaeter liegende Abfahrts-Stuetzpunkt bleibt davon unberuehrt.
    const samples = spliced.samples
      .filter((s) =>
        isFirstLeg ? s.distanzM >= distStart : s.distanzM > distStart,
      )
      .filter((s) => s.distanzM <= distEnd)
      .sort((a, b) => a.distanzM - b.distanzM)
      .map((s) => ({ ...s, distanzM: s.distanzM - distStart }));
    legs.push({ coordinates, totalDistanceM: distEnd - distStart, samples });
    isFirstLeg = false;
  };

  for (const boundary of spliced.legBoundaries) {
    pushLeg(boundary.coordinateIndex, boundary.distanzM);
    coordStart = boundary.coordinateIndex;
    distStart = boundary.distanzM;
  }
  pushLeg(spliced.coordinates.length - 1, spliced.totalDistanceM);

  return legs.filter((leg) => leg.coordinates.length >= 2);
}
