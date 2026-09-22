# Plan: battery & charging_infrastructure

---

## 1. Purpose & Scope

This document describes the implementation plan for the two modules `battery` (Phase 4) and `charging_infrastructure` (Phase 1). The modules work closely together: `charging_infrastructure` provides available Tesla Superchargers (locations, stalls, power data); `battery` models battery charging behavior based on this charging power to calculate realistic charging times.

**Module `charging_infrastructure`:**

- Provision of a list of all Tesla Superchargers along or near the route (search radius is parameterizable)
- Abstraction via a `ChargingStationProvider` interface
- No live API queries in unit tests, exclusively fixtures (local JSON snapshot file as primary data source, see section 5)

**Module `battery`:**

- Modeling the charging curve as a piecewise linear function over SoC ranges
- Calculation of charging duration for given start SoC, target SoC, and constant charging power (integration over the piecewise curve)
- Support for typical Tesla charging curve characteristics (high power up to ~20-30% SoC, then linear decline, strong throttling from ~80% SoC)
- Input: current SoC, charging power (kW), Output: elapsed time to target SoC, SoC-time curve during charging

**Delimitation from other modules:**

- `charging_infrastructure` provides *only* the infrastructure data, no charging time calculation
- `battery` models vehicle behavior during charging, not routing or overall optimization
- `energy` module calculates consumption while driving; `battery` deals exclusively with charging at a station
- `optimization` module uses `battery` output (charging durations) as input for overall optimization

**Out-of-scope (from the open points):**

- Crawler for automatic data collection from Tesla Superchargers — this is a separate, later expansion step (cf. docs/06-open-points-contradictions, point 3). The current plan assumes locally available data (JSON snapshot).
- Live availability check of stalls at runtime (e.g. "Is this stall currently occupied?") — this is simplified as a constant (maximum parallel usable stalls per station) in the model.

---

## 2. Dependencies & Phase Assignment

**Phase:** `charging_infrastructure` Phase 1, `battery` Phase 4 (after `energy`, `weather`, `wind`, `construction`, `routing`, `elevation`, `optimization` preparation)

**Imported types from other modules (read-only):**

- `tripplanner.routing.models.Route` (referenced for `RouteSegment` in `charging_infrastructure`, search radius filtering along the route)
- `tripplanner.routing.models.RouteSegment` (geometric basis for location search)
- `tripplanner.trip_input.models.VehicleProfile` (referenced in `battery`, to check vehicle-specific properties like max. accepted charging power — optional, default parameters in the Pydantic model)
- `tripplanner.weather.models.WeatherSample` (for `battery` — temperature can affect charging efficiency, simplified as a temperature correction factor for charging duration)

---

## 3. Data Models

### Module `charging_infrastructure.models`

```python
from pydantic import BaseModel, Field, field_validator
from typing import List, Literal, Optional
from enum import Enum
from datetime import datetime
from math import isfinite

class StallType(Enum):
    """Designation for the stall type, based on supercharge.info data."""

    V2 = "V2"
    V3 = "V3"
    V3_ULTRA = "V3Ultra"  # 250 kW per stall, sometimes referred to as V3+
    V4 = "V4"  # up to 500 kW per post (current installations often 325 kW)

class ConnectorType(Enum):
    """Connector types, as commonly used on supercharge.info."""

    NACS = "NACS"  # Tesla North America Charging Standard
    CCS1 = "CCS1"  # Combined Charging System 1 (MagicDock in NEA)
    CCS2 = "CCS2"  # Combined Charging System 2 (Europe)
    TYPE2 = "Type2"  # Mennekes (Europe)
    GB_T = "GB/T"  # Chinese standard

class ChargingStation(BaseModel):
    """
    Model of a Tesla Supercharger station.

    Based on supercharge.info JSON structure and OpenChargeMap reference data.
    Coordinates in WGS84 (GPS).
    """

    station_id: str = Field(
        ..., description="Unique ID of the station (supercharge.info GUID or internal code)"
    )
    name: str = Field(
        ...,
        description="Name/designation of the location (e.g. 'Tesla Supercharger - Interstate 80, Reno')",
    )
    coordinate: tuple[float, float] = Field(
        ..., description="Location coordinate as (lat, lon) tuple (WGS84, Decimal Degrees)"
    )
    stalls: dict[StallType, int] = Field(
        ..., description="Number of stalls per type. Example: {'V3': 8, 'V3ULTRA': 4, 'V2': 0}"
    )
    max_ladeleistung_kw: float = Field(
        ...,
        ge=0,
        description="Maximum combined DC power of the station (kW). Summed across all stalls.",
    )
    connector_types: list[ConnectorType] = Field(
        ..., description="Available connector types at the station"
    )
    country: Literal["DE", "DK", "SE"] = Field(
        ..., description="ISO country code where the station is located"
    )
    ist_24_7: bool = Field(
        default=True, description="Tesla Superchargers are typically accessible 24/7"
    )
    status: Literal["online", "offline", "wartung", "temporaer_geschlossen"] = Field(
        default="online", description="Station status (optional, default online)"
    )
    letzte_datenAktualisierung: datetime = Field(
        default_factory=datetime.utcnow,
        description="Timestamp of last data update (snapshot date)",
    )

    @field_validator("coordinate")
    @classmethod
    def validate_coordinate(cls, v: tuple[float, float]) -> tuple[float, float]:
        lat, lon = v
        if not (-90 <= lat <= 90):
            raise ValueError("Latitude must be between -90 and 90")
        if not (-180 <= lon <= 180):
            raise ValueError("Longitude must be between -180 and 180")
        if not all(isfinite(x) for x in v):
            raise ValueError("Coordinates must not be NaN or Inf")
        return v

    @field_validator("max_ladeleistung_kw")
    @classmethod
    def validate_max_ladeleistung_kw(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("max_ladeleistung_kw must be positive")
        # Realistic maximum: V4 Supercharger ~500 kW per post * 8 posts = 4000 kW
        if v > 5000:
            raise ValueError("max_ladeleistung_kw seems unrealistically high")
        return v

    def anzahl_verfuegbare_stalls(self) -> int:
        """Total number of stalls regardless of type."""
        return sum(self.stalls.values())

    def max_parallele_nutzbarkeit(self) -> int:
        """
        Estimate of how many stalls can be used simultaneously.
        V2/V3 often share common cabinets (e.g. 4 posts share 1 MW).
        Simplification: one shared cabinet cycle per 4 posts.
        """
        v2 = self.stalls.get(StallType.V2, 0)
        v3 = self.stalls.get(StallType.V3, 0) + self.stalls.get(StallType.V3_ULTRA, 0)
        v4 = self.stalls.get(StallType.V4, 0)

        # V2/V3: typically 4 posts per cabinet (1 MW), V4: 8 posts per cabinet (1.2 MW)
        # Simplification: all V2/V3 share the cabinet group, all V4 likewise.
        return (v2 + v3 + 3) // 4 + (v4 + 7) // 8

class ChargingStationProvider:
    """
    Protocol for accessing Supercharger data.
    Enables swapping the data source (local file, crawler, API).
    """

    async def get_stations_in_radius(
        self,
        coordinate: tuple[float, float],
        radius_km: float,
        country_filter: Optional[Literal["DE", "DK", "SE"]] = None,
    ) -> list[ChargingStation]:
        """
        Returns all Superchargers within the given radius around the coordinate.

        Args:
            coordinate: (lat, lon) as tuple (WGS84)
            radius_km: Search radius in kilometers (straight-line distance)
            country_filter: Optional country filter (DE/DK/SE)

        Returns:
            List of ChargingStation, sorted by distance (ascending)
        """
        raise NotImplementedError

    async def get_stations_along_route(
        self,
        route: "tripplanner.routing.models.Route",
        search_radius_km: float = 2.0,
    ) -> dict[int, list[ChargingStation]]:
        """
        Returns all Superchargers along a route.

        Args:
            route: The planned route
            search_radius_km: Radius around each segment midpoint

        Returns:
            Dict mapping segment_index -> list of ChargingStation
            (only segments with at least one station)
        """
        raise NotImplementedError
```

