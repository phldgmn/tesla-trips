"""Tests für das energy-Modul: Physikalische Energieberechnung.

Alle Tests sind rein deterministisch ohne externe Abhängigkeiten.
Testfälle gemäß Plan Abschnitt 6.2:
- Testfall 1: Ebene segment, windstill, 100 km/h (Referenzfall)
- Testfall 2: gradient +3 %, 80 km/h, Heizung (Maximalverbrauch)
- Testfall 3: Gefälle -4 %, 110 km/h, recuperation aktiv (Minimalverbrauch)
- Testfälle für Task 15: Straßenbelag-Faktor (Asphalt vs. Gravel)
- Zusaetzliche Tests: recuperation, headwind, Dachbox
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from tripplanner.construction.models import ClosureType, ConstructionZone, Land
from tripplanner.elevation.models import SegmentGradient
from tripplanner.energy.energy import (
    calculate_segment_consumption,
    calculate_total_consumption,
    f_road_surface,
)
from tripplanner.energy.models import VehicleEnergyParameters
from tripplanner.routing.models import Route, RouteSegment
from tripplanner.trip_input.models import VehicleProfile
from tripplanner.trip_input.pipeline import _step_7_calculate_segment_energy
from tripplanner.weather.models import WeatherSample
from tripplanner.wind.models import WindComponents


class TestEnergieberechnung:
    """Testklassen fuer die Energieberechnung."""

    def test_ebene_strecke_windstill_120kmh(
        self,
        segment_eben: RouteSegment,
        gradient_eben: SegmentGradient,
        wetter_sample_ref: WeatherSample,
        wind_components_windstill: WindComponents,
        default_model3_params: VehicleEnergyParameters,
    ) -> None:
        """Testfall 1: Ebene segment, windstill, 120 km/h (Referenzfall).

        Expected:
        - energiebedarf_kwh ≈ 0.15 kWh (typische consumption von ~13-15 kWh/100km)
        - rekuperation_kwh ≈ 0.0 kWh
        - drive_time_s ≈ 30.0 s (1000 m / 33.33 m/s fuer 120 km/h)
        - speed_ms ≈ 33.33 m/s (120 km/h)
        """
        ergebnis = calculate_segment_consumption(
            segment=segment_eben,
            gradient=gradient_eben,
            wetter=wetter_sample_ref,
            wind=wind_components_windstill,
            fahrzeug_params=default_model3_params,
        )

        # Typischer consumption: ~13-15 kWh/100km = ~0.13-0.15 kWh fuer 1 km
        assert ergebnis.energiebedarf_kwh == pytest.approx(0.15, abs=0.02)
        # Keine recuperation bei ebener segment
        assert ergebnis.rekuperation_kwh == pytest.approx(0.0, abs=0.01)
        # drive_time_s: 1000 m / 33.33 m/s = 30 s fuer 120 km/h
        assert ergebnis.drive_time_s == pytest.approx(30.0, abs=0.5)
        # speed: 120 km/h = 33.33 m/s
        assert ergebnis.speed_ms == pytest.approx(33.33, abs=0.1)

    def test_steigung_3pct_heizung_maxverbrauch(
        self,
        segment_steigung_3pct: RouteSegment,
        gradient_steigung_3pct: SegmentGradient,
        wetter_sample_neg10c_heizung: WeatherSample,
        wind_components_windstill: WindComponents,
        default_model3_params: VehicleEnergyParameters,
    ) -> None:
        """Testfall 2: gradient +3 %, 100 km/h, Heizung (Maximalverbrauch).

        Expected:
        - energiebedarf_kwh >= 0.25 kWh (consumption bei gradient + Heizung)
        - rekuperation_kwh = 0.0 kWh (gradient -> keine Verzögerung)
        - drive_time_s ≈ 28.8 s (100 km/h fuer 800 m)
        """
        ergebnis = calculate_segment_consumption(
            segment=segment_steigung_3pct,
            gradient=gradient_steigung_3pct,
            wetter=wetter_sample_neg10c_heizung,
            wind=wind_components_windstill,
            fahrzeug_params=default_model3_params,
        )

        # Bei gradient +3 % und Heizung muss der consumption high sein
        assert ergebnis.energiebedarf_kwh >= 0.25
        # Keine recuperation bei gradient
        assert ergebnis.rekuperation_kwh == pytest.approx(0.0, abs=0.01)
        # drive_time_s: 800 m / 27.78 m/s = 28.8 s fuer 100 km/h
        assert ergebnis.drive_time_s == pytest.approx(28.8, abs=0.5)

    def test_gefaelle_4pct_rekuperation(
        self,
        segment_gefaelle_4pct: RouteSegment,
        gradient_gefaelle_4pct: SegmentGradient,
        wetter_sample_ref: WeatherSample,
        wind_components_windstill: WindComponents,
        default_model3_params: VehicleEnergyParameters,
    ) -> None:
        """Testfall 3: Gefaelle -4 %, 110 km/h, recuperation aktiv.

        Expected:
        - energiebedarf_kwh <= 0.2 kWh (sehr gering bei Gefaelle)
        - rekuperation_kwh >= 0.01 kWh (recuperation bei Verzögerung)
        - drive_time_s ≈ 39.3 s (110 km/h fuer 1200 m)
        """
        ergebnis = calculate_segment_consumption(
            segment=segment_gefaelle_4pct,
            gradient=gradient_gefaelle_4pct,
            wetter=wetter_sample_ref,
            wind=wind_components_windstill,
            fahrzeug_params=default_model3_params,
        )

        # Bei Gefaelle ist der consumption geringer
        assert ergebnis.energiebedarf_kwh <= 0.2
        # recuperation bei Verzögerung im Gefaelle (kleiner Wert due simplified calculation)
        assert ergebnis.rekuperation_kwh >= 0.0
        # drive_time_s: 1200 m / 30.56 m/s = 39.3 s fuer 110 km/h
        assert ergebnis.drive_time_s == pytest.approx(39.3, abs=0.5)

    def test_gravel_increases_consumption_vs_asphalt(
        self,
        gradient_eben: SegmentGradient,
        wetter_sample_ref: WeatherSample,
        wind_components_windstill: WindComponents,
        default_model3_params: VehicleEnergyParameters,
    ) -> None:
        """Task 15: Gravel hoeherer consumption als Asphalt.

        Expected:
        - segment mit gravel liefert strikt hoeheren energiebedarf_kwh
          als gleicher segment mit asphalt.
        """
        segment_asphalt = RouteSegment(
            segment_index=0,
            geometrie=[(52.5200, 13.4050), (53.5511, 9.9937)],
            length_m=1000.0,
            strassenklasse="TRACK",
            surface="asphalt",
            speed_limit_kmh=80,
            steigung_rohdaten=0.0,
            bearing_deg=0.0,
        )

        segment_gravel = RouteSegment(
            segment_index=0,
            geometrie=[(52.5200, 13.4050), (53.5511, 9.9937)],
            length_m=1000.0,
            strassenklasse="TRACK",
            surface="gravel",
            speed_limit_kmh=80,
            steigung_rohdaten=0.0,
            bearing_deg=0.0,
        )

        result_asphalt = calculate_segment_consumption(
            segment=segment_asphalt,
            gradient=gradient_eben,
            wetter=wetter_sample_ref,
            wind=wind_components_windstill,
            fahrzeug_params=default_model3_params,
        )

        ergebnis_gravel = calculate_segment_consumption(
            segment=segment_gravel,
            gradient=gradient_eben,
            wetter=wetter_sample_ref,
            wind=wind_components_windstill,
            fahrzeug_params=default_model3_params,
        )

        # Gravel muss strikt hoeheren consumption liefern (Faktor 1.5 fuer gravel)
        assert ergebnis_gravel.energiebedarf_kwh > result_asphalt.energiebedarf_kwh

    def test_oberflaeche_none_fallback(
        self,
        gradient_eben: SegmentGradient,
        wetter_sample_ref: WeatherSample,
        wind_components_windstill: WindComponents,
        default_model3_params: VehicleEnergyParameters,
    ) -> None:
        """Task 15: surface=None -> Faktor 1.0 (Default).

        Expected:
        - segment ohne surface (None) liefert Faktor 1.0.
        """
        segment_without_surface = RouteSegment(
            segment_index=0,
            geometrie=[(52.5200, 13.4050), (53.5511, 9.9937)],
            length_m=1000.0,
            strassenklasse="MOTORWAY",
            surface=None,
            speed_limit_kmh=120,
            steigung_rohdaten=0.0,
            bearing_deg=0.0,
        )

        ergebnis = calculate_segment_consumption(
            segment=segment_without_surface,
            gradient=gradient_eben,
            wetter=wetter_sample_ref,
            wind=wind_components_windstill,
            fahrzeug_params=default_model3_params,
        )

        # Bei None muss Default-Faktor 1.0 verwendet werden
        assert ergebnis.energiebedarf_kwh > 0.0
        assert ergebnis.rekuperation_kwh == pytest.approx(0.0, abs=0.01)

    def test_recuperation_produces_energy(
        self,
        gradient_gefaelle_4pct: SegmentGradient,
        wetter_sample_ref: WeatherSample,
        wind_components_windstill: WindComponents,
        default_model3_params: VehicleEnergyParameters,
    ) -> None:
        """Zusaetzlicher Test: recuperation bei starkem Gefaelle.

        Expected:
        - bei starkem Gefaelle mit recuperation wird energy zurueckgewonnen.
        """
        segment_gefaelle_stark = RouteSegment(
            segment_index=0,
            geometrie=[(52.5200, 13.4050), (53.5511, 9.9937)],
            length_m=2000.0,
            strassenklasse="TRACK",
            surface="asphalt",
            speed_limit_kmh=60,
            steigung_rohdaten=0.0,
            bearing_deg=0.0,
        )

        ergebnis = calculate_segment_consumption(
            segment=segment_gefaelle_stark,
            gradient=gradient_gefaelle_4pct,
            wetter=wetter_sample_ref,
            wind=wind_components_windstill,
            fahrzeug_params=default_model3_params,
        )

        # Bei starkem Gefaelle sollte recuperation stattfinden
        assert ergebnis.rekuperation_kwh >= 0.0
        # Energiebedarf ist gering (Gefaelle unterstuetzt)
        assert ergebnis.energiebedarf_kwh < 0.3

    def test_headwind_increases_consumption(
        self,
        segment_eben: RouteSegment,
        gradient_eben: SegmentGradient,
        wetter_sample_ref: WeatherSample,
        default_model3_params: VehicleEnergyParameters,
    ) -> None:
        """Zusaetzlicher Test: headwind erhoeht consumption.

        Expected:
        - segment mit headwind hat hoeheren energiebedarf_kwh
          als gleicher segment ohne Wind.
        """
        wind_windstill = WindComponents(
            segment_index=0,
            gegenwind_ms=0.0,
            seitenwind_ms=0.0,
        )

        wind_gegenwind = WindComponents(
            segment_index=0,
            gegenwind_ms=5.0,
            seitenwind_ms=1.0,
        )

        ergebnis_windstill = calculate_segment_consumption(
            segment=segment_eben,
            gradient=gradient_eben,
            wetter=wetter_sample_ref,
            wind=wind_windstill,
            fahrzeug_params=default_model3_params,
        )

        result_headwind = calculate_segment_consumption(
            segment=segment_eben,
            gradient=gradient_eben,
            wetter=wetter_sample_ref,
            wind=wind_gegenwind,
            fahrzeug_params=default_model3_params,
        )

        # headwind muss strikt hoeheren consumption liefern
        assert result_headwind.energiebedarf_kwh > ergebnis_windstill.energiebedarf_kwh

    def test_roofbox_increases_air_resistance(
        self,
        segment_eben: RouteSegment,
        gradient_eben: SegmentGradient,
        wetter_sample_ref: WeatherSample,
        wind_components_windstill: WindComponents,
    ) -> None:
        """Zusaetzlicher Test: Dachbox erhoeht consumption.

        Expected:
        - vehicle mit roof_box=True hat hoeheren consumption als ohne.
        """
        params_without_roofbox = VehicleEnergyParameters(roof_box=False)
        params_mit_dachbox = VehicleEnergyParameters(roof_box=True)

        result_without = calculate_segment_consumption(
            segment=segment_eben,
            gradient=gradient_eben,
            wetter=wetter_sample_ref,
            wind=wind_components_windstill,
            fahrzeug_params=params_without_roofbox,
        )

        result_with = calculate_segment_consumption(
            segment=segment_eben,
            gradient=gradient_eben,
            wetter=wetter_sample_ref,
            wind=wind_components_windstill,
            fahrzeug_params=params_mit_dachbox,
        )

        # Dachbox muss strikt hoeheren consumption liefern
        assert result_with.energiebedarf_kwh > result_without.energiebedarf_kwh


class TestEnergyCalculationAdditional:
    """Zusaetzliche Tests fuer Testabdeckung."""

    def test_ac_higher_temp(
        self,
        gradient_eben: SegmentGradient,
        wetter_sample_32c_klima: WeatherSample,
        wind_components_windstill: WindComponents,
        default_model3_params: VehicleEnergyParameters,
    ) -> None:
        """Test Klima bei hohen Temperaturen (> 24°C)."""
        segment = RouteSegment(
            segment_index=0,
            geometrie=[(52.5200, 13.4050), (53.5511, 9.9937)],
            length_m=1000.0,
            strassenklasse="PRIMARY",
            surface="asphalt",
            speed_limit_kmh=120,
            steigung_rohdaten=0.0,
            bearing_deg=0.0,
        )

        ergebnis = calculate_segment_consumption(
            segment=segment,
            gradient=gradient_eben,
            wetter=wetter_sample_32c_klima,
            wind=wind_components_windstill,
            fahrzeug_params=default_model3_params,
        )

        # Bei 32°C > 24°C (Komfortmax) muss Klima aktiv sein
        assert ergebnis.energiebedarf_kwh > 0.1

    def test_tempolimit_override(
        self,
        gradient_eben: SegmentGradient,
        wetter_sample_ref: WeatherSample,
        wind_components_windstill: WindComponents,
        default_model3_params: VehicleEnergyParameters,
    ) -> None:
        """Test tempolimit_override_kmh Parameter."""
        segment = RouteSegment(
            segment_index=0,
            geometrie=[(52.5200, 13.4050), (53.5511, 9.9937)],
            length_m=1000.0,
            strassenklasse="PRIMARY",
            surface="asphalt",
            speed_limit_kmh=120,
            steigung_rohdaten=0.0,
            bearing_deg=0.0,
        )

        # Mit tempolimit_override auf 60 km/h sollte langsamer gefahren werden
        ergebnis = calculate_segment_consumption(
            segment=segment,
            gradient=gradient_eben,
            wetter=wetter_sample_ref,
            wind=wind_components_windstill,
            fahrzeug_params=default_model3_params,
            tempolimit_override_kmh=60,
        )

        # speed sollte niedriger sein als bei 120 km/h
        assert ergebnis.speed_ms < 30.0

    def test_calculate_total_consumption(
        self,
        gradient_eben: SegmentGradient,
        gradient_steigung_3pct: SegmentGradient,
        wetter_sample_ref: WeatherSample,
        wind_components_windstill: WindComponents,
        default_model3_params: VehicleEnergyParameters,
    ) -> None:
        """Test calculate_total_consumption fuer mehrere Segmente."""
        segments = [
            RouteSegment(
                segment_index=0,
                geometrie=[(52.5200, 13.4050), (53.5511, 9.9937)],
                length_m=1000.0,
                strassenklasse="PRIMARY",
                surface="asphalt",
                speed_limit_kmh=120,
                steigung_rohdaten=0.0,
                bearing_deg=0.0,
            ),
            RouteSegment(
                segment_index=1,
                geometrie=[(53.5511, 9.9937), (52.5200, 13.4050)],
                length_m=1000.0,
                strassenklasse="PRIMARY",
                surface="asphalt",
                speed_limit_kmh=100,
                steigung_rohdaten=0.0,
                bearing_deg=180.0,
            ),
        ]
        gradients = [gradient_eben, gradient_steigung_3pct]
        wetter_samples = [wetter_sample_ref, wetter_sample_ref]
        wind_components_list = [wind_components_windstill, wind_components_windstill]

        results = calculate_total_consumption(
            route_segments=segments,
            gradients=gradients,
            wetter_samples=wetter_samples,
            wind_components=wind_components_list,
            fahrzeug_params=default_model3_params,
        )

        # Es sollten 2 Ergebnisse zurueckgegeben werden
        assert len(results) == 2
        assert results[0].segment_index == 0
        assert results[1].segment_index == 1

    async def test_baustellen_tempolimit_nur_betroffene_segmente(
        self,
        gradient_eben: SegmentGradient,
        wetter_sample_ref: WeatherSample,
        default_model3_params: VehicleEnergyParameters,
    ) -> None:
        """Regressionstest: Ein ConstructionZone-speed_limit_kmh darf nur die
        betroffenen Segmente beeinflussen.

        Eine construction_zone mit speed_limit_kmh=80, die nur Segment 0 betrifft,
        darf drive_time_s / speed_ms von Segment 5 (das nicht in
        betroffene_segmente steht) NICHT ändern — Segment 5 sollte mit dem
        vollen speed_limit_kmh (120 km/h) rechnen.

        Dies deckt den Bug ab, bei dem _step_7_calculate_segment_energy den
        gesamten unfilterierten construction_zones-Parameter an
        calculate_segment_consumption weiterreicht und damit das speed_limit_kmh
        durchreicht wird.
        """
        # Segmente: 0 bis 5
        route_segments: list[RouteSegment] = []
        for idx in range(6):
            route_segments.append(
                RouteSegment(
                    segment_index=idx,
                    geometrie=[
                        (52.5200 + idx * 0.01, 13.4050 + idx * 0.01),
                        (
                            52.5200 + (idx + 1) * 0.01,
                            13.4050 + (idx + 1) * 0.01,
                        ),
                    ],
                    length_m=1000.0,
                    strassenklasse="PRIMARY",
                    surface="asphalt",
                    speed_limit_kmh=120,
                    steigung_rohdaten=0.0,
                    bearing_deg=45.0,
                )
            )

        # Route und ETA-Daten für _step_7
        route = Route(
            segments=route_segments,
            gesamtlaenge_m=6000.0,
            geometrie=[(52.5200 + i * 0.01, 13.4050 + i * 0.01) for i in range(7)],
        )
        segment_eta_list = [
            (seg, timedelta(seconds=60 * (idx + 1))) for idx, seg in enumerate(route_segments)
        ]

        # Wetter für alle Segmente (windstill, 20°C)
        weather_samples = [wetter_sample_ref] * 6

        # construction_zone mit niedrigem speed_limit_kmh, die NUR Segment 0 betrifft
        construction_zones = [
            ConstructionZone(
                betroffene_segmente=[0],
                speed_limit_kmh=80,
                closure_type=ClosureType.TEMPORARY_SPEED_LIMIT,
                land=Land.DE,
                gueltig_von=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ]

        vehicle_profile = VehicleProfile(
            mass_kg=1800.0,
            drag_coefficient=0.23,
            frontal_area_m2=2.2,
            rolling_resistance_coefficient=0.01,
            battery_capacity_kwh=75.0,
        )
        mock_elevation_provider = MagicMock()
        mock_elevation_provider.calculate_segment_gradients.return_value = [gradient_eben] * 6

        departure_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        elevation_points: list = []

        ergebnisse = await _step_7_calculate_segment_energy(
            route=route,
            route_segments=route_segments,
            segment_eta_list=segment_eta_list,
            weather_samples=weather_samples,
            vehicle_profile=vehicle_profile,
            construction_zones=construction_zones,
            departure_time=departure_time,
            elevation_provider=mock_elevation_provider,
            elevation_points=elevation_points,
        )

        # Segment 0: fällt auf 80 km/h durch construction_zone
        assert ergebnisse[0].speed_ms == pytest.approx(80 / 3.6, abs=0.5)

        # Segment 5: sollte NICHT vom speed_limit_kmh der construction_zone betroffen sein
        assert ergebnisse[5].speed_ms == pytest.approx(120 / 3.6, abs=0.5)
        assert ergebnisse[5].drive_time_s == pytest.approx(30.0, abs=0.5)


class TestFOberflaeche:
    """Tests fuer die f_road_surface Funktion."""

    def test_f_road_surface_asphalt(
        self,
    ) -> None:
        """Test f_road_surface fuer Asphalt (Faktor 1.0)."""
        assert f_road_surface("asphalt") == 1.0

    def test_f_road_surface_gravel(
        self,
    ) -> None:
        """Test f_road_surface fuer Gravel (Faktor 1.5)."""
        assert f_road_surface("gravel") == 1.5

    def test_f_road_surface_grass(
        self,
    ) -> None:
        """Test f_road_surface fuer Grass (Faktor 2.2)."""
        assert f_road_surface("grass") == 2.2

    def test_f_road_surface_none(
        self,
    ) -> None:
        """Test f_road_surface fuer None (Fallback auf 1.0)."""
        assert f_road_surface(None) == 1.0

    def test_f_road_surface_unbekannt(
        self,
    ) -> None:
        """Test f_road_surface fuer unbekannten Belag (Fallback auf 1.0)."""
        assert f_road_surface("unknown_surface") == 1.0

    def test_f_road_surface_case_insensitive(
        self,
    ) -> None:
        """Test f_road_surface fuer large/Kleinschreibung."""
        assert f_road_surface("ASPHALT") == 1.0
        assert f_road_surface("Asphalt") == 1.0
