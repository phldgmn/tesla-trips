"""Unit-Tests für `tripplanner.trip_input.models`."""

from __future__ import annotations

from datetime import datetime

import pytest

from tripplanner.trip_input.models import FerryExclusion, TripRequest, VehicleProfile


@pytest.fixture
def vehicle_profile() -> VehicleProfile:
    """Beispiel-Fahrzeugprofil für TripRequest-Tests."""
    return VehicleProfile(
        masse_kg=1706.0,
        cw_wert=0.23,
        stirnflaeche_m2=2.22,
        rollwiderstandsbeiwert=0.011,
        batteriekapazitaet_kwh=62.5,
    )


class TestDriveExclusion:
    """Tests für das FerryExclusion-Pydantic-Modell."""

    def test_ferry_exclusion_requires_name_and_bbox(self) -> None:
        """FerryExclusion benötigt name, bbox_sw, bbox_no."""
        ausschluss = FerryExclusion(
            name="Rødby (DK) - Puttgarden (D)",
            bbox_sw=(54.50, 11.22),
            bbox_no=(54.66, 11.36),
        )
        assert ausschluss.name == "Rødby (DK) - Puttgarden (D)"
        assert ausschluss.bbox_sw == (54.50, 11.22)
        assert ausschluss.bbox_no == (54.66, 11.36)


class TestTripRequestFaehrPraeferenzen:
    """Tests für die Fährvermeidungs-Felder von TripRequest."""

    def test_avoid_all_ferries_defaults_false(self, vehicle_profile: VehicleProfile) -> None:
        """alle_faehren_vermeiden ist standardmäßig False."""
        anfrage = TripRequest(
            start=(52.52, 13.405),
            ziel=(53.5511, 9.9937),
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0),
            fahrzeugprofil=vehicle_profile,
        )
        assert anfrage.alle_faehren_vermeiden is False
        assert anfrage.vermiedene_faehren == []

    def test_avoided_ferries_accepts_ferry_exclusion_list(
        self, vehicle_profile: VehicleProfile
    ) -> None:
        """vermiedene_faehren akzeptiert eine Liste von FerryExclusion."""
        anfrage = TripRequest(
            start=(52.52, 13.405),
            ziel=(53.5511, 9.9937),
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0),
            fahrzeugprofil=vehicle_profile,
            alle_faehren_vermeiden=True,
            vermiedene_faehren=[
                FerryExclusion(name="Testfähre", bbox_sw=(54.0, 11.0), bbox_no=(55.0, 12.0))
            ],
        )
        assert anfrage.alle_faehren_vermeiden is True
        assert len(anfrage.vermiedene_faehren) == 1
        assert anfrage.vermiedene_faehren[0].name == "Testfähre"


class TestTripRequestAutobahnPraeferenz:
    """Tests für das autobahn_praeferenz-Feld von TripRequest."""

    def test_prefer_motorways_defaults_off(self, vehicle_profile: VehicleProfile) -> None:
        """autobahn_praeferenz ist standardmäßig 'off'."""
        anfrage = TripRequest(
            start=(52.52, 13.405),
            ziel=(53.5511, 9.9937),
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0),
            fahrzeugprofil=vehicle_profile,
        )
        assert anfrage.autobahn_praeferenz == "off"

    @pytest.mark.parametrize("level", ["off", "low", "medium", "high"])
    def test_prefer_motorways_accepts_all_levels(
        self, vehicle_profile: VehicleProfile, level: str
    ) -> None:
        """autobahn_praeferenz akzeptiert 'off', 'low', 'medium' und 'high'."""
        anfrage = TripRequest(
            start=(52.52, 13.405),
            ziel=(53.5511, 9.9937),
            abfahrtszeit=datetime(2026, 8, 15, 8, 0, 0),
            fahrzeugprofil=vehicle_profile,
            autobahn_praeferenz=level,
        )
        assert anfrage.autobahn_praeferenz == level