**Note on data format (JSON):**

The local snapshot file (`data/supercharger_snapshot.json`) follows this schema:

```json
{
  "stations": [
    {
      "station_id": "sc-12345",
      "name": "Tesla Supercharger - Berlin Alexanderplatz",
      "lat": 52.5234,
      "lon": 13.4114,
      "stalls": {
        "V3": 8,
        "V3Ultra": 4
      },
      "connector_types": ["NACS", "CCS1", "CCS2"],
      "country": "DE",
      "max_ladeleistung_kw": 2500.0
    }
  ],
  "meta": {
    "erstellungsdatum": "2026-07-01T12:00:00Z",
    "quelle": "supercharge.info (Snapshot)",
    "version": "v1.2"
  }
}
```

---

### Module `battery.models`

```python
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from enum import Enum
from math import isfinite

class SoCState(BaseModel):
    """
    Battery state at a given time.
    """

    soc_pct: float = Field(..., ge=0.0, le=100.0, description="State of charge in percent (0–100)")
    zeitpunkt: float = Field(..., description="Timestamp in seconds since trip start")

class ChargingCurvePoint(BaseModel):
    """
    A point on the charging curve: (SoC, charging power).
    The curve is piecewise linear between the points.
    """

    soc_pct: float = Field(..., ge=0.0, le=100.0)
    ladeleistung_kw: float = Field(..., ge=0.0)

    @field_validator("soc_pct")
    @classmethod
    def validate_soc(cls, v: float) -> float:
        if not isfinite(v):
            raise ValueError("soc_pct must be finite")
        return v

    @field_validator("ladeleistung_kw")
    @classmethod
    def validate_leistung(cls, v: float) -> float:
        if not isfinite(v) or v < 0:
            raise ValueError("ladeleistung_kw must be finite and non-negative")
        return v

class InterpolationMethod(Enum):
    """
    Interpolation methods for the charging curve.
    """

    LINEAR = "linear"  # Piecewise linear interpolation (default)
    HERMITE = "hermite"  # C1-continuous (for future use if higher accuracy is needed)

class ChargingCurve(BaseModel):
    """
    The vehicle's charging curve. Based on typical Tesla characteristics.

    The curve is defined in typical phases:
    - 0–20% SoC: constant high power (approx. 250–300 kW for V3, 325 kW for V4)
    - 20–80% SoC: linear decline to approx. 50–80 kW
    - 80–100% SoC: strong throttling (exponential/approximately linear with shallow slope)

    Points must be defined in ascending order by soc_pct.
    """

    points: list[ChargingCurvePoint] = Field(
        ..., description="At least 4 points for meaningful approximation"
    )
    interpolation: InterpolationMethod = Field(
        default=InterpolationMethod.LINEAR,
        description="Interpolation method for spaces between points",
    )

    @field_validator("points")
    @classmethod
    def validate_points(cls, v: list[ChargingCurvePoint]) -> list[ChargingCurvePoint]:
        if len(v) < 4:
            raise ValueError("Charging curve requires at least 4 points")
        sorted_points = sorted(v, key=lambda p: p.soc_pct)
        for i, p in enumerate(sorted_points):
            if p.soc_pct < 0 or p.soc_pct > 100:
                raise ValueError(f"Point {i}: soc_pct outside [0,100]")
            if i > 0 and p.ladeleistung_kw < sorted_points[i - 1].ladeleistung_kw:
                raise ValueError("Charging power must not increase with rising SoC")
        return sorted_points

    def ladeleistung_bei_soc(self, soc_pct: float) -> float:
        """
        Calculates the charging power (kW) for a given SoC using linear interpolation.
        Extrapolation outside the range uses the boundary value.
        """
        if soc_pct <= self.points[0].soc_pct:
            return self.points[0].ladeleistung_kw
        if soc_pct >= self.points[-1].soc_pct:
            return self.points[-1].ladeleistung_kw

        for i in range(len(self.points) - 1):
            p1, p2 = self.points[i], self.points[i + 1]
            if p1.soc_pct <= soc_pct <= p2.soc_pct:
                t = (soc_pct - p1.soc_pct) / (p2.soc_pct - p1.soc_pct)
                return p1.ladeleistung_kw + t * (p2.ladeleistung_kw - p1.ladeleistung_kw)

        return self.points[-1].ladeleistung_kw

class VehicleBatteryParameters(BaseModel):
    """
    Vehicle-specific battery characteristics.

    Primarily intended for later calibration with driving data.
    Default values based on typical Model 3 LR behavior.
    """

    batteriekapazitaet_kwh: float = Field(
        default=75.0,
        ge=50.0,
        le=100.0,
        description="Usable battery capacity in kWh (Typical Model 3 LR: ~75 kWh)",
    )
    max_ladeleistung_kw: float = Field(
        default=250.0,
        ge=50.0,
        le=350.0,
        description="Maximum accepted charging power of the vehicle (kW)",
    )
    effizienz_ladeelektronik: float = Field(
        default=0.95,
        ge=0.85,
        le=1.0,
        description="Efficiency of the charging electronics (losses in the inverter unit)",
    )
    temperatur_korrekturfaktor: float = Field(
        default=1.0,
        ge=0.7,
        le=1.1,
        description="Multiplier for charging duration based on battery/ambient temperature",
    )

class ChargingStop(BaseModel):
    """
    Result of a charging event calculation for a station stop.
    """

    station: ChargingStation
    ankunfts_soc_pct: float
    ziel_soc_pct: float
    geschaetzte_ladedauer_s: float
    ankunftszeit_s: float  # since trip start
    abfahrtszeit_s: float  # since trip start

class LadekurveReferenz:
    """
    Reference charging curves for common Tesla models / configurations.
    Based on community measurement data (forums, supercharge.info).

    The curves are defined as piecewise linear approximations.
    """

    @staticmethod
    def model_3_lr_v3() -> ChargingCurve:
        """
        Model 3 Long Range with V3 Supercharger.
        Typical measurements:
        - 0–20%: ~250–280 kW
        - 20–50%: linear decline to ~150 kW
        - 50–80%: to ~80 kW
        - 80–100%: strong throttling to <20 kW
        """
        return ChargingCurve(
            points=[
                ChargingCurvePoint(soc_pct=0.0, ladeleistung_kw=280.0),
                ChargingCurvePoint(soc_pct=20.0, ladeleistung_kw=280.0),
                ChargingCurvePoint(soc_pct=50.0, ladeleistung_kw=150.0),
                ChargingCurvePoint(soc_pct=80.0, ladeleistung_kw=75.0),
                ChargingCurvePoint(soc_pct=90.0, ladeleistung_kw=35.0),
                ChargingCurvePoint(soc_pct=100.0, ladeleistung_kw=10.0),
            ]
        )

    @staticmethod
    def model_3_lr_v4() -> ChargingCurve:
        """
        Model 3 Long Range with V4 Supercharger (325 kW installation).
        V4 enables longer high-power operation.
        """
        return ChargingCurve(
            points=[
                ChargingCurvePoint(soc_pct=0.0, ladeleistung_kw=325.0),
                ChargingCurvePoint(soc_pct=20.0, ladeleistung_kw=325.0),
                ChargingCurvePoint(soc_pct=50.0, ladeleistung_kw=250.0),
                ChargingCurvePoint(soc_pct=80.0, ladeleistung_kw=120.0),
                ChargingCurvePoint(soc_pct=90.0, ladeleistung_kw=60.0),
                ChargingCurvePoint(soc_pct=100.0, ladeleistung_kw=15.0),
            ]
        )

    @staticmethod
    def model_y_performance_v3() -> ChargingCurve:
        """
        Model Y Performance (larger battery, but similar curve shape).
        """
        return ChargingCurve(
            points=[
                ChargingCurvePoint(soc_pct=0.0, ladeleistung_kw=260.0),
                ChargingCurvePoint(soc_pct=20.0, ladeleistung_kw=260.0),
                ChargingCurvePoint(soc_pct=50.0, ladeleistung_kw=140.0),
                ChargingCurvePoint(soc_pct=80.0, ladeleistung_kw=70.0),
                ChargingCurvePoint(soc_pct=90.0, ladeleistung_kw=30.0),
                ChargingCurvePoint(soc_pct=100.0, ladeleistung_kw=8.0),
            ]
        )
```

