/** Fahrzeugprofil-Presets für die Reiseplanung.
 *
 * ANNAHME (dokumentiert gemäß `AGENTS.md`, "Bei Unsicherheit"): Es gibt
 * (noch) keinen Backend-Endpunkt, der Fahrzeugprofile aus einer Konfiguration
 * auflistet (`docs/plans/06-energy.md` sieht `VehicleEnergyParameters` nur
 * mit fest verdrahteten Defaults vor, `06-offene-punkte-widersprueche.md`
 * Punkt 5 bestätigt: keine Kalibrier-Pflicht). Die Presets hier sind
 * plausible, öffentlich bekannte Fahrzeugdaten für die drei gängigen
 * Tesla-Varianten und dienen als Startwerte; das Formular erlaubt das
 * Überschreiben jedes Einzelfelds ("Erweitert"). Das "Model 3 Standard
 * Range RWD"-Preset übernimmt exakt die Default-Werte aus
 * `src/tripplanner/trip_input/cli.py`, damit CLI und UI ohne Override
 * identisch simulieren.
 */

import type { VehicleProfilePreset } from "../types/trip-request";

export const VEHICLE_PROFILE_PRESETS: VehicleProfilePreset[] = [
  {
    id: "model3_standard",
    label: "Model 3 Standard Range RWD",
    profile: {
      massKg: 1800.0,
      dragCoefficient: 0.23,
      frontalAreaM2: 2.2,
      rollingResistanceCoefficient: 0.01,
      batteryCapacityKwh: 60.0,
      auxiliaryBaselineKw: 0.34,
      tireType: "standard",
      roofBox: false,
    },
  },
  {
    id: "model3_long_range_awd",
    label: "Model 3 Long Range AWD",
    profile: {
      massKg: 1930.0,
      dragCoefficient: 0.23,
      frontalAreaM2: 2.22,
      rollingResistanceCoefficient: 0.0095,
      batteryCapacityKwh: 75.0,
      auxiliaryBaselineKw: 0.36,
      tireType: "standard",
      roofBox: false,
    },
  },
  {
    id: "model_y_long_range_awd",
    label: "Model Y Long Range AWD",
    profile: {
      massKg: 2050.0,
      dragCoefficient: 0.23,
      frontalAreaM2: 2.62,
      rollingResistanceCoefficient: 0.011,
      batteryCapacityKwh: 75.0,
      auxiliaryBaselineKw: 0.38,
      tireType: "standard",
      roofBox: false,
    },
  },
];

export const DEFAULT_VEHICLE_PROFILE_PRESET_ID = VEHICLE_PROFILE_PRESETS[0].id;
