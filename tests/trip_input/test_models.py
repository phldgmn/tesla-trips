"""Unit-Tests für `tripplanner.trip_input.models`."""

from __future__ import annotations

from datetime import datetime

import pytest
from tripplanner.trip_input.models import FerryExclusion, TripRequest, VehicleProfile


@pytest.fixture
def vehicle_profile() -> VehicleProfile:
    """Beispiel-vehicle_profile für TripRequest-Tests."""
    return VehicleProfile(
        mass_kg=1706.0,
        drag_coefficient=0.23,
        frontal_area_m2=2.22,
        rolling_resistance_coefficient=0.011,
        battery_capacity_kwh=62.5,
    )


class TestDriveExclusion:
    """Tests für das FerryExclusion-Pydantic-Modell."""

    def test_ferry_exclusion_requires_name_and_bbox(self) -> None:
        """FerryExclusion benötigt name, bbox_sw, bbox_ne."""
        ausschluss = FerryExclusion(
            name="Rødby (DK) - Puttgarden (D)",
            bbox_sw=(54.50, 11.22),
            bbox_ne=(54.66, 11.36),
        )
        assert ausschluss.name == "Rødby (DK) - Puttgarden (D)"
        assert ausschluss.bbox_sw == (54.50, 11.22)
        assert ausschluss.bbox_ne == (54.66, 11.36)


class TestTripRequestFaehrPraeferenzen:
    """Tests für die Fährvermeidungs-Felder von TripRequest."""

    def test_avoid_all_ferries_defaults_false(self, vehicle_profile: VehicleProfile) -> None:
        """avoid_all_ferries ist standardmäßig False."""
        anfrage = TripRequest(
            start=(52.52, 13.405),
            destination=(53.5511, 9.9937),
            departure_time=datetime(2026, 8, 15, 8, 0, 0),
            vehicle_profile=vehicle_profile,
        )
        assert anfrage.avoid_all_ferries is False
        assert anfrage.avoided_ferries == []

    def test_avoided_ferries_accepts_ferry_exclusion_list(
        self, vehicle_profile: VehicleProfile
    ) -> None:
        """avoided_ferries akzeptiert eine Liste von FerryExclusion."""
        anfrage = TripRequest(
            start=(52.52, 13.405),
            destination=(53.5511, 9.9937),
            departure_time=datetime(2026, 8, 15, 8, 0, 0),
            vehicle_profile=vehicle_profile,
            avoid_all_ferries=True,
            avoided_ferries=[
                FerryExclusion(name="Testfähre", bbox_sw=(54.0, 11.0), bbox_ne=(55.0, 12.0))
            ],
        )
        assert anfrage.avoid_all_ferries is True
        assert len(anfrage.avoided_ferries) == 1
        assert anfrage.avoided_ferries[0].name == "Testfähre"


class TestTripRequestAutobahnPraeferenz:
    """Tests für das highway_preference-Feld von TripRequest."""

    def test_prefer_motorways_defaults_off(self, vehicle_profile: VehicleProfile) -> None:
        """highway_preference ist standardmäßig 'off'."""
        anfrage = TripRequest(
            start=(52.52, 13.405),
            destination=(53.5511, 9.9937),
            departure_time=datetime(2026, 8, 15, 8, 0, 0),
            vehicle_profile=vehicle_profile,
        )
        assert anfrage.highway_preference == "off"

    @pytest.mark.parametrize("level", ["off", "low", "medium", "high"])
    def test_prefer_motorways_accepts_all_levels(
        self, vehicle_profile: VehicleProfile, level: str
    ) -> None:
        """highway_preference akzeptiert 'off', 'low', 'medium' und 'high'."""
        anfrage = TripRequest(
            start=(52.52, 13.405),
            destination=(53.5511, 9.9937),
            departure_time=datetime(2026, 8, 15, 8, 0, 0),
            vehicle_profile=vehicle_profile,
            highway_preference=level,
        )
        assert anfrage.highway_preference == level