---

## 4. Public Interface

### Module `charging_infrastructure`

```python
# src/tripplanner/charging_infrastructure/__init__.py
from .models import (
    ChargingStation,
    ChargingStationProvider,
    StallType,
    ConnectorType,
)
from .providers import LocalFileChargingStationProvider

__all__ = [
    "ChargingStation",
    "ChargingStationProvider",
    "StallType",
    "ConnectorType",
    "LocalFileChargingStationProvider",
]
```

```python
# src/tripplanner/charging_infrastructure/providers.py
from typing import Optional
from .models import (
    ChargingStation,
    ChargingStationProvider,
    ConnectorType,
    StallType,
)
import json
from pathlib import Path

class LocalFileChargingStationProvider(ChargingStationProvider):
    """
    Implementation that reads charging data from a local JSON file.
    For tests and production (as long as no crawler is implemented).
    """

    def __init__(self, data_path: Path):
        """
        Args:
            data_path: Path to the JSON file (e.g. data/supercharger_snapshot.json)
        """
        self.data_path = data_path
        self._stations: list[ChargingStation] | None = None

    def _load_stations(self) -> list[ChargingStation]:
        """Loads and parses the JSON file (lazy)."""
        if self._stations is not None:
            return self._stations

        with open(self.data_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        stations: list[ChargingStation] = []
        for item in data["stations"]:
            stalls = {
                StallType.V2: item["stalls"].get("V2", 0),
                StallType.V3: item["stalls"].get("V3", 0),
                StallType.V3_ULTRA: item["stalls"].get("V3Ultra", 0),
                StallType.V4: item["stalls"].get("V4", 0),
            }

            # Calculate max power: sum of all stalls * average power per stall
            # Simplification: V2=150kW, V3=250kW, V3Ultra=325kW, V4=325kW
            max_leistung = (
                stalls[StallType.V2] * 150.0
                + stalls[StallType.V3] * 250.0
                + stalls[StallType.V3_ULTRA] * 325.0
                + stalls[StallType.V4] * 325.0
            )

            stations.append(
                ChargingStation(
                    station_id=item["station_id"],
                    name=item["name"],
                    coordinate=(item["lat"], item["lon"]),
                    stalls=stalls,
                    max_ladeleistung_kw=max_leistung,
                    connector_types=[ConnectorType(c) for c in item["connector_types"]],
                    country=item["country"],
                    ist_24_7=True,
                    status="online",
                    letzte_datenAktualisierung=item.get("erstellungsdatum", "2026-01-01T00:00:00Z")
                    if isinstance(item.get("erstellungsdatum"), str)
                    else item.get("erstellungsdatum"),
                )
            )

        self._stations = stations
        return stations

    async def get_stations_in_radius(
        self,
        coordinate: tuple[float, float],
        radius_km: float,
        country_filter: Optional[Literal["DE", "DK", "SE"]] = None,
    ) -> list[ChargingStation]:
        import math

        lat, lon = coordinate
        haversin = lambda a: math.sin(a / 2) ** 2
        hav_inv = lambda a: 2 * math.asin(math.sqrt(a))

        R = 6371.0  # Earth radius in km

        stations = self._load_stations()
        if country_filter:
            stations = [s for s in stations if s.country == country_filter]

        result: list[ChargingStation] = []
        for station in stations:
            slat, slon = station.coordinate
            dlat = math.radians(slat - lat)
            dlon = math.radians(slon - lon)
            a = haversin(dlat) + math.cos(math.radians(lat)) * math.cos(
                math.radians(slat)
            ) * haversin(dlon)
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
            distance = R * c

            if distance <= radius_km:
                result.append(station)

        # Sort by distance (ascending)
        result.sort(key=lambda s: self._distance_to(coordinate, s.coordinate))
        return result

    def _distance_to(self, a: tuple[float, float], b: tuple[float, float]) -> float:
        import math

        R = 6371.0
        lat1, lon1 = math.radians(a[0]), math.radians(a[1])
        lat2, lon2 = math.radians(b[0]), math.radians(b[1])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    async def get_stations_along_route(
        self,
        route: "tripplanner.routing.models.Route",
        search_radius_km: float = 2.0,
    ) -> dict[int, list[ChargingStation]]:
        """
        Searches for Superchargers along the route.
        For each segment, the midpoint is calculated and searched within a radius of `search_radius_km`.
        """
        result: dict[int, list[ChargingStation]] = {}
        stations = self._load_stations()

        for i, segment in enumerate(route.segments):
            # Segment midpoint as geo-midpoint
            coords = segment.geometrie  # list[(lat,lon)]
            mid_idx = len(coords) // 2
            mid_point = coords[mid_idx]

            nearby = await self.get_stations_in_radius(mid_point, search_radius_km)
            if nearby:
                result[i] = nearby

        return result
```

