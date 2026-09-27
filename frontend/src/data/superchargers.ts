/**
 * Vendored supercharger station list — exclusively for the UI charging stop picker.
 *
 * Data source: `data/supercharger_snapshot.json` (manually re-typed, no
 * build step, no fs read at runtime). This is a small
 * static snapshot list that only lets the user enter plausible
 * waypoints to be easier. The backend resolves the actual charging stops
 * independently via the `ChargingStationProvider` – this list is therefore
 * NOT authoritative, but a pure UX shortcut.
 */

export interface SuperchargerStation {
  stationId: string;
  name: string;
  /** [lat, lon] */
  position: [number, number];
  country: string;
  maxChargingPowerKw: number;
}

export const SUPERCHARGER_STATIONS: SuperchargerStation[] = [
  {
    stationId: "sc-de-berlin-alex-001",
    name: "Tesla Supercharger - Berlin Alexanderplatz",
    position: [52.5234, 13.4114],
    country: "DE",
    maxChargingPowerKw: 2500.0,
  },
  {
    stationId: "sc-de-berlin-potsdamer-002",
    name: "Tesla Supercharger - Berlin Potsdamer Platz",
    position: [52.5083, 13.3797],
    country: "DE",
    maxChargingPowerKw: 2000.0,
  },
  {
    stationId: "sc-de-hamburg-003",
    name: "Tesla Supercharger - Hamburg Harburg",
    position: [53.4775, 10.0819],
    country: "DE",
    maxChargingPowerKw: 3000.0,
  },
  {
    stationId: "sc-dk-copenhagen-004",
    name: "Tesla Supercharger - Kopenhagen Airport",
    position: [55.6182, 12.6509],
    country: "DK",
    maxChargingPowerKw: 3000.0,
  },
  {
    stationId: "sc-se-malmo-005",
    name: "Tesla Supercharger - Malmö Urban",
    position: [55.5941, 13.0039],
    country: "SE",
    maxChargingPowerKw: 2500.0,
  },
];
