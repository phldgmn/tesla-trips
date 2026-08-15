/**
 * Vendorte Supercharger-Stationliste – ausschließlich für den UI-Ladestopp-Picker.
 *
 * Datenquelle: `data/supercharger_snapshot.json` (manuell nachgetippt, kein
 * Build-Schritt, kein fs-Lesen zur Laufzeit). Es handelt sich um eine kleine
 * statische Schnappschuss-Liste, die dem Benutzer nur das Eingeben plausibler
 * Wegepunkte erleichtern soll. Das Backend löst die tatsächchen Ladestopps
 * unabhängig über den `ChargingStationProvider` auf – diese Liste ist daher
 * NICHT autoritativ, sondern eine reine UX-Abkürzung.
 */

export interface SuperchargerStation {
  stationId: string;
  name: string;
  /** [lat, lon] */
  position: [number, number];
  country: string;
  maxLadeleistungKw: number;
}

export const SUPERCHARGER_STATIONS: SuperchargerStation[] = [
  {
    stationId: "sc-de-berlin-alex-001",
    name: "Tesla Supercharger - Berlin Alexanderplatz",
    position: [52.5234, 13.4114],
    country: "DE",
    maxLadeleistungKw: 2500.0,
  },
  {
    stationId: "sc-de-berlin-potsdamer-002",
    name: "Tesla Supercharger - Berlin Potsdamer Platz",
    position: [52.5083, 13.3797],
    country: "DE",
    maxLadeleistungKw: 2000.0,
  },
  {
    stationId: "sc-de-hamburg-003",
    name: "Tesla Supercharger - Hamburg Harburg",
    position: [53.4775, 10.0819],
    country: "DE",
    maxLadeleistungKw: 3000.0,
  },
  {
    stationId: "sc-dk-copenhagen-004",
    name: "Tesla Supercharger - Kopenhagen Airport",
    position: [55.6182, 12.6509],
    country: "DK",
    maxLadeleistungKw: 3000.0,
  },
  {
    stationId: "sc-se-malmo-005",
    name: "Tesla Supercharger - Malmö Urban",
    position: [55.5941, 13.0039],
    country: "SE",
    maxLadeleistungKw: 2500.0,
  },
];