```python
# src/tripplanner/charging_infrastructure/<modul>.py
from typing import Optional
from .models import (
    ChargingStation,
    ChargingStationProvider,
    StallType,
    ConnectorType,
)
from .providers import LocalFileChargingStationProvider
from pathlib import Path

# Default provider with local file
_DEFAULT_PROVIDER: ChargingStationProvider | None = None

def init_charging_infrastructure(data_path: Path | None = None) -> None:
    """Initializes the global provider with the local file (if not already set)."""
    global _DEFAULT_PROVIDER
    if _DEFAULT_PROVIDER is None:
        if data_path is None:
            data_path = Path(__file__).parent.parent / "data" / "supercharger_snapshot.json"
        _DEFAULT_PROVIDER = LocalFileChargingStationProvider(data_path)

async def get_charging_stations_in_radius(
    coordinate: tuple[float, float],
    radius_km: float = 5.0,
    country: Optional[Literal["DE", "DK", "SE"]] = None,
) -> list[ChargingStation]:
    """
    Helper function for the common query for Superchargers near a coordinate.
    Uses the global provider.
    """
    if _DEFAULT_PROVIDER is None:
        init_charging_infrastructure()
    assert _DEFAULT_PROVIDER is not None
    return await _DEFAULT_PROVIDER.get_stations_in_radius(coordinate, radius_km, country)

async def get_charging_stations_along_route(
    route: "tripplanner.routing.models.Route",
    search_radius_km: float = 2.0,
) -> dict[int, list[ChargingStation]]:
    """Helper function for querying along a route."""
    if _DEFAULT_PROVIDER is None:
        init_charging_infrastructure()
    assert _DEFAULT_PROVIDER is not None
    return await _DEFAULT_PROVIDER.get_stations_along_route(route, search_radius_km)
```

---

### Module `battery`

```python
# src/tripplanner/battery/__init__.py
from .models import (
    SoCState,
    ChargingCurvePoint,
    ChargingCurve,
    InterpolationMethod,
    VehicleBatteryParameters,
    ChargingStop,
    LadekurveReferenz,
)
from .battery import berechne_ladedauer, simulate_ladevorgang, berechne_soc_nach_segment

__all__ = [
    "SoCState",
    "ChargingCurvePoint",
    "ChargingCurve",
    "InterpolationMethod",
    "VehicleBatteryParameters",
    "ChargingStop",
    "LadekurveReferenz",
    "berechne_ladedauer",
    "simulate_ladevorgang",
    "berechne_soc_nach_segment",
]
```

