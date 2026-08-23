"""Unit tests for `tripplanner.weather.coverage.detect_country`."""

from tripplanner.geo import Coordinate
from tripplanner.weather.coverage import detect_country

BERLIN: Coordinate = (52.5200, 13.4050)
MUNICH: Coordinate = (48.1351, 11.5820)
FLENSBURG: Coordinate = (54.7937, 9.4464)
COPENHAGEN: Coordinate = (55.6761, 12.5683)
RODBY: Coordinate = (54.6564, 11.3928)
BORNHOLM_RONNE: Coordinate = (55.1006, 14.7065)
MALMO: Coordinate = (55.6050, 13.0038)
STOCKHOLM: Coordinate = (59.3293, 18.0686)
PARIS: Coordinate = (48.8566, 2.3522)
OSLO: Coordinate = (59.9139, 10.7522)


def test_detect_country_berlin_is_de() -> None:
    """Berlin (well inside Germany) classifies as DE."""
    assert detect_country(BERLIN) == "DE"


def test_detect_country_munich_is_de() -> None:
    """Munich (southern Germany) classifies as DE."""
    assert detect_country(MUNICH) == "DE"


def test_detect_country_flensburg_is_de() -> None:
    """Flensburg (German side of the DE/DK border) classifies as DE."""
    assert detect_country(FLENSBURG) == "DE"


def test_detect_country_copenhagen_is_dk() -> None:
    """Copenhagen classifies as DK."""
    assert detect_country(COPENHAGEN) == "DK"


def test_detect_country_rodby_is_dk() -> None:
    """Rødby (Danish island south of the DE/DK border band) classifies as DK."""
    assert detect_country(RODBY) == "DK"


def test_detect_country_bornholm_is_dk() -> None:
    """Bornholm (Danish island east of Sweden) classifies as DK, not SE."""
    assert detect_country(BORNHOLM_RONNE) == "DK"


def test_detect_country_malmoe_is_se() -> None:
    """Malmö classifies as SE."""
    assert detect_country(MALMO) == "SE"


def test_detect_country_stockholm_is_se() -> None:
    """Stockholm classifies as SE."""
    assert detect_country(STOCKHOLM) == "SE"


def test_detect_country_paris_is_none() -> None:
    """A location outside all three focus countries returns None."""
    assert detect_country(PARIS) is None


def test_detect_country_oslo_is_none() -> None:
    """Norway is outside the project's DE/DK/SE focus and returns None."""
    assert detect_country(OSLO) is None
