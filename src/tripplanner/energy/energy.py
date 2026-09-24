"""Energieverbrauchs-Modul (Phase 3).

Physikalisch fundierte Berechnung des Energieverbrauchs für Elektrofahrzeuge
unter Berücksichtigung von rolling_resistance, air_drag, gradient, recuperation
und HVAC-consumption.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from tripplanner.construction.models import ConstructionZone
from tripplanner.elevation.models import SegmentGradient
from tripplanner.energy.models import SegmentEnergyResult, VehicleEnergyParameters
from tripplanner.routing.models import RouteSegment
from tripplanner.weather.models import WeatherSample
from tripplanner.wind.models import WindComponents

# Luftdichte (ISA-Standard bei 15°C, 1013 hPa)
LUFTDICHE_KGM3: float = 1.225

# ISA-Referenzwerte für Luftdichte-Berechnung
ISA_TEMPERATUR_C: float = 15.0
ISA_LUFTDRUCK_HPA: float = 1013.25

# Erdbeschleunigung
ERDBESCHLEUNIGUNG_MS2: float = 9.81

# Maximaler Rekuperationswert
REKUPERATION_MAX_POWER_W: float = 80_000  # 80 kW

# Straßenbelag-Faktoren für rolling_resistance
OBERFLAECHEN_ROLLWIDERSTAND_FAKTOR: dict[str, float] = {
    "asphalt": 1.0,
    "paved": 1.0,
    "concrete": 1.0,
    "compacted": 1.15,
    "gravel": 1.5,
    "unpaved": 1.5,
    "dirt": 1.8,
    "ground": 1.8,
    "grass": 2.2,
    "sand": 2.5,
}

DEFAULT_OBERFLAECHEN_FAKTOR: float = 1.0

# Schwellenwerte für Witterungs-Faktor (siehe f_strassenzustand)
SCHNEEFALL_SCHWELLE_CM: float = 0.5
NIEDERSCHLAG_SCHWELLE_MM: float = 0.5


def f_oberflaeche(surface: str | None) -> float:
    """Rollwiderstands-Multiplikator für den gegebenen Straßenbelag.

    Args:
        surface: Straßenbelag (z. B. "asphalt", "gravel", None).

    Returns:
        Multiplikator für den Rollwiderstandsbeiwert. Bei None oder unbekanntem
        Belag wird der Default-Faktor 1.0 zurückgegeben.
    """
    if surface is None:
        return DEFAULT_OBERFLAECHEN_FAKTOR
    return OBERFLAECHEN_ROLLWIDERSTAND_FAKTOR.get(surface.lower(), DEFAULT_OBERFLAECHEN_FAKTOR)


def berechne_luftdichte(temperature_c: float, pressure_hpa: float) -> float:
    """Berechnet die Luftdichte basierend auf temperature und Luftdruck.

    Verwendet die ideale Gasgleichung: rho = p / (R_spez * T)
    R_spez für trockene Luft ≈ 287.058 J/(kg·K)

    Args:
        temperature_c: temperature in °C
        pressure_hpa: Luftdruck in hPa

    Returns:
        Luftdichte in kg/m³
    """
    # temperature in Kelvin
    t_kelvin = temperature_c + 273.15
    # Luftdruck in Pa
    p_pa = pressure_hpa * 100.0
    # Spezifische Gaskonstante für trockene Luft
    r_spez = 287.058
    return p_pa / (r_spez * t_kelvin)


def f_strassenzustand(
    surface: str | None,
    precipitation_mm: float,
    snowfall_cm: float,
    temperature_c: float,
) -> float:
    """Rollwiderstands-Multiplikator für Straßenbelag UND Witterung.

    Kombiniert den Belag-Faktor (Asphalt, Gravel, etc.) mit einem
    Witterungs-Faktor (trocken, nass, Schnee, Eis).

    Args:
        surface: Straßenbelag (z. B. "asphalt", "gravel", None).
        precipitation_mm: precipitation in mm (Stundensumme).
        snowfall_cm: snowfall in cm (Wasserequivalent).
        temperature_c: temperature in °C (für Eis-Erkennung).

    Returns:
        Kombinierter Multiplikator für den Rollwiderstandsbeiwert.
    """
    # Basis-Faktor für Belag
    f_belag = f_oberflaeche(surface)

    # Witterungs-Faktor
    if snowfall_cm > SCHNEEFALL_SCHWELLE_CM:
        # Schnee auf der Fahrbahn
        f_wetter = 1.8
    elif precipitation_mm > NIEDERSCHLAG_SCHWELLE_MM:
        # Nasse Fahrbahn
        f_wetter = 1.2
    elif temperature_c < 0.0 and precipitation_mm > 0.0:
        # Eisglätte (gefrierender rain bei < 0°C)
        f_wetter = 2.5
    else:
        # Trocken
        f_wetter = 1.0

    return f_belag * f_wetter


def calculate_segment_consumption(
    segment: RouteSegment,
    gradient: SegmentGradient,
    wetter: WeatherSample,
    wind: WindComponents,
    fahrzeug_params: VehicleEnergyParameters,
    construction_zones: Sequence[ConstructionZone] | None = None,
    tempolimit_override_kmh: float | None = None,
) -> SegmentEnergyResult:
    """Berechnet den energy_consumption für ein einzelnes Segment.

    Args:
        segment: Routing-Segment (Geometrie, Länge, speed_limit_kmh, Straßenbelag).
        gradient: Höhenprofil-Gradient für dieses Segment.
        wetter: Wetterdaten (temperature, Wind) zum erwarteten Durchfahrtszeitpunkt.
        wind: Projektion des Windes auf die heading (Gegen-/crosswind).
        fahrzeug_params: Fahrzeugparameter (Masse, cW, rolling_resistance, etc.).
        construction_zones: optionale Liste von construction_zones (überschreibt speed_limit_kmh).
        tempolimit_override_kmh: optionales speed_limit_kmh-Override (für construction_zones-Logik).

    Returns:
        SegmentEnergyResult mit Energiebedarf, recuperation, drive_time_s und speed.

    Algorithmus:
        1. Bestimme Effective Speed (speed_limit_kmh oder Überschreitung, max. 150 km/h).
        2. Berechne Kräfte (rolling_resistance inkl. Straßenbelag-Faktor, air_drag,
           gradient, recuperation).
        3. Konvertiere Kräfte → Leistung → energy über drive_time_s.
        4. Addiere Nebenverbraucher (temperaturabhängig).
        5. Subtrahiere recuperation (physikalisch begrenzt).
    """
    # 1. speed und drive_time_s bestimmen
    speed_limit_kmh: float = float(segment.speed_limit_kmh) if segment.speed_limit_kmh else 120.0
    if tempolimit_override_kmh is not None:
        speed_limit_kmh = min(speed_limit_kmh, tempolimit_override_kmh)

    if construction_zones:
        for bz in construction_zones:
            if bz.speed_limit_kmh is not None:
                speed_limit_kmh = min(speed_limit_kmh, float(bz.speed_limit_kmh))

    # Maximalgeschwindigkeit physikalisch sinnvoll begrenzen
    v_mittel_kmh = min(speed_limit_kmh, 150.0)
    v_mittel_ms = v_mittel_kmh / 3.6

    # drive_time_s berechnen
    s_m = segment.length_m
    t_s = s_m / v_mittel_ms

    # 2. Winkel berechnen
    alpha_rad = math.atan(gradient.steigung_prozent / 100.0)

    # 3. Kräfte berechnen
    # 3.1 rolling_resistance (inkl. Straßenbelag-Faktor UND Witterung)
    f_ober = f_strassenzustand(
        segment.surface,
        wetter.precipitation_mm,
        wetter.snowfall_cm,
        wetter.temperature_c,
    )
    cr_eff = fahrzeug_params.rolling_resistance_coefficient * f_ober
    F_roll = cr_eff * fahrzeug_params.mass_kg * ERDBESCHLEUNIGUNG_MS2 * math.cos(alpha_rad)

    # 3.2 air_drag (inkl. Dachbox-Korrektur)
    cw_eff = fahrzeug_params.drag_coefficient
    if fahrzeug_params.roof_box:
        cw_eff += 0.04  # Konservative Schätzung für Dachbox

    # relative speed zum Luftmassenstrom
    # headwind erhöht den Widerstand (addieren), Rückenwind verringert ihn (subtrahieren)
    v_relativ = v_mittel_ms + wind.gegenwind_ms
    # Bei starkem Rückenwind: Mindestens 50 % der speed annehmen
    v_relativ = max(v_relativ, 0.5 * v_mittel_ms)

    F_luft = (
        0.5
        * berechne_luftdichte(wetter.temperature_c, wetter.pressure_hpa)
        * cw_eff
        * fahrzeug_params.frontal_area_m2
        * (v_relativ**2)
    )

    # 3.3 gradient (Höhenenergie)
    F_steigung = fahrzeug_params.mass_kg * ERDBESCHLEUNIGUNG_MS2 * math.sin(alpha_rad)

    # 4. energy für Bewegung
    # Nur positive gradient verbraucht energy (bei Gefällen gibt der Motor keine energy auf)
    F_bewegung = F_roll + F_luft + max(0.0, F_steigung)
    E_bewegung_j = F_bewegung * s_m

    # 5. HVAC-consumption (temperaturabhängig)
    T_c = wetter.temperature_c
    P_next_to_kw = fahrzeug_params.auxiliary_baseline_kw

    delta_T_min = fahrzeug_params.komforttemperatur_min_c - T_c
    delta_T_max = T_c - fahrzeug_params.komforttemperatur_max_c

    if delta_T_min > 0:
        # Heizung nötig
        P_heiz_kw = fahrzeug_params.heizung_max_kw * min(delta_T_min / 10.0, 1.0)
        P_next_to_kw += P_heiz_kw
    elif delta_T_max > 0:
        # Klima nötig
        P_klima_kw = fahrzeug_params.klimaanlage_max_kw * min(delta_T_max / 10.0, 1.0)
        P_next_to_kw += P_klima_kw

    E_next_to_j = P_next_to_kw * 1000 * t_s  # kW → W, dann * s

    # 6. recuperation (nur bei Verzögerung)
    # Vereinfachung: v_anfang = v_mittel, v_ende reduziert um 10 % der gradient (in m/s Äquivalent)
    v_anfang_ms = v_mittel_ms
    # Verzögerung bei gradient, Beschleunigung bei Gefälle
    aenderung_ms = 0.1 * abs(gradient.steigung_prozent)
    v_end_ms = max(v_anfang_ms - aenderung_ms, 0) if gradient.steigung_prozent > 0 else v_anfang_ms

    E_rekup_j = 0.0
    if v_end_ms < v_anfang_ms:
        # recuperation nur bei Verzögerung
        E_kin_j = 0.5 * fahrzeug_params.mass_kg * (v_anfang_ms**2 - v_end_ms**2)
        E_rekup_j = min(
            E_kin_j * fahrzeug_params.wirkungsgrad_rekuperation,
            REKUPERATION_MAX_POWER_W * t_s,
        )

    # 7. Gesamtergebnis
    E_brutto_j = E_bewegung_j + E_next_to_j

    # recuperation abziehen
    E_gesamt_j = E_brutto_j - E_rekup_j

    # Energiebedarf darf nicht negativ sein (energy aus dem Netz ist nicht negativ)
    energiebedarf_j = max(0.0, E_gesamt_j)

    # Umrechnung von J in kWh (1 kWh = 3.6e6 J)
    energiebedarf_kwh = energiebedarf_j / 3_600_000
    rekuperation_kwh = E_rekup_j / 3_600_000
    energiebedarf_brutto_kwh = E_brutto_j / 3_600_000

    return SegmentEnergyResult(
        segment_index=segment.segment_index,
        energiebedarf_kwh=energiebedarf_kwh,
        rekuperation_kwh=rekuperation_kwh,
        energiebedarf_brutto_kwh=energiebedarf_brutto_kwh,
        speed_ms=v_mittel_ms,
        drive_time_s=t_s,
        segment_length_m=s_m,
    )


def calculate_total_consumption(
    route_segments: Sequence[RouteSegment],
    gradients: Sequence[SegmentGradient],
    wetter_samples: Sequence[WeatherSample],
    wind_components: Sequence[WindComponents],
    fahrzeug_params: VehicleEnergyParameters,
    construction_zones: Sequence[ConstructionZone] | None = None,
) -> list[SegmentEnergyResult]:
    """Berechnet den energy_consumption für eine gesamte Route (Segment-für-Segment).

    Wrapper-Funktion für parallele oder sequenzielle Verarbeitung mehrerer Segmente.
    Wetter- und Winddaten müssen der Reihenfolge der Route Segmente entsprechen.

    Args:
        route_segments: Liste aller Route-Segmente in heading.
        gradients: Liste der SegmentGradient für jedes Segment (muss gleiche Länge haben).
        wetter_samples: Liste der WeatherSample für jedes Segment (muss gleiche Länge haben).
        wind_components: Liste der WindComponents für jedes Segment (muss gleiche Länge haben).
        fahrzeug_params: Fahrzeugparameter für alle Segmente.
        construction_zones: optionale Liste von construction_zones (überschreibt speed_limit_kmh).

    Returns:
        Liste von SegmentEnergyResult für jedes Segment.
    """
    if not route_segments:
        return []

    # Prüfen, dass alle Listen gleiche Länge haben
    n = len(route_segments)
    if len(gradients) != n:
        raise ValueError(f"gradients hat {len(gradients)} Einträge, erwartet {n}")
    if len(wetter_samples) != n:
        raise ValueError(f"wetter_samples hat {len(wetter_samples)} Einträge, erwartet {n}")
    if len(wind_components) != n:
        raise ValueError(f"wind_components hat {len(wind_components)} Einträge, erwartet {n}")

    ergebnisse: list[SegmentEnergyResult] = []

    for i in range(n):
        ergebnis = calculate_segment_consumption(
            segment=route_segments[i],
            gradient=gradients[i],
            wetter=wetter_samples[i],
            wind=wind_components[i],
            fahrzeug_params=fahrzeug_params,
            construction_zones=construction_zones,
        )
        ergebnisse.append(ergebnis)

    return ergebnisse