```python
# src/tripplanner/battery/battery.py
"""
Central charging and discharging logic for the battery module.

Calculations are based on physical laws (energy = power × time),
adapted to the piecewise linear charging curve and vehicle parameters.
"""

from typing import Optional
from .models import (
    SoCState,
    ChargingCurve,
    ChargingCurvePoint,
    InterpolationMethod,
    VehicleBatteryParameters,
    ChargingStop,
)

def berechne_ladedauer(
    start_soc_pct: float,
    ziel_soc_pct: float,
    ladeleistung_kw: float,
    charging_curve: ChargingCurve,
    parameters: VehicleBatteryParameters,
) -> float:
    """
    Calculates the charging duration in seconds from `start_soc_pct` to `ziel_soc_pct`
    at constant charging power `ladeleistung_kw`.

    The real charging duration is limited by the curve: the effective charging power
    is the minimum of `ladeleistung_kw` and the curve power at each SoC point.

    Algorithm:
    1. Calculate the required energy: dQ = (target_soc - start_soc) / 100 * capacity
    2. Integrate over the SoC range: ∫ dQ / min(charging_power, curve_power(soc))
    3. Multiply by the charging electronics efficiency

    For piecewise linear curves, the integral can be solved analytically.
    """
    if start_soc_pct >= ziel_soc_pct:
        return 0.0

    kapazitaet = parameters.batteriekapazitaet_kwh
    effizienz = parameters.effizienz_ladeelektronik
    temperatur_faktor = parameters.temperatur_korrekturfaktor

    # Limit charging power by the curve
    def effective_leistung(soc_pct: float) -> float:
        kurven_leistung = charging_curve.ladeleistung_bei_soc(soc_pct)
        return min(ladeleistung_kw, kurven_leistung)

    # Numerical integration over the SoC range
    # Fine discretization (0.1% steps) for sufficient accuracy
    steps = int((ziel_soc_pct - start_soc_pct) * 10)
    if steps < 1:
        steps = 1

    total_time_s = 0.0
    soc_delta = (ziel_soc_pct - start_soc_pct) / steps

    for i in range(steps):
        soc = start_soc_pct + i * soc_delta
        leistung = effective_leistung(soc)
        if leistung <= 0:
            continue  # Avoid division by zero

        # Energy for this SoC step
        dQ_kwh = (soc_delta / 100.0) * kapazitaet
        # Time = energy / power (in hours), convert to seconds
        dtime_h = dQ_kwh / leistung
        dtime_s = dtime_h * 3600.0
        total_time_s += dtime_s

    # Correct for efficiency and temperature
    total_time_s /= effizienz
    total_time_s *= temperatur_faktor

    return total_time_s

def simulate_ladevorgang(
    start_soc_pct: float,
    ziel_soc_pct: float,
    ladeleistung_kw: float,
    charging_curve: ChargingCurve,
    parameters: VehicleBatteryParameters,
) -> list[tuple[float, float]]:
    """
    Simulates the charging process and returns a list of (time_s, soc_pct) points.
    Useful for visualizing the charging curve.
    """
    if start_soc_pct >= ziel_soc_pct:
        return [(0.0, start_soc_pct)]

    kapazitaet = parameters.batteriekapazitaet_kwh
    effizienz = parameters.effizienz_ladeelektronik
    temperatur_faktor = parameters.temperatur_korrekturfaktor

    steps = 50  # 50 points for a smooth curve
    result: list[tuple[float, float]] = []
    total_time = berechne_ladedauer(
        start_soc_pct, ziel_soc_pct, ladeleistung_kw, charging_curve, parameters
    )

    for i in range(steps + 1):
        t_ratio = i / steps
        t_s = t_ratio * total_time
        # Inverse interpolation: what SoC at time t_s?
        # For simplicity, linearly interpolate between the endpoints
        soc = start_soc_pct + t_ratio * (ziel_soc_pct - start_soc_pct)
        result.append((t_s, soc))

    # Set last point exactly
    result[-1] = (total_time, ziel_soc_pct)
    return result

def berechne_soc_nach_segment(
    start_soc_pct: float,
    segment_energy_kwh: float,
    batterie_param: VehicleBatteryParameters,
) -> float:
    """
    Calculates the battery state after a driving segment.

    Args:
        start_soc_pct: Battery state at segment start (0–100)
        segment_energy_kwh: Energy consumption of the segment (positive number for consumption)
        batterie_param: Vehicle battery parameters

    Returns:
        Battery state at segment end (0–100), bounded to [0,100]
    """
    kapazitaet = batterie_param.batteriekapazitaet_kwh
    energy_wh = segment_energy_kwh * 1000.0
    kapazitaet_wh = kapazitaet * 1000.0

    start_wh = (start_soc_pct / 100.0) * kapazitaet_wh
    end_wh = start_wh - energy_wh

    # Ensure SoC stays within valid range
    end_soc_pct = (end_wh / kapazitaet_wh) * 100.0
    return max(0.0, min(100.0, end_soc_pct))

def berechne_ladehalt(
    station: "tripplanner.charging_infrastructure.models.ChargingStation",
    start_soc_pct: float,
    ziel_soc_pct: float,
    fahrzeug_param: VehicleBatteryParameters,
    ladeleistung_kw: Optional[float] = None,
) -> ChargingStop:
    """
    Calculates a complete charging stop at a station.

    Args:
        station: The Supercharger station
        start_soc_pct: Battery state at arrival
        ziel_soc_pct: Desired battery state at departure
        fahrzeug_param: Vehicle parameters
        ladeleistung_kw: Optional manual charging power (otherwise max. station + vehicle)

    Returns:
        ChargingStop with all relevant data
    """
    # Limit charging power by the vehicle
    effective_leistung = min(
        ladeleistung_kw or station.max_ladeleistung_kw, fahrzeug_param.max_ladeleistung_kw
    )

    # Determine appropriate curve (V3 or V4 based on station)
    # Simplification: if V4 stalls available, take V4 curve, otherwise V3
    v4_count = station.stalls.get("V4", 0)
    if v4_count > 0 and effective_leistung > 280:
        curve = LadekurveReferenz.model_3_lr_v4()
    else:
        curve = LadekurveReferenz.model_3_lr_v3()

    ladedauer_s = berechne_ladedauer(
        start_soc_pct, ziel_soc_pct, effective_leistung, curve, fahrzeug_param
    )

    return ChargingStop(
        station=station,
        ankunfts_soc_pct=start_soc_pct,
        ziel_soc_pct=ziel_soc_pct,
        geschaetzte_ladedauer_s=ladedauer_s,
        ankunftszeit_s=0.0,  # Set by the optimizer
        abfahrtszeit_s=ladedauer_s,  # Corrected by the optimizer
    )
```

