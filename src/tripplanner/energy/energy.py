"""Energieverbrauchs-Modul (Phase 3).

Physikalisch fundierte Berechnung des Energieverbrauchs für Elektrofahrzeuge
unter Berücksichtigung von Rollwiderstand, Luftwiderstand, Steigung, Rekuperation
und HVAC-Verbrauch.
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

# Erdbeschleunigung
ERDBESCHLEUNIGUNG_MS2: float = 9.81

# Maximaler Rekuperationswert
REKUPERATION_MAX_POWER_W: float = 80_000  # 80 kW

# Straßenbelag-Faktoren für Rollwiderstand
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


def f_oberflaeche(oberflaeche: str | None) -> float:
    """Rollwiderstands-Multiplikator für den gegebenen Straßenbelag.

    Args:
        oberflaeche: Straßenbelag (z. B. "asphalt", "gravel", None).

    Returns:
        Multiplikator für den Rollwiderstandsbeiwert. Bei None oder unbekanntem
        Belag wird der Default-Faktor 1.0 zurückgegeben.
    """
    if oberflaeche is None:
        return DEFAULT_OBERFLAECHEN_FAKTOR
    return OBERFLAECHEN_ROLLWIDERSTAND_FAKTOR.get(oberflaeche.lower(), DEFAULT_OBERFLAECHEN_FAKTOR)


def calculate_segment_consumption(
    segment: RouteSegment,
    gradient: SegmentGradient,
    wetter: WeatherSample,
    wind: WindComponents,
    fahrzeug_params: VehicleEnergyParameters,
    baustellen: Sequence[ConstructionZone] | None = None,
    tempolimit_override_kmh: float | None = None,
) -> SegmentEnergyResult:
    """Berechnet den Energieverbrauch für ein einzelnes Segment.

    Args:
        segment: Routing-Segment (Geometrie, Länge, Tempolimit, Straßenbelag).
        gradient: Höhenprofil-Gradient für dieses Segment.
        wetter: Wetterdaten (Temperatur, Wind) zum erwarteten Durchfahrtszeitpunkt.
        wind: Projektion des Windes auf die Fahrtrichtung (Gegen-/Seitenwind).
        fahrzeug_params: Fahrzeugparameter (Masse, cW, Rollwiderstand, etc.).
        baustellen: optionale Liste von Baustellen (überschreibt Tempolimit).
        tempolimit_override_kmh: optionales Tempolimit-Override (für Baustellen-Logik).

    Returns:
        SegmentEnergyResult mit Energiebedarf, Rekuperation, Fahrzeit und Geschwindigkeit.

    Algorithmus:
        1. Bestimme Effective Speed (Tempolimit oder Überschreitung, max. 150 km/h).
        2. Berechne Kräfte (Rollwiderstand inkl. Straßenbelag-Faktor, Luftwiderstand,
           Steigung, Rekuperation).
        3. Konvertiere Kräfte → Leistung → Energie über Fahrzeit.
        4. Addiere Nebenverbraucher (temperaturabhängig).
        5. Subtrahiere Rekuperation (physikalisch begrenzt).
    """
    # 1. Geschwindigkeit und Fahrzeit bestimmen
    tempolimit_kmh: float = float(segment.tempolimit_kmh) if segment.tempolimit_kmh else 120.0
    if tempolimit_override_kmh is not None:
        tempolimit_kmh = min(tempolimit_kmh, tempolimit_override_kmh)

    if baustellen:
        for bz in baustellen:
            if bz.tempolimit_kmh is not None:
                tempolimit_kmh = min(tempolimit_kmh, float(bz.tempolimit_kmh))

    # Maximalgeschwindigkeit physikalisch sinnvoll begrenzen
    v_mittel_kmh = min(tempolimit_kmh, 150.0)
    v_mittel_ms = v_mittel_kmh / 3.6

    # Fahrzeit berechnen
    s_m = segment.laenge_m
    t_s = s_m / v_mittel_ms

    # 2. Winkel berechnen
    alpha_rad = math.atan(gradient.steigung_prozent / 100.0)

    # 3. Kräfte berechnen
    # 3.1 Rollwiderstand (inkl. Straßenbelag-Faktor)
    f_ober = f_oberflaeche(segment.oberflaeche)
    cr_eff = fahrzeug_params.rollwiderstandsbeiwert * f_ober
    F_roll = cr_eff * fahrzeug_params.masse_kg * ERDBESCHLEUNIGUNG_MS2 * math.cos(alpha_rad)

    # 3.2 Luftwiderstand (inkl. Dachbox-Korrektur)
    cw_eff = fahrzeug_params.cw_wert
    if fahrzeug_params.dachbox:
        cw_eff += 0.04  # Konservative Schätzung für Dachbox

    # relative Geschwindigkeit zum Luftmassenstrom
    # Gegenwind erhöht den Widerstand (addieren), Rückenwind verringert ihn (subtrahieren)
    v_relativ = v_mittel_ms + wind.gegenwind_ms
    # Bei starkem Rückenwind: Mindestens 50 % der Geschwindigkeit annehmen
    v_relativ = max(v_relativ, 0.5 * v_mittel_ms)

    F_luft = 0.5 * LUFTDICHE_KGM3 * cw_eff * fahrzeug_params.stirnflaeche_m2 * (v_relativ**2)

    # 3.3 Steigung (Höhenenergie)
    F_steigung = fahrzeug_params.masse_kg * ERDBESCHLEUNIGUNG_MS2 * math.sin(alpha_rad)

    # 4. Energie für Bewegung
    # Nur positive Steigung verbraucht Energie (bei Gefällen gibt der Motor keine Energie auf)
    F_bewegung = F_roll + F_luft + max(0.0, F_steigung)
    E_bewegung_j = F_bewegung * s_m

    # 5. HVAC-Verbrauch (temperaturabhängig)
    T_c = wetter.temperatur_c
    P_next_to_kw = fahrzeug_params.nebenverbraucher_baseline_kw

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

    # 6. Rekuperation (nur bei Verzögerung)
    # Vereinfachung: v_anfang = v_mittel, v_ende reduziert um 10 % der Steigung (in m/s Äquivalent)
    v_anfang_ms = v_mittel_ms
    # Verzögerung bei Steigung, Beschleunigung bei Gefälle
    aenderung_ms = 0.1 * abs(gradient.steigung_prozent)
    v_end_ms = max(v_anfang_ms - aenderung_ms, 0) if gradient.steigung_prozent > 0 else v_anfang_ms

    E_rekup_j = 0.0
    if v_end_ms < v_anfang_ms:
        # Rekuperation nur bei Verzögerung
        E_kin_j = 0.5 * fahrzeug_params.masse_kg * (v_anfang_ms**2 - v_end_ms**2)
        E_rekup_j = min(
            E_kin_j * fahrzeug_params.wirkungsgrad_rekuperation,
            REKUPERATION_MAX_POWER_W * t_s,
        )

    # 7. Gesamtergebnis
    E_brutto_j = E_bewegung_j + E_next_to_j

    # Rekuperation abziehen
    E_gesamt_j = E_brutto_j - E_rekup_j

    # Energiebedarf darf nicht negativ sein (Energie aus dem Netz ist nicht negativ)
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
        geschwindigkeit_m_s=v_mittel_ms,
        fahrzeit_s=t_s,
        streckenlaenge_m=s_m,
    )


def calculate_total_consumption(
    route_segments: Sequence[RouteSegment],
    gradients: Sequence[SegmentGradient],
    wetter_samples: Sequence[WeatherSample],
    wind_components: Sequence[WindComponents],
    fahrzeug_params: VehicleEnergyParameters,
    baustellen: Sequence[ConstructionZone] | None = None,
) -> list[SegmentEnergyResult]:
    """Berechnet den Energieverbrauch für eine gesamte Route (Segment-für-Segment).

    Wrapper-Funktion für parallele oder sequenzielle Verarbeitung mehrerer Segmente.
    Wetter- und Winddaten müssen der Reihenfolge der Route Segmente entsprechen.

    Args:
        route_segments: Liste aller Route-Segmente in Fahrtrichtung.
        gradients: Liste der SegmentGradient für jedes Segment (muss gleiche Länge haben).
        wetter_samples: Liste der WeatherSample für jedes Segment (muss gleiche Länge haben).
        wind_components: Liste der WindComponents für jedes Segment (muss gleiche Länge haben).
        fahrzeug_params: Fahrzeugparameter für alle Segmente.
        baustellen: optionale Liste von Baustellen (überschreibt Tempolimit).

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
            baustellen=baustellen,
        )
        ergebnisse.append(ergebnis)

    return ergebnisse
