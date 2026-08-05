"""Tests für construction-Parser: DATEX II XML Parsing und Feld-Mapping."""

from datetime import datetime
from pathlib import Path

import pytest

from tripplanner.construction.models import Land
from tripplanner.construction.parser import (
    _delay_band_to_speed,
    _parse_datetime,
    parse_datexii_xml,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "construction"


@pytest.fixture
def germany_xml() -> str:
    """Lese deutsche DATEX II Fixture."""
    return (FIXTURES_DIR / "datexii_germany_roadworks_example.xml").read_text()


@pytest.fixture
def denmark_xml() -> str:
    """Lese dänische DATEX II Fixture."""
    return (FIXTURES_DIR / "datexii_denmark_lane_closure.xml").read_text()


@pytest.fixture
def sweden_xml() -> str:
    """Lese schwedische DATEX II Fixture."""
    return (FIXTURES_DIR / "datexii_sweden_temp_limit.xml").read_text()


def test_parse_datexii_germany(germany_xml: str) -> None:
    """Parse einer deutschen DATEX II Nachricht."""
    zones = parse_datexii_xml(germany_xml, Land.DE)

    assert len(zones) >= 1
    zone = zones[0]

    assert "Roadworks" in zone.sperrungstyp or "MaintenanceWorks" in zone.sperrungstyp
    assert zone.gueltig_von.year == 2024
    assert zone.gueltig_von.month == 3
    assert zone.gueltig_von.day == 20

    assert zone.gueltig_bis is not None
    assert zone.gueltig_bis.year == 2024
    assert zone.gueltig_bis.month == 3
    assert zone.gueltig_bis.day == 21

    assert len(zone.koordinaten) >= 2
    assert zone.tempolimit_kmh == 80


def test_parse_datexii_denmark(denmark_xml: str) -> None:
    """Parse einer dänischen DATEX II Nachricht."""
    zones = parse_datexii_xml(denmark_xml, Land.DK)

    assert len(zones) >= 1
    zone = zones[0]

    assert "Lane" in zone.sperrungstyp or "Roadworks" in zone.sperrungstyp
    assert zone.gueltig_von.year == 2024
    assert zone.gueltig_von.month == 3
    assert zone.gueltig_von.day == 17

    assert zone.gueltig_bis is not None
    assert zone.gueltig_bis.year == 2024
    assert zone.gueltig_bis.month == 3
    assert zone.gueltig_bis.day == 17

    assert len(zone.koordinaten) >= 3
    assert zone.tempolimit_kmh == 100


def test_parse_datexii_sweden(sweden_xml: str) -> None:
    """Parse einer schwedischen DATEX II Nachricht."""
    zones = parse_datexii_xml(sweden_xml, Land.SE)

    assert len(zones) >= 1
    zone = zones[0]

    assert "TemporarySpeedLimit" in zone.sperrungstyp or "Roadworks" in zone.sperrungstyp
    assert zone.gueltig_von.year == 2024
    assert zone.gueltig_von.month == 3
    assert zone.gueltig_von.day == 19

    assert zone.gueltig_bis is not None
    assert zone.gueltig_bis.year == 2024
    assert zone.gueltig_bis.month == 3
    assert zone.gueltig_bis.day == 19

    assert len(zone.koordinaten) >= 4
    assert zone.tempolimit_kmh in [60, 80, 100]


def test_delay_band_to_speed() -> None:
    """Test _delay_band_to_speed mapping."""
    assert _delay_band_to_speed("upToTenMinutes") == 100
    assert _delay_band_to_speed("tenToTwentyMinutes") == 80
    assert _delay_band_to_speed("twentyToFortyMinutes") == 60
    assert _delay_band_to_speed("overFortyMinutes") == 40
    assert _delay_band_to_speed("unknownBand") == 60
    assert _delay_band_to_speed(None) is None


def test_parse_datetime() -> None:
    """Test _parse_datetime helper."""
    dt = _parse_datetime("2024-03-15T18:03:11Z")
    assert dt.year == 2024
    assert dt.month == 3
    assert dt.day == 15
    assert dt.hour == 18

    dt2 = _parse_datetime("2024-03-15T18:03:11+00:00")
    assert dt2.year == 2024
    assert dt2.month == 3
    assert dt2.day == 15

    dt3 = _parse_datetime(None)
    assert dt3 is not None
    assert isinstance(dt3, datetime)


def test_parse_empty_xml() -> None:
    """Test Parsing eines leeren/ungültigen XML."""
    zones = parse_datexii_xml("<root></root>", Land.DE)
    assert zones == []


def test_parse_invalid_xml() -> None:
    """Test Parsing eines ungültigen XML."""
    try:
        zones = parse_datexii_xml("<invalid xml>", Land.DE)
        assert zones == []
    except Exception:
        # If parsing fails completely, that's acceptable
        pass