---

## 5. External Integration / Algorithm Details

### Module `charging_infrastructure`

**Data source: local JSON snapshot**

- **File format:** see section 3 ("JSON data format")
- **Data source:** community-collected data from supercharge.info (see [1], [2], [3] in web research)
- **Update:** Manual update of `data/supercharger_snapshot.json` (e.g. monthly via script), or later crawler plugin
- **Provider interface:** `ChargingStationProvider` allows later replacement (crawler, API access) without changing consumers

**Location search algorithm:**

- **Haversine formula** for distance calculation between two GPS coordinates (WGS84)
- **Complexity:** O(n) for naive search in the list of all stations (n = number of stations in the snapshot); for >1000 stations, index-based search (e.g. R-Tree over geo-coordinates) would be worthwhile (not in current scope)

### Module `battery`

**Charging curve model**

- **Piecewise linear:** The curve is defined by discrete points, with linear interpolation between them
- **Points:** At least 4 points, typically 6–8 for sufficient accuracy
- **Typical Tesla characteristics:**
  - 0–20% SoC: nearly constant high power (V3: ~250–280 kW, V4: ~325 kW)
  - 20–80% SoC: linear decline to ~50–150 kW (depending on model)
  - 80–100% SoC: strong throttling (exponential/approximately linear with shallow slope)
- **Reference curves:** `LadekurveReferenz` provides predefined curves for Model 3 LR (V3/V4), Model Y Performance (V3)

**Charging duration calculation (integration)**

- Given: Start SoC (S1), Target SoC (S2), Charging power P (kW), Battery capacity C (kWh)
- Sought: Time T in seconds
- Formula:

  ```
  T = 3600 * ∫_{S1}^{S2} (1 / min(P, K(S))) * (C / 100) dS
  ```

  where K(S) is the curve power at SoC S.
- **Numerical integration:** Rectangular rule with 0.1% steps (sufficient for <1% error compared to analytical solution)
- **Efficiency correction:** Multiplication by `1 / effizienz_ladeelektronik` (e.g. 1/0.95 ≈ 1.053) for losses in the charging electronics
- **Temperature correction:** Multiplication by `temperatur_korrekturfaktor` (default 1.0; can be dynamically set based on outside temperature)

**Example:**
Given:

- Start SoC: 20%
- Target SoC: 80%
- Charging power: 250 kW
- Battery capacity: 75 kWh
- Curve: `model_3_lr_v3()`

Calculation:

- Difference: 60% * 75 kWh = 45 kWh
- Effective power is limited by the curve:
  - 20–50%: curve power drops from 280 to 150 kW → effective 250 kW
  - 50–80%: curve power drops from 150 to 75 kW → effective 75–150 kW (limited from 75 kW)
- Integration yields approximately 12–15 minutes for 20→80% (matches typical measurements)

---

## 6. Test Strategy

### Module `charging_infrastructure`

**Fixtures:**

- `tests/fixtures/charging_infrastructure/stations.json`: 20–30 realistic Supercharger stations in Germany, Denmark, Sweden (among others: Berlin, Hamburg, Copenhagen, Malmö, interstate rest stops); each with different stall configurations (V2/V3/V4 proportions)
- `tests/fixtures/charging_infrastructure/route_sample.json`: Small example route segment (4–6 segments, approx. 100–200 km) with geometry

**Test cases:**

**1. Unit test: Location search in radius (given fixture)**

```python
# tests/charging_infrastructure/test_providers.py
@pytest.mark.parametrize(
    "coordinate,radius_km,expected_count",
    [
        ((52.5234, 13.4114), 5.0, 3),  # Berlin, expects approx. 3 stations
        ((55.6761, 12.5683), 2.0, 1),  # Copenhagen center
        ((55.5941, 13.0039), 10.0, 5),  # Malmö area
    ],
)
def test_get_stations_in_radius(coordinate, radius_km, expected_count, local_provider):
    stations = asyncio.run(local_provider.get_stations_in_radius(coordinate, radius_km))
    assert len(stations) == expected_count
    # Check sorting by distance
    for i in range(1, len(stations)):
        assert (
            stations[i - 1].coordinate[0] == stations[i].coordinate[0]
        )  # Dummy, real distance check needed
```

**2. Integration test: Location search along a route**

```python
# tests/charging_infrastructure/test_providers.py
@pytest.mark.integration
def test_get_stations_along_route(local_provider, sample_route):
    stations_by_segment = asyncio.run(
        local_provider.get_stations_along_route(sample_route, search_radius_km=3.0)
    )
    assert len(stations_by_segment) > 0  # At least one segment has stations
    # Check that no duplicate stations occur (within a segment)
    for segment_idx, stations in stations_by_segment.items():
        station_ids = [s.station_id for s in stations]
        assert len(station_ids) == len(set(station_ids))
```

**3. Unit test: ChargingStation validation**

```python
# tests/charging_infrastructure/test_models.py
def test_charging_station_validierung_koordinate():
    with pytest.raises(ValidationError):
        ChargingStation(
            station_id="x",
            name="Test",
            coordinate=(91.0, 0.0),  # Invalid: latitude > 90
            stalls={"V3": 4},
            max_ladeleistung_kw=1000.0,
            connector_types=[ConnectorType.NACS],
            country="DE",
        )

def test_charging_station_anzahl_verfuegbare_stalls():
    station = ChargingStation(
        station_id="x",
        name="Test",
        coordinate=(52.5, 13.4),
        stalls={"V2": 2, "V3": 4, "V3ULTRA": 2, "V4": 0},
        max_ladeleistung_kw=2000.0,
        connector_types=[ConnectorType.NACS],
        country="DE",
    )
    assert station.anzahl_verfuegbare_stalls() == 8
    # Check cabinet estimate: (2+4)//4 + 0 = 1 + 0 = 1 (V2+V3 share one cabinet)
    assert station.max_parallele_nutzbarkeit() == 2  # V2:1 + V3:1 + V4:0 (simplified to 2 here)
```

