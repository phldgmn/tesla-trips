"""Tests für die Datenmodelle von `charging_infrastructure`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from tripplanner.charging_infrastructure.models import (
    ChargingPricingTier,
    ChargingStation,
    ChargingStationWithPricing,
    ConnectorType,
    StallType,
)


class TestStallType:
    """Tests für StallType Enum."""

    def test_stall_type_values(self) -> None:
        assert StallType.V2.value == "V2"
        assert StallType.V3.value == "V3"
        assert StallType.V3_ULTRA.value == "V3Ultra"
        assert StallType.V4.value == "V4"

    def test_stall_type_from_string(self) -> None:
        assert StallType("V2") == StallType.V2
        assert StallType("V3") == StallType.V3
        assert StallType("V3Ultra") == StallType.V3_ULTRA
        assert StallType("V4") == StallType.V4


class TestConnectorType:
    """Tests für ConnectorType Enum."""

    def test_connector_type_values(self) -> None:
        assert ConnectorType.NACS.value == "NACS"
        assert ConnectorType.CCS1.value == "CCS1"
        assert ConnectorType.CCS2.value == "CCS2"
        assert ConnectorType.TYPE2.value == "Type2"
        assert ConnectorType.GB_T.value == "GB/T"

    def test_connector_type_from_string(self) -> None:
        assert ConnectorType("NACS") == ConnectorType.NACS
        assert ConnectorType("CCS2") == ConnectorType.CCS2
        assert ConnectorType("Type2") == ConnectorType.TYPE2


class TestChargingStation:
    """Tests für das ChargingStation-Modell."""

    def test_valid_station_creation(self) -> None:
        """Testet Erstellung einer gültigen Station."""
        station = ChargingStation(
            station_id="test-001",
            name="Test Station",
            coordinate=(52.5, 13.4),
            stalls={StallType.V3: 8, StallType.V3_ULTRA: 4},
            max_ladeleistung_kw=3000.0,
            connector_types=[ConnectorType.CCS2, ConnectorType.TYPE2],
            country="DE",
        )
        assert station.station_id == "test-001"
        assert station.name == "Test Station"
        assert station.coordinate == (52.5, 13.4)
        assert station.stalls[StallType.V3] == 8
        assert station.max_ladeleistung_kw == 3000.0
        assert station.country == "DE"
        assert station.ist_24_7 is True  # Default
        assert station.status == "online"  # Default

    def test_invalid_latitude_too_high(self) -> None:
        """Testet, dass Breitengrad > 90 fehlschlägt."""
        with pytest.raises(ValidationError) as exc_info:
            ChargingStation(
                station_id="test",
                name="Test",
                coordinate=(91.0, 0.0),
                stalls={StallType.V3: 4},
                max_ladeleistung_kw=1000.0,
                connector_types=[ConnectorType.CCS2],
                country="DE",
            )
        assert "Breitengrad muss zwischen -90 und 90 liegen" in str(exc_info.value)

    def test_invalid_latitude_too_low(self) -> None:
        """Testet, dass Breitengrad < -90 fehlschlägt."""
        with pytest.raises(ValidationError) as exc_info:
            ChargingStation(
                station_id="test",
                name="Test",
                coordinate=(-91.0, 0.0),
                stalls={StallType.V3: 4},
                max_ladeleistung_kw=1000.0,
                connector_types=[ConnectorType.CCS2],
                country="DE",
            )
        assert "Breitengrad muss zwischen -90 und 90 liegen" in str(exc_info.value)

    def test_invalid_longitude_too_high(self) -> None:
        """Testet, dass Längengrad > 180 fehlschlägt."""
        with pytest.raises(ValidationError) as exc_info:
            ChargingStation(
                station_id="test",
                name="Test",
                coordinate=(0.0, 181.0),
                stalls={StallType.V3: 4},
                max_ladeleistung_kw=1000.0,
                connector_types=[ConnectorType.CCS2],
                country="DE",
            )
        assert "Längengrad muss zwischen -180 und 180 liegen" in str(exc_info.value)

    def test_invalid_longitude_too_low(self) -> None:
        """Testet, dass Längengrad < -180 fehlschlägt."""
        with pytest.raises(ValidationError) as exc_info:
            ChargingStation(
                station_id="test",
                name="Test",
                coordinate=(0.0, -181.0),
                stalls={StallType.V3: 4},
                max_ladeleistung_kw=1000.0,
                connector_types=[ConnectorType.CCS2],
                country="DE",
            )
        assert "Längengrad muss zwischen -180 und 180 liegen" in str(exc_info.value)

    def test_invalid_coordinate_nan(self) -> None:
        """Testet, dass NaN-Koordinaten fehlschlagen."""
        with pytest.raises(ValidationError) as exc_info:
            ChargingStation(
                station_id="test",
                name="Test",
                coordinate=(float("nan"), 0.0),
                stalls={StallType.V3: 4},
                max_ladeleistung_kw=1000.0,
                connector_types=[ConnectorType.CCS2],
                country="DE",
            )
        assert "Breitengrad muss zwischen -90 und 90 liegen" in str(exc_info.value)

    def test_invalid_coordinate_inf(self) -> None:
        """Testet, dass Inf-Koordinaten fehlschlagen."""
        with pytest.raises(ValidationError) as exc_info:
            ChargingStation(
                station_id="test",
                name="Test",
                coordinate=(0.0, float("inf")),
                stalls={StallType.V3: 4},
                max_ladeleistung_kw=1000.0,
                connector_types=[ConnectorType.CCS2],
                country="DE",
            )
        assert "Längengrad muss zwischen -180 und 180 liegen" in str(exc_info.value)

    def test_invalid_max_ladeleistung_zero(self) -> None:
        """Testet, dass max_ladeleistung_kw <= 0 fehlschlägt."""
        with pytest.raises(ValidationError) as exc_info:
            ChargingStation(
                station_id="test",
                name="Test",
                coordinate=(52.5, 13.4),
                stalls={StallType.V3: 4},
                max_ladeleistung_kw=0.0,
                connector_types=[ConnectorType.CCS2],
                country="DE",
            )
        assert "max_ladeleistung_kw muss positiv sein" in str(exc_info.value)

    def test_invalid_max_ladeleistung_too_high(self) -> None:
        """Testet, dass max_ladeleistung_kw > 5000 fehlschlägt."""
        with pytest.raises(ValidationError) as exc_info:
            ChargingStation(
                station_id="test",
                name="Test",
                coordinate=(52.5, 13.4),
                stalls={StallType.V3: 4},
                max_ladeleistung_kw=6000.0,
                connector_types=[ConnectorType.CCS2],
                country="DE",
            )
        assert "unrealistisch hoch" in str(exc_info.value)

    def test_invalid_country(self) -> None:
        """Testet, dass ungültiger Ländercode fehlschlägt."""
        with pytest.raises(ValidationError):
            ChargingStation(
                station_id="test",
                name="Test",
                coordinate=(52.5, 13.4),
                stalls={StallType.V3: 4},
                max_ladeleistung_kw=1000.0,
                connector_types=[ConnectorType.CCS2],
                country="US",  # Nicht erlaubt
            )

    def test_anzahl_verfuegbare_stalls(self, sample_station: ChargingStation) -> None:
        """Testet die Methode anzahl_verfuegbare_stalls."""
        # sample_station: V3: 4, V2: 2 = 6
        assert sample_station.anzahl_verfuegbare_stalls() == 6

    def test_max_parallel_usage(self) -> None:
        """Testet die Methode max_parallel_usability."""
        # V2: 2, V3: 4 -> (2+4+3)//4 + (0+7)//8 = 2 + 0 = 2
        station = ChargingStation(
            station_id="test",
            name="Test",
            coordinate=(52.5, 13.4),
            stalls={StallType.V2: 2, StallType.V3: 4, StallType.V3_ULTRA: 0, StallType.V4: 0},
            max_ladeleistung_kw=1000.0,
            connector_types=[ConnectorType.CCS2],
            country="DE",
        )
        assert station.max_parallel_usability() == 2

        # V2: 4, V3: 4 -> (4+4)//4 = 2
        station2 = ChargingStation(
            station_id="test",
            name="Test",
            coordinate=(52.5, 13.4),
            stalls={StallType.V2: 4, StallType.V3: 4, StallType.V3_ULTRA: 0, StallType.V4: 0},
            max_ladeleistung_kw=2000.0,
            connector_types=[ConnectorType.CCS2],
            country="DE",
        )
        assert station2.max_parallel_usability() == 2

        # V4: 8 -> (8+7)//8 = 1
        station3 = ChargingStation(
            station_id="test",
            name="Test",
            coordinate=(52.5, 13.4),
            stalls={StallType.V2: 0, StallType.V3: 0, StallType.V3_ULTRA: 0, StallType.V4: 8},
            max_ladeleistung_kw=2600.0,
            connector_types=[ConnectorType.CCS2],
            country="DE",
        )
        assert station3.max_parallel_usability() == 1

        # V4: 16 -> (16+7)//8 = 2, but max_ladeleistung_kw <= 5000
        station4 = ChargingStation(
            station_id="test",
            name="Test",
            coordinate=(52.5, 13.4),
            stalls={StallType.V2: 0, StallType.V3: 0, StallType.V3_ULTRA: 0, StallType.V4: 16},
            max_ladeleistung_kw=4000.0,
            connector_types=[ConnectorType.CCS2],
            country="DE",
        )
        assert station4.max_parallel_usability() == 2

    def test_defaults(self) -> None:
        """Testet, dass Defaults korrekt gesetzt werden."""
        station = ChargingStation(
            station_id="test",
            name="Test",
            coordinate=(52.5, 13.4),
            stalls={StallType.V3: 4},
            max_ladeleistung_kw=1000.0,
            connector_types=[ConnectorType.CCS2],
            country="DE",
        )
        assert station.ist_24_7 is True
        assert station.status == "online"
        assert station.letzte_datenAktualisierung is not None


# Fix: Need to import sample_station fixture properly
@pytest.fixture
def sample_station() -> ChargingStation:
    """Einfaches Sample-ChargingStation für Tests."""
    return ChargingStation(
        station_id="test-001",
        name="Test Station",
        coordinate=(52.5, 13.4),
        stalls={StallType.V3: 4, StallType.V2: 2},
        max_ladeleistung_kw=1200.0,
        connector_types=[ConnectorType.CCS2],
        country="DE",
        letzte_datenAktualisierung=datetime.now(UTC),
    )


class TestChargingPricingTier:
    """Tests für das ChargingPricingTier-Modell."""

    def test_minimal_pricing_tier(self) -> None:
        """Testet Minimal-Erzeugung (nur Pflichtfelder)."""
        tier = ChargingPricingTier(
            tier_label="Charging Fees for Tesla Owner",
            currency="EUR",
            amount=0.39,
            unit="kWh",
        )
        assert tier.tier_label == "Charging Fees for Tesla Owner"
        assert tier.currency == "EUR"
        assert tier.amount == 0.39
        assert tier.unit == "kWh"
        assert tier.time_label is None
        assert tier.idle_fee_text is None

    def test_full_pricing_tier(self) -> None:
        """Testet vollständige Erzeugung mit allen Feldern."""
        tier = ChargingPricingTier(
            tier_label="Charging Fees for Other EV",
            time_label="4:00 PM - 8:00 PM",
            currency="DKK",
            amount=1.75,
            unit="kWh",
            idle_fee_text="0.50 EUR/min idle",
        )
        assert tier.time_label == "4:00 PM - 8:00 PM"
        assert tier.currency == "DKK"
        assert tier.amount == 1.75
        assert tier.idle_fee_text == "0.50 EUR/min idle"

    def test_validation_amount_must_be_positive(self) -> None:
        """Negativer oder Null-Preis muss fehlschlagen."""
        with pytest.raises(ValidationError):
            ChargingPricingTier(
                tier_label="Test",
                currency="EUR",
                amount=0,
                unit="kWh",
            )
        with pytest.raises(ValidationError):
            ChargingPricingTier(
                tier_label="Test",
                currency="EUR",
                amount=-1.0,
                unit="kWh",
            )

    def test_validation_currency_length(self) -> None:
        """Falsche Währungslänge muss fehlschlagen."""
        with pytest.raises(ValidationError):
            ChargingPricingTier(
                tier_label="Test",
                currency="EURO",
                amount=0.39,
                unit="kWh",
            )
        with pytest.raises(ValidationError):
            ChargingPricingTier(
                tier_label="Test",
                currency="EU",
                amount=0.39,
                unit="kWh",
            )

    def test_validation_unit_must_be_kwh_or_min(self) -> None:
        """Ungültige Einheit muss fehlschlagen."""
        with pytest.raises(ValidationError):
            ChargingPricingTier(
                tier_label="Test",
                currency="EUR",
                amount=0.39,
                unit="hour",  # type: ignore[arg-type]
            )

    def test_min_unit(self) -> None:
        """Testet Abrechnung pro Minute."""
        tier = ChargingPricingTier(
            tier_label="Idle Fee",
            currency="EUR",
            amount=0.50,
            unit="min",
        )
        assert tier.unit == "min"


class TestChargingStationWithPricing:
    """Tests für das ChargingStationWithPricing-Modell."""

    def test_minimal(self, sample_station: ChargingStation) -> None:
        """Testet Minimal-Erzeugung (nur Station, kein Pricing)."""
        swp = ChargingStationWithPricing(station=sample_station)
        assert swp.station.station_id == "test-001"
        assert swp.pricing == []

    def test_with_pricing(self, sample_station: ChargingStation) -> None:
        """Testet Erzeugung mit Pricing-Daten."""
        tiers = [
            ChargingPricingTier(
                tier_label="Charging Fees for Tesla Owner",
                currency="EUR",
                amount=0.39,
                unit="kWh",
            ),
        ]
        swp = ChargingStationWithPricing(station=sample_station, pricing=tiers)
        assert len(swp.pricing) == 1
        assert swp.pricing[0].amount == 0.39
        assert swp.pricing[0].currency == "EUR"