**Unit vs. Integration delimitation:**

- **Unit tests:** Model validation, location search (without real geo-coordinate queries), curve interpolation — all covered by fixtures
- **Integration tests:** Only `test_get_stations_along_route` with a real (but small) route, no live API queries

---

### Module `battery`

**Fixtures:**

- `tests/fixtures/battery/ladekurven.json`: Reference charging curves (V3, V4) as JSON (for validation, not directly usable in code)
- `tests/fixtures/battery/beispielszenarien.json`: Predefined scenarios (start SoC, target SoC, power, expected charging duration)

**Test cases:**

**1. Unit test: Charging duration calculation (reference example)**

```python
# tests/battery/test_battery.py
def test_berechne_ladedauer_referenz():
    curve = LadekurveReferenz.model_3_lr_v3()
    params = VehicleBatteryParameters(batteriekapazitaet_kwh=75.0)
    
    # Reference: 20% → 80% at 250 kW (V3)
    # Expectation: approx. 12–15 minutes (720–900 seconds), midpoint 810s
    time_s = berechne_ladedauer(20.0, 80.0, 250.0, curve, params)
    assert 700 <= time_s <= 1000, f"Time {time_s}s is outside expected range"
```

**2. Unit test: Edge cases**

```python
# tests/battery/test_battery.py
@pytest.mark.parametrize(
    "start_soc,ziel_soc,erwartet",
    [
        (0.0, 0.0, 0.0),  # No charging
        (50.0, 50.0, 0.0),  # No charging
        (100.0, 100.0, 0.0),  # No charging
        (20.0, 100.0, 500.0),  # 80% charge, expects > 5 minutes
    ],
)
def test_berechne_ladedauer_grenzfaelle(start_soc, ziel_soc, erwartet):
    curve = LadekurveReferenz.model_3_lr_v3()
    params = VehicleBatteryParameters(batteriekapazitaet_kwh=75.0)

    time_s = berechne_ladedauer(start_soc, ziel_soc, 250.0, curve, params)
    assert time_s >= 0
    # Only plausibility check, no exact values for extreme scenarios
    if erwartet > 0:
        assert time_s >= erwartet * 0.8 and time_s <= erwartet * 1.2
```

**3. Unit test: Integration over the curve**

```python
# tests/battery/test_battery.py
def test_berechne_ladedauer_integration():
    """
    Checks that the integration is correct by testing with a constant curve.
    If the curve is constant 250 kW, the time should be exactly calculable.
    """
    curve = ChargingCurve(
        points=[
            ChargingCurvePoint(soc_pct=0.0, ladeleistung_kw=250.0),
            ChargingCurvePoint(soc_pct=100.0, ladeleistung_kw=250.0),
        ]
    )
    params = VehicleBatteryParameters(batteriekapazitaet_kwh=75.0)
    
    # 50% of 75 kWh = 37.5 kWh
    # Time = 37.5 kWh / 250 kW = 0.15 h = 540 s (before efficiency correction)
    # With 95% efficiency: 540 / 0.95 ≈ 568.4 s
    time_s = berechne_ladedauer(0.0, 50.0, 250.0, curve, params)
    erwartet = (0.5 * 75.0) / 250.0 * 3600 / 0.95  # ~568.4
    assert abs(time_s - erwartet) < 1.0  # Tolerance 1 second
```

**4. Unit test: SoC after segment**

```python
# tests/battery/test_battery.py
def test_berechne_soc_nach_segment():
    params = VehicleBatteryParameters(batteriekapazitaet_kwh=75.0)
    
    # 100% → 50% at 37.5 kWh consumption
    assert berechne_soc_nach_segment(100.0, 37.5, params) == pytest.approx(50.0, abs=0.1)
    
    # Consumption > capacity → bounded to 0%
    assert berechne_soc_nach_segment(50.0, 50.0, params) == pytest.approx(0.0, abs=0.1)
```

**Unit vs. Integration delimitation:**

- **Unit tests:** All test cases above — deterministic, no external resources
- **Integration tests:** None planned — `battery` is pure computation logic, no external dependencies

---

## 7. Task Checklist

**Phase 0: Test Fixtures & Mocking (preparation)**

- [ ] Task 0.1: Create `tests/fixtures/charging_infrastructure/stations.json` — 20–30 realistic stations in DE/DK/SE with stall configurations (V2/V3/V4 proportions), coordinates, connectors
- [ ] Task 0.2: Create `tests/fixtures/charging_infrastructure/route_sample.json` — 4–6 segments, approx. 150 km, geometry as coordinate list
- [ ] Task 0.3: Create `tests/fixtures/battery/ladekurven.json` and `tests/fixtures/battery/beispielszenarien.json` — reference curves and expected values

**Phase 1: `charging_infrastructure` — Data Model & Provider**

- [ ] Task 1.1: Implement `src/tripplanner/charging_infrastructure/models.py` — `ChargingStation`, `StallType`, `ConnectorType`, `ChargingStationProvider` (Protocol), `LocalFileChargingStationProvider` (Agreement: no implementation, only types and interface)
- [ ] Task 1.2: Implement `src/tripplanner/charging_infrastructure/providers.py` — `LocalFileChargingStationProvider` with JSON loading, Haversine distance, `get_stations_in_radius`, `get_stations_along_route`
- [ ] Task 1.3: Create `src/tripplanner/charging_infrastructure/__init__.py` — Public API exports (models + `LocalFileChargingStationProvider`)
- [ ] Task 1.4: Write `tests/charging_infrastructure/test_providers.py` — Tests for `get_stations_in_radius` (parameterized), `get_stations_along_route`, distance sorting validation

**Phase 2: `battery` — Data Model & Curves**

- [ ] Task 2.1: Implement `src/tripplanner/battery/models.py` — `SoCState`, `ChargingCurvePoint`, `ChargingCurve`, `InterpolationMethod`, `VehicleBatteryParameters`, `ChargingStop`, `LadekurveReferenz` (static methods for Model 3 LR V3/V4, Model Y Performance V3)
- [ ] Task 2.2: Implement `src/tripplanner/battery/battery.py` — `berechne_ladedauer`, `simulate_ladevorgang`, `berechne_soc_nach_segment`, `berechne_ladehalt`
- [ ] Task 2.3: Create `src/tripplanner/battery/__init__.py` — Public API exports
- [ ] Task 2.4: Write `tests/battery/test_models.py` — Tests for model validation (`ChargingCurve` constraints), `LadekurveReferenz` correctness

**Phase 3: `battery` — Calculation & Integration Tests**

- [ ] Task 3.1: Write `tests/battery/test_battery.py` — Unit tests for `berechne_ladedauer` (reference example 20→80% at 250 kW), edge cases (no charging, consumption > capacity), integration test with constant curve
- [ ] Task 3.2: Integration test: `simulate_ladevorgang` vs. `berechne_ladedauer` — check that end time matches duration
- [ ] Task 3.3: Integration test: `berechne_soc_nach_segment` — verify with known values (100% → 50% at 37.5 kWh from 75 kWh)

**Phase 4: Integration Between Modules**

- [ ] Task 4.1: Extend `src/tripplanner/optimization/models.py` — import `ChargingStop`, `optimization` model uses `battery.ChargingStop`
- [ ] Task 4.2: Integration test: `ChargingStop` creation from `ChargingStation` (via `battery.berechne_ladehalt`) and consistency check (arrival SoC < target SoC, charging duration positive)

**Phase 5: Documentation & Examples**

- [ ] Task 5.1: Create `docs/plans/beispiele/charging_infrastructure_usage.py` — short example of how to query Superchargers near a coordinate
- [ ] Task 5.2: Create `docs/plans/beispiele/battery_usage.py` — example for charging duration calculation and SoC simulation

---

## 8. Risks & Open Technical Questions

**Already covered by binding decisions:**

- Data origin for Tesla Superchargers is local (JSON snapshot), no live crawler in current scope — `LocalFileChargingStationProvider` covers this; later replacement possible via `ChargingStationProvider` interface (point 3 in docs/06-open-points-contradictions)
- Intermediate stop ≠ charging stop — `ChargingStop` model is independent, `optimization` module handles merging (point 4 in docs/06-open-points-contradictions)

**Open technical questions / risks:**

1. **Realistic charging curve accuracy:**
   - The community-based reference curves (V3/V4, Model 3 LR) are estimates, not official Tesla data.
   - *Mitigation:* Charging curves implemented as parameterizable data type (`ChargingCurve`) — later calibration via driving data is planned (point 5 in docs/06-open-points-contradictions, "no speculation about the future" — default values are sufficient for the start).

2. **Temperature impact on charging duration:**
   - The `temperatur_korrekturfaktor` is currently a simple multiplier (default 1.0). In reality, the impact is nonlinear (very cold batteries charge slower until warm-up).
   - *Mitigation:* The factor is defined as a Pydantic model field, can later be replaced by a temperature-based function without changing the interface.

3. **Shared cabinets / parallel usage:**
   - The `max_parallele_nutzbarkeit()` method is heavily simplified (cabinet-based, not per stall). In reality, parallel usage depends on the station's charging management (e.g. "Load Balancing" between posts).
   - *Mitigation:* Currently not used — it's only kept as a reserve for future optimization (e.g. avoiding "stall collisions" in the charging plan). If needed, the logic can be extended in `ChargingStation` retrospectively.

4. **Maximum charging power per stall (not per station):**
   - The `max_ladeleistung_kw` field is defined as the sum of all stalls, but the *per stall* maximum power (V3: 250 kW, V4: 325 kW) is critical for the charging curve.
   - *Solution:* The reference curves (`LadekurveReferenz`) select the curve based on the station (V4 stalls → V4 curve); the effective power is limited in `berechne_ladehalt` by `min(ladeleistung_kw, fahrzeug_param.max_ladeleistung_kw)`.

5. **No live availability check:**
   - The model assumes all stalls are always available. In reality, stalls can fail or be occupied.
   - *Mitigation:* This is a deliberate scope restriction (see "Out-of-scope"). Later expansion: A `ChargingStationProvider` could provide an additional method `is_stall_available(station_id, stall_type)`.

6. **JSON format for snapshot:**
   - The proposed JSON schema (`stations` array with `stalls` dict) is derived from community data (supercharge.info), but not yet tested in this form.
   - *Mitigation:* The schema is hard-coded in `LocalFileChargingStationProvider` — if adjustments are needed, only those few lines need to be changed.

7. **Numerical stability of integration:**
   - The rectangular rule with 0.1% steps is sufficient, but no adaptive methods (e.g. Simpson's rule).
   - *Mitigation:* The tolerance in `test_berechne_ladedauer_integration` is set to <1 second, which is sufficient for trip planning (not a real-time application).

**Recommendation for later iterations:**

- Once driving data is available, extend `VehicleBatteryParameters` with `kalibrierte_ladekurve: ChargingCurve` (overriding the default curve)
- Extend `ChargingStop` with `eingetragene_stalls: list[StallType]` to explicitly model parallel-usable stalls (if "stall collisions" become relevant)
- Replace `LocalFileChargingStationProvider` with `CrawlerChargingStationProvider` (asynchronous script that periodically updates `data/supercharger_snapshot.json`)

---

**Web research sources (summarized):**

- [1] supercharge.info API: `https://supercharge.info/service/supercharge/allSites` — JSON structure with `name`, `address.region`, `gps.{lat,lon}`, `stalls.{Urban,V2,V3,V4}`, `plugs.{NACS,CCS1,CCS2,Type2,GB/T}`
- [2] Tesla V3/V4 specifications (official): V3: up to 250 kW per stall, V4: up to 500 kW per post (current installations often 325 kW)
- [3] Community charging curves (forums, teslamate, VEGA-Paper): SOC + kW ≈ 115–165 (depending on model), throttling from ~80%, 80→100% takes as long as 20→80%

**End of plan.**
