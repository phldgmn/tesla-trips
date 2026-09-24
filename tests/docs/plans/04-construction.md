# Plan: `construction` Module (Phase 1, Construction Zones)

---

## 1. Purpose & Scope

The `construction` module provides active construction zones, closures, and speed limits along a given route for the countries Germany (DE), Denmark (DK), and Sweden (SE).

**Capabilities:**

- Querying current DATEX II feeds from German, Danish, and Swedish National Access Points (NAP)
- Parsing DATEX II XML messages (unified parser for all three countries, since the standard is uniform)
- Extracting construction zone information: affected road sections, speed limits, blocking types, detour information
- Mapping extracted data to the unified `ConstructionZone` model

**Boundary with other modules:**

- `routing`: Calculates the road route; `construction` works on the fixed route, not on OSM data.
- `optimization`: Uses `ConstructionZone` information as input for charging planning (e.g., reduced speed = increased travel time).
- `energy`: Construction zone speed limits factor into energy consumption; the module provides the `ConstructionZone` list, not the calculation.

**Out of scope (later expansion stages):**

- Real-time traffic data (except construction zones included in DATEX II)
- Forecasting construction zone schedules (only current/valid construction zones)
- Integration of non-European countries (no DATEX II standard)
- Crawler for collecting construction zone data (only client for public feeds)

---

## 2. Dependencies & Phase Assignment

**Phase:** Phase 1 (independent data source modules)

**External models (read-only, exact names from the registry):**

- `tripplanner.routing.models.Route`: Input for querying along the route
- `tripplanner.routing.models.RouteSegment`: For mapping segment IDs to construction zones
- `tripplanner.elevation.models.ElevationPoint`: Optional for geo-check (construction zone lies within the radius of a segment)

**Dependencies on other modules:**

- No runtime dependency on other modules — the module is self-contained and can be tested in isolation.
- Only the interface via `models.py` is required.

---

## 3. Data Models

The following Pydantic models define the data structure of the module. All models follow the Google-style docstring convention (see `docs/04-repo-tooling-setup.md`).

```python
# src/tripplanner/construction/models.py

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from pydantic import BaseModel, Field, model_validator
from typing_extensions import Self

class Sperrungstyp(StrEnum):
    """Blocking type according to DATEX II RoadOrCarriagewayOrLaneManagementType."""

    FULLY_CLOSED = "fullyClosed"
    PARTIALLY_CLOSED = "partiallyClosed"
    LANE_CLOSED = "laneClosed"
    TEMPORARY_SPEED_LIMIT = "temporarySpeedLimit"
    REDUCED_LANES = "reducedLanes"
    DETOUR_REQUIRED = "detrourRequired"

class Land(StrEnum):
    """Country codes for construction zones (DE=Germany, DK=Denmark, SE=Sweden)."""

    DE = "DE"
    DK = "DK"
    SE = "SE"

class ConstructionZone(BaseModel):
    """
    A construction zone section with speed limit, blocking type, and detour information.

    Args:
        betroffene_segmente: List of RouteSegment IDs (0-based) affected by the construction zone.
        tempolimit_kmh: Reduced speed limit in km/h (None if no restriction).
        sperrungstyp: Type of blocking/construction zone.
        umleitungshinweis: Free-text detour information (optional).
        land: Country where the construction zone is located.
        gueltig_von: Start time of the construction zone (ISO 8601).
        gueltig_bis: End time of the construction zone (ISO 8601), None if indefinite.
    """

    betroffene_segmente: list[int] = Field(
        description="List of RouteSegment IDs (0-based) affected by the construction zone."
    )
    tempolimit_kmh: Annotated[int | None, Field(ge=0, le=200, default=None)] = Field(
        description="Reduced speed limit in km/h (None if no restriction)."
    )
    sperrungstyp: Sperrungstyp = Field(description="Type of blocking/construction zone.")
    umleitungshinweis: Annotated[str | None, Field(max_length=500, default=None)] = Field(
        description="Free-text detour information (optional)."
    )
    land: Land = Field(description="Country where the construction zone is located.")
    gueltig_von: datetime = Field(description="Start time of the construction zone (ISO 8601).")
    gueltig_bis: Annotated[datetime | None, Field(default=None)] = Field(
        description="End time of the construction zone (ISO 8601), None if indefinite."
    )

    @model_validator(mode="after")
    def validate_tempolimit_for_sperrungstyp(self) -> Self:
        """Validates that tempolimit_kmh is set for certain blocking types."""
        if (
            self.sperrungstyp
            in (
                Sperrungstyp.TEMPORARY_SPEED_LIMIT,
                Sperrungstyp.PARTIALLY_CLOSED,
                Sperrungstyp.LANE_CLOSED,
                Sperrungstyp.REDUCED_LANES,
            )
            and self.tempolimit_kmh is None
        ):
            raise ValueError(
                f"tempolimit_kmh must be set for blocking type {self.sperrungstyp}."
            )
        return self

class ConstructionProvider:
    """
    Protocol for data providers of construction zone information.

    All implementing providers must implement the `fetch_construction_zones` method,
    which returns a list of ConstructionZone for a given route.
    """

    async def fetch_construction_zones(
        self,
        route: "tripplanner.routing.models.Route",
        laender: list[Land],
    ) -> list[ConstructionZone]:
        """Query for construction zones along the route for the specified countries."""
        raise NotImplementedError
```

**Additional internal helper types (not exported, for processing only):**

- `DATEXIIConstructionZone`: Internal Pydantic model for parsing DATEX II XML (see section 5)

---

## 4. Public API

The public API of the module consists of a factory function for creating the provider and the main function `fetch_construction_zones`.

```python
# src/tripplanner/construction/__init__.py
"""Module for construction zone and blocking information along the route."""

from tripplanner.construction.models import (
    ConstructionZone,
    Sperrungstyp,
    Land,
    ConstructionProvider,
)

__all__ = [
    "ConstructionZone",
    "Sperrungstyp",
    "Land",
    "ConstructionProvider",
]
```

```python
# src/tripplanner/construction/providers.py

from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

from httpx import AsyncClient, TimeoutException
from pydantic import BaseModel

import tripplanner.routing.models as routing_models
from tripplanner.construction.models import (
    ConstructionZone,
    Land,
    Sperrungstyp,
    ConstructionProvider,
)

# Configuration for external services (passed by the caller)
DATEXII_ENDPOINTS = {
    Land.DE: "https://www.mobilithek.info/datexii/rest/v2/situations",
    Land.DK: "https://businessservice.dataudveksler.app.vd.dk/api/DateX2",
    Land.SE: "https://api.trafikinfo.trafikverket.se/v1/trafficincidents",
}

class ConstructionProviderConfig(BaseModel):
    """Configuration for the ConstructionProvider."""

    mdm_username: str | None = None  # For Germany (MDM)
    mdm_password: str | None = None  # For Germany (MDM)
    dk_service_account: str | None = None  # For Denmark (Dataudveksleren)
    dk_api_key: str | None = None  # Optional, if required
    tv_api_key: str  # For Sweden (Trafikverket), required
    timeout_seconds: float = 30.0  # HTTP timeout

class ConstructionProviderImpl(ConstructionProvider):
    """
    Implementation of ConstructionProvider with DATEX II feeds for DE, DK, SE.

    The provider uses a common XML parser (DATEX II version 3.3) for all countries,
    as the standard is uniform.
    """

    def __init__(self, config: ConstructionProviderConfig):
        self._config = config
        self._client: AsyncClient | None = None

    async def __aenter__(self) -> "ConstructionProviderImpl":
        self._client = AsyncClient(timeout=self._config.timeout_seconds)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def fetch_construction_zones(
        self,
        route: routing_models.Route,
        laender: list[Land],
    ) -> list[ConstructionZone]:
        """
        Query for construction zones along the route for the specified countries.

        Args:
            route: The route to check (from routing.models).
            laender: List of countries for which to query construction zones.

        Returns:
            List of ConstructionZone objects for the specified countries.

        Raises:
            RuntimeError: If the HTTP client is not initialized.
            TimeoutException: If an HTTP request times out.
        """
        if not self._client:
            raise RuntimeError("ConstructionProviderImpl must be used as async context manager.")

        all_zones: list[ConstructionZone] = []

        for land in laender:
            zones = await self._fetch_landscape_zones(route, land)
            all_zones.extend(zones)

        return all_zones

    async def _fetch_landscape_zones(
        self,
        route: routing_models.Route,
        land: Land,
    ) -> list[ConstructionZone]:
        """Query and parse for a country."""
        endpoint = DATEXII_ENDPOINTS[land]

        # Adjust query parameters for DATEX II feeds
        if land == Land.DE:
            params = self._build_de_params(route)
        elif land == Land.DK:
            params = self._build_dk_params(route)
        else:  # SE
            params = self._build_se_params(route)

        try:
            response = await self._client.get(endpoint, params=params)
            response.raise_for_status()
        except TimeoutException as e:
            # Return empty list on timeout (retry later at higher level)
            return []

        # XML parsing (lxml or xmlschema, see section 5)
        xml_content = response.text
        construction_zones = parse_datexii_xml(xml_content, land)

        # Mapping to ConstructionZone
        return [
            ConstructionZone(
                betroffene_segmente=await self._map_to_segment_ids(zone, route),
                tempolimit_kmh=zone.tempolimit_kmh,
                sperrungstyp=zone.sperrungstyp,
                umleitungshinweis=zone.umleitungshinweis,
                land=zone.land,
                gueltig_von=zone.gueltig_von,
                gueltig_bis=zone.gueltig_bis,
            )
            for zone in construction_zones
        ]

    def _build_de_params(self, route: routing_models.Route) -> dict[str, str]:
        """Parameters for MDM (Germany) DATEX II API."""
        # MDM uses REST API for situations, filtered by validity and geo-coordinates
        # Generate coordinate polygon from route (bounding box)
        coords = self._route_to_bounding_box(route)
        return {
            "query": "roadworks",
            "coords": coords,
            "validity": "active",
            "format": "xml",
        }

    def _build_dk_params(self, route: routing_models.Route) -> dict[str, str]:
        """Parameters for Dataudveksleren (Denmark) DATEX II API."""
        # Denmark uses SOAP or REST with DATEX II XML as payload
        coords = self._route_to_bounding_box(route)
        return {
            "coords": coords,
            "startDate": (datetime.utcnow().isoformat() + "Z"),
            "format": "datex2",
        }

    def _build_se_params(self, route: routing_models.Route) -> dict[str, str]:
        """Parameters for Trafikverket (Sweden) API."""
        # Trafikverket Open API uses JSON POST with DATEX II ontology
        coords = self._route_to_bounding_box(route)
        return {
            "query": f"location geometry '{coords}' AND status 'active' AND type 'roadworks'",
            "key": self._config.tv_api_key,
        }

    def _route_to_bounding_box(self, route: routing_models.Route) -> str:
        """Converts route to bounding box for API query."""
        # Simple implementation: min/max Lat/Lon from geometry
        coords = []
        for segment in route.segments:
            # segment.geometrie contains waypoints as List[Tuple[float, float]]
            for lat, lon in segment.geometrie:
                coords.append((lat, lon))

        if not coords:
            return ""

        lats, lons = zip(*coords)
        return f"{min(lats)},{min(lons)},{max(lats)},{max(lons)}"  # WKT-style BBOX

    async def _map_to_segment_ids(
        self,
        zone: "DATEXIIConstructionZoneInternal",
        route: routing_models.Route,
    ) -> list[int]:
        """
        Maps DATEX II geometry to route segment IDs.

        The algorithm checks whether the construction zone geometry overlaps with segments.
        Since DATEX II uses polygon or line references, an intersection check
        is performed (rasterio or shapely for geometry operations).
        """
        from shapely.geometry import LineString, box

        # Convert construction zone geometry to Shapely
        zone_geom = self._zone_to_geometry(zone)

        betroffene_ids = []
        for idx, segment in enumerate(route.segments):
            # Segment geometry as LineString
            seg_geom = LineString(segment.geometrie)

            # Intersection check
            if zone_geom.intersects(seg_geom):
                betroffene_ids.append(idx)

        return betroffene_ids

    def _zone_to_geometry(self, zone: "DATEXIIConstructionZoneInternal") -> LineString:
        """Converts DATEX II geometry to Shapely LineString."""
        # DATEX II uses gml:LineString or gml:Curve
        # For simple implementation: coordinate list direct mapping
        coords = [(pt.lon, pt.lat) for pt in zone.koordinaten]
        return LineString(coords)
```

---

## 5. External Integration / Algorithm Details

### DATEX II XML Parsing

**Library selection:** `xmlschema` (recommended over `lxml`)

**Rationale:**

- `xmlschema` offers full XSD 1.0/1.1 validation, which is essential for DATEX II structure.
- DATEX II schemas are strictly defined; `xmlschema` delivers data directly as Python dicts/objects (`to_dict()`).
- `lxml` is faster (~42x in validation), but `xmlschema` is more memory-friendly with large XML files (`lazy=True` mode).
- No external C libraries needed (`xmlschema` is pure Python), which simplifies installation.

**DATEX II version:** 3.3 (latest stable; Germany MDM, Denmark Dataudveksleren, Sweden Trafikverket all support DATEX II v3.x).

**Schema download:** <https://docs.datex2.eu/downloads/modelv33/> (DATEXII_3_Situation.xsd, DATEXII_3_Common.xsd, DATEXII_3_LocationReferencing.xsd)

**Mapping DATEX II → `ConstructionZone`:**

| DATEX II Element (Situation) | XML Path | `ConstructionZone` Field |
| ------------------------------ | ---------- | -------------------------- |
| `situationRecord` (xsi:type) | `/situationRecord/@xsi:type` | `Sperrungstyp` (see below) |
| `creationTime` | `/situationRecord/situationRecordCreationTime` | `gueltig_von` (or current time as fallback) |
| `validity` -> `validityTimeSpec` | `/situationRecord/validity/validityTimeSpecification` | `gueltig_von`, `gueltig_bis` |
| `impact` -> `delays` | `/situationRecord/impact/delays/delayBand` | `tempolimit_kmh` (see below) |
| `groupOfLocations` -> `itinerary` | `/situationRecord/groupOfLocations/groupOfLocations` | `koordinaten` (for `_zone_to_geometry`) |
| `source` | `/situationRecord/source/sourceName/value` | `umleitungshinweis` (if present) |

**Blocking type mapping (per DATEX II v3 Roadworks profile):**

| DATEX II `roadworksType` | XML Value | `Sperrungstyp` |
| -------------------------- | ----------- | ---------------- |
| `fullyClosed` | `fullyClosed` | `FULLY_CLOSED` |
| `partiallyClosed` | `partiallyClosed` | `PARTIALLY_CLOSED` |
| `laneClosed` | `laneClosed` | `LANE_CLOSED` |
| `temporarySpeedLimit` | `temporarySpeedLimit` | `TEMPORARY_SPEED_LIMIT` |
| `reducedLanes` | `reducedLanes` | `REDUCED_LANES` |
| `detrourRequired` | `detrourRequired` | `DETROUR_REQUIRED` |

**Direction-aware matching (direction of travel):** A construction zone/event is assigned to a route segment only when it is both spatially close (within 500 m) AND actually applicable in the direction of travel of the route — not just on the same road. For DK/SE zones with LineString geometry, the zone's own bearing (start→end of its coordinates) is compared against `RouteSegment.bearing_deg` of the assigned segment; a match is excluded when the angular difference (folded to `[0°, 180°]`) exceeds 100° — i.e., the zone runs roughly opposite to the route (opposite carriageway on a divided road). For SE zones, an explicit `AffectedDirectionValue` (unless "both directions") is preferred over the geometry heuristic (source is more trustworthy than derivation). **Known limitation:** DE construction zones (Autobahn GmbH API) provide only a single point coordinate — no LineString geometry and no direction/lane field — so direction-aware filtering is not possible for DE; DE matching remains purely distance-based (documented in `_parse_autobahn_roadwork`).

**Length derivation (`laenge_m`):** For DK/SE zones with LineString geometry, the length is computed directly as the geodesic length of that geometry (`tripplanner.geo.geodesic_length_m`). For DE zones (point coordinate only), the length is derived from the assigned route segment (`RouteSegment.laenge_m`), since the source does not provide a length value. `None` if neither can be computed.

**If DATEX II fields are missing (robust default):**

- `gueltig_von`: Fallback to `situationRecordCreationTime` (or UTC now).
- `gueltig_bis`: If `overallEndTime` is missing, set to `None` (indefinite).
- `tempolimit_kmh`: Derive from `delayBand` (e.g. `upToTenMinutes` → 100 km/h, `tenToTwentyMinutes` → 80 km/h, etc.) — concretization in configuration.

**Example parse function (internal):**

```python
# src/tripplanner/construction/parser.py

from datetime import datetime
from pathlib import Path
from typing import cast

import xmlschema

# Load DATEX II v3.3 schema locally (from bundle or URL)
SCHEMA_PATH = Path(__file__).parent / "datexii_3.3" / "DATEXII_3_Situation.xsd"

class DATEXIIConstructionZoneInternal(BaseModel):
    """Internal model for DATEX II parse results."""

    sperrungstyp: str  # DATEX II roadworksType
    gueltig_von: datetime
    gueltig_bis: datetime | None
    koordinaten: list[tuple[float, float]]  # Lat, Lon
    umleitungshinweis: str | None
    tempolimit_kmh: int | None  # derived from delayBand

def parse_datexii_xml(xml_content: str, land: Land) -> list[DATEXIIConstructionZoneInternal]:
    """
    Parse DATEX II XML and extract construction zone information.

    Args:
        xml_content: Raw XML string from DATEX II feed.
        land: Country (for country-specific mapping logic).

    Returns:
        List of DATEXIIConstructionZoneInternal (internal).
    """
    schema = xmlschema.XMLSchema(SCHEMA_PATH)

    # Validation and decoding
    data = schema.to_dict(xml_content, validate=True)

    # Extract situations
    situations = data.get("situation", [])
    if not isinstance(situations, list):
        situations = [situations] if situations else []

    zones: list[DATEXIIConstructionZoneInternal] = []

    for sit in situations:
        sr = sit.get("situationRecord", {})
        if not sr:
            continue

        # Extract type (only roadworks-relevant types)
        xsi_type = sr.get("@xsi:type", "")
        if "Roadworks" not in xsi_type and "MaintenanceWorks" not in xsi_type:
            continue

        # Extract validity times
        validity = sr.get("validity", {})
        time_spec = validity.get("validityTimeSpecification", {})
        start = time_spec.get("overallStartTime")
        end = time_spec.get("overallEndTime")

        gueltig_von = _parse_datetime(start) if start else datetime.utcnow()
        gueltig_bis = _parse_datetime(end) if end else None

        # Extract delay band (for tempolimit_kmh)
        impact = sr.get("impact", {})
        delays = impact.get("delays", {})
        delay_band = delays.get("delayBand")
        tempolimit_kmh = _delay_band_to_speed(delay_band)

        # Extract location (LineString)
        locations = sr.get("groupOfLocations", [])
        koordinaten = []
        for loc in locations:
            # gml:LineString -> coordinates array
            coords_elem = loc.get("lineString", {}).get("coordinates")
            if coords_elem:
                # Format: "lat1 lon1, lat2 lon2, ..."
                pts = [c.strip().split() for c in coords_elem.split(",")]
                for pt in pts:
                    if len(pt) >= 2:
                        # DATEX II: Lat first? Check locale
                        # For German feeds: Lat, Lon; for Swedish/Danish: Lon, Lat
                        if land == Land.DE:
                            lat, lon = float(pt[1]), float(pt[0])
                        else:
                            lat, lon = float(pt[0]), float(pt[1])
                        koordinaten.append((lat, lon))

        # Extract detour information
        source_name = sr.get("source", {}).get("sourceName", {}).get("value", "")
        umleitungshinweis = source_name if source_name else None

        zones.append(
            DATEXIIConstructionZoneInternal(
                sperrungstyp=xsi_type,
                gueltig_von=gueltig_von,
                gueltig_bis=gueltig_bis,
                koordinaten=koordinaten,
                umleitungshinweis=umleitungshinweis,
                tempolimit_kmh=tempolimit_kmh,
            )
        )

    return zones

def _parse_datetime(dt_str: str) -> datetime:
    """Parse ISO 8601 datetime string (DATEX II standard)."""
    # DATEX II uses UTC with Z suffix
    if dt_str.endswith("Z"):
        dt_str = dt_str[:-1] + "+00:00"
    return datetime.fromisoformat(dt_str)

def _delay_band_to_speed(delay_band: str | None) -> int | None:
    """Maps delayBand to tempolimit_kmh (configuration for fine tuning)."""
    if not delay_band:
        return None

    # Example map (configurable via settings)
    band_map = {
        "upToTenMinutes": 100,
        "tenToTwentyMinutes": 80,
        "twentyToFortyMinutes": 60,
        "overFortyMinutes": 40,
    }
    return band_map.get(delay_band, 60)
```

### Concrete API Access Points & Authentication

**Germany (MDM – Mobility Data Marketplace):**

- **Endpoint:** `https://www.mobilithek.info/datexii/rest/v2/situations` (REST API)
- **Authentication:** User registration required (contact via <https://service.mdm-portal.de/mdm-portal-application/_accountRegister.do>)
- **Data format:** XML (DATEX II v3.3)
- **Note:** HTTPS alone is sufficient; DATEX II auth options (C.13, C.14, C.17) not required.
- **Source:** Technical interface description version 1.2.2 (2024-04-04), section "Authentication".

**Denmark (Vejdirektoratet – Dataudveksleren):**

- **Endpoint:** `https://businessservice.dataudveksler.app.vd.dk/api/DateX2` (SOAP or REST)
- **Authentication:** Service account required (documentation at <https://vejdirektoratet.atlassian.net/wiki/spaces/TRC/pages>)
- **Data format:** XML (DATEX II v3.2)
- **Documentation:** TRACÉ Protocol Description Datex II 3.2 (PDF on vejdirektoratet.atlassian.net)
- **Portal:** <https://du-portal-ui.dataudveksler.app.vd.dk/data> (UI for configuration)

**Sweden (Trafikverket – NVDB):**

- **Endpoint:** `https://api.trafikinfo.trafikverket.se/v1/trafficincidents` (Open API)
- **Authentication:** API key required (registration at <https://api.trafikinfo.trafikverket.se/>)
- **Data format:** JSON (DATEX II ontology as underlying model)
- **Note:** All public data is readable without login, but data retrieval requires an account.
- **Cost:** Free, but license agreement required.

---

## 6. Test Strategy

### Fixtures

**Test XML files (in the repo as fixtures):**

- `tests/fixtures/construction/datexii_germany_roadworks_example.xml`: Excerpt from MDM (Germany)
- `tests/fixtures/construction/datexii_denmark_lane_closure.xml`: Example Denmark
- `tests/fixtures/construction/datexii_sweden_temp_limit.xml`: Example Sweden

**Fixture content (example for Germany):**

```xml
<!-- tests/fixtures/construction/datexii_germany_roadworks_example.xml -->
<situation id="DE001" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <situationRecord xsi:type="MaintenanceWorks" id="RWS01_M311126_MAIN_ROADWORKS_D2" version="4">
    <situationRecordCreationTime>2024-03-15T18:03:11Z</situationRecordCreationTime>
    <situationRecordVersionTime>2024-03-15T18:29:27Z</situationRecordVersionTime>
    <probabilityOfOccurrence>probable</probabilityOfOccurrence>
    <source>
      <sourceName>
        <values>
          <value lang="de">WNN-Z [RWS West-Nederland Noord District Zuid]</value>
        </values>
      </sourceName>
    </source>
    <validity>
      <validityStatus>definedByValidityTimeSpec</validityStatus>
      <validityTimeSpecification>
        <overallStartTime>2024-03-20T21:01:00Z</overallStartTime>
        <overallEndTime>2024-03-21T03:00:00Z</overallEndTime>
      </validityTimeSpecification>
    </validity>
    <impact>
      <residualRoadWidth>10.5</residualRoadWidth>
      <delays>
        <delayBand>tenToTwentyMinutes</delayBand>
        <delayTimeValue>300.0</delayTimeValue>
      </delays>
    </impact>
    <groupOfLocations xsi:type="ItineraryByIndexedLocations">
      <route>
        <routeLink>
          <from>
            <location>
              <geographicPosition>
                <latitude>52.5200</latitude>
                <longitude>13.4050</longitude>
              </geographicPosition>
            </location>
          </from>
          <to>
            <location>
              <geographicPosition>
                <latitude>52.5210</latitude>
                <longitude>13.4060</longitude>
              </geographicPosition>
            </location>
          </to>
        </routeLink>
      </route>
    </groupOfLocations>
  </situationRecord>
</situation>
```

### Test Cases

**Test case 1: Parsing a German DATEX II message**

- **Given:** XML file from `datexii_germany_roadworks_example.xml`.
- **When:** `parse_datexii_xml(xml_content, Land.DE)` is called.
- **Then:** Result contains at least one `DATEXIIConstructionZoneInternal` with:
  - `sperrungstyp` = `MaintenanceWorks`
  - `gueltig_von` = `2024-03-20T21:01:00+00:00`
  - `gueltig_bis` = `2024-03-21T03:00:00+00:00`
  - `koordinaten` contains at least 2 points
  - `tempolimit_kmh` = `80` (from `delayBand` = `tenToTwentyMinutes`)

**Test case 2: Mapping to `ConstructionZone` with route intersection**

- **Given:** Fake `ConstructionProviderImpl` with test route (segment 0: coordinates (52.5200,13.4050) → (52.5210,13.4060)), XML fixture.
- **When:** `fetch_construction_zones(route, [Land.DE])` is executed.
- **Then:** Result contains one `ConstructionZone` with `betroffene_segmente = [0]`, `tempolimit_kmh = 80`, `sperrungstyp = Sperrungstyp.TEMPORARY_SPEED_LIMIT`.

**Test case 3: Edge case — no construction zones on route**

- **Given:** Fake `ConstructionProviderImpl` with route that does not intersect any construction zone geometries.
- **When:** `fetch_construction_zones(route, [Land.DE])`.
- **Then:** Empty list `[]` returned.

**Test case 4: Validation of `ConstructionZone.tempolimit_kmh` required**

- **Given:** `ConstructionZone` with `sperrungstyp = Sperrungstyp.PARTIALLY_CLOSED` and `tempolimit_kmh = None`.
- **When:** Instantiation.
- **Then:** Pydantic `ValidationError` is raised (model validation).

### Unit vs. Integration Tests

| Test | File | Decorator |
| ------ | ------- | ----------- |
| `test_parse_datexii_germany()` | `tests/construction/test_parser.py` | — |
| `test_parse_datexii_denmark()` | `tests/construction/test_parser.py` | — |
| `test_parse_datexii_sweden()` | `tests/construction/test_parser.py` | — |
| `test_delay_band_to_speed()` | `tests/construction/test_parser.py` | — |
| `test_fetch_construction_zones_no_overlap()` | `tests/construction/test_providers.py` | `@pytest.mark.integration` |
| `test_fetch_construction_zones_with_overlap()` | `tests/construction/test_providers.py` | `@pytest.mark.integration` |

---

## 7. Task Checklist

- [ ] **Task 1:** Create fixture XML files
  - **Files:** `tests/fixtures/construction/datexii_germany_roadworks_example.xml`, `datexii_denmark_lane_closure.xml`, `datexii_sweden_temp_limit.xml`
  - **Description:** Three concrete DATEX II XML example files with realistic values for construction zones.
  - **Acceptance:** Files validate with `xmlschema` against DATEXII_3_Situation.xsd (v3.3).

- [ ] **Task 2:** Implement `models.py`
  - **Files:** `src/tripplanner/construction/models.py` (create)
  - **Description:** Pydantic models `ConstructionZone`, `Sperrungstyp` (Enum), `Land` (Enum), `ConstructionProvider` (Protocol).
  - **Acceptance:** Models importable, validated through pytest (`test_validierung_construction_zone()`).

- [ ] **Task 3:** Implement parser `parser.py`
  - **Files:** `src/tripplanner/construction/parser.py` (create), `tests/construction/test_parser.py` (create)
  - **Description:** `parse_datexii_xml()`, `_parse_datetime()`, `_delay_band_to_speed()`, internal models.
  - **Acceptance:** 100% coverage for parser functions (pytest-cov).

- [ ] **Task 4:** Implement `providers.py` (core logic)
  - **Files:** `src/tripplanner/construction/providers.py` (create)
  - **Description:** `ConstructionProviderConfig`, `ConstructionProviderImpl`, `fetch_construction_zones()`, `parse_datexii_xml()` call.
  - **Acceptance:** `test_fetch_construction_zones()` in `test_providers.py` runs without HTTP (mock).

- [ ] **Task 5:** Implement `__init__.py` exports
  - **Files:** `src/tripplanner/construction/__init__.py` (create)
  - **Description:** Re-export all public models and protocols.
  - **Acceptance:** `from tripplanner.construction import ConstructionZone` works.

- [ ] **Task 6:** Check and optionally add rasterio/shapely dependency
  - **Files:** `pyproject.toml` (modify)
  - **Description:** Add `shapely` for geometry intersection check (required for `_map_to_segment_ids`).
  - **Acceptance:** `uv add shapely` succeeds, `import shapely` works in Python interpreter.

- [ ] **Task 7:** Document DATEX II feeds
  - **Files:** `docs/plans/04-construction.md` (this file, modify)
  - **Description:** Document concrete endpoints, auth methods, API keys for DE/DK/SE.
  - **Acceptance:** Every developer can test the feeds with the documentation links.

- [ ] **Task 8:** Prepare integration with `optimization` module
  - **Files:** `src/tripplanner/optimization/models.py` (modify), `docs/03-module-specifications.md` (modify)
  - **Description:** `optimization.models.OptimizationConstraints` receives `construction_zones: list[ConstructionZone]` as optional input.
  - **Acceptance:** `optimization` module imports `ConstructionZone` from `tripplanner.construction.models`.

- [ ] **Task 9:** Check ruff + mypy configuration
  - **Files:** `.ruff.toml`, `pyproject.toml` (modify)
  - **Description:** `ruff check src/tripplanner/construction/` and `mypy src/tripplanner/construction/` run without errors.
  - **Acceptance:** 0 ruff errors, 0 mypy errors.

- [ ] **Task 10:** Adjust pytest-cov configuration
  - **Files:** `pyproject.toml` (modify)
  - **Description:** Ensure coverage gate of 85% for `construction` module.
  - **Acceptance:** `pytest --cov=tripplanner.construction tests/construction/` reports ≥85%.

- [ ] **Task 11:** Build/Deployment test
  - **Files:** —
  - **Description:** `uv build` and `pip install .` succeed in venv.
  - **Acceptance:** `from tripplanner.construction import ConstructionZone` works in the new venv.

- [ ] **Task 12:** Run linting / formatting
  - **Files:** All Python files in the `construction` module
  - **Description:** Run `ruff format src/tripplanner/construction/` and `ruff check --fix src/tripplanner/construction/`.
  - **Acceptance:** No formatting errors (ruff clean).

- [ ] **Task 13:** Work through code review checklist
  - **Files:** —
  - **Description:** API conventions (only models.py imports), docstrings (Google-style), type annotations.
  - **Acceptance:** Review confirmed by team member.

- [ ] **Task 14:** Developer documentation (optional, but recommended)
  - **Files:** `docs/07-construction-doku.md` (create)
  - **Description:** How to test the feeds locally (MDM registration, API key from Trafikverket).
  - **Acceptance:** New developers can set up the feeds without support.

---

## 8. Risks & Open Technical Questions

**1. DATEX II API keys/registration (High risk, but solvable):**

- **Problem:** Germany (MDM) and Denmark (Dataudveksleren) require registration/service account; Sweden (Trafikverket) requires an API key.
- **Solution:** Configuration via environment variables (`MDM_USERNAME`, `MDM_PASSWORD`, `DK_SERVICE_ACCOUNT`, `TV_API_KEY`), default values set to dummy strings (test fallback). Clear registration instructions in the documentation.
- **Status:** Documented in Task 7.

**2. Geometry mapping inaccurate (Medium risk):**

- **Problem:** DATEX II uses complex GML geometries (LineString, Curve, Polygon); route segments are simplified; intersection check may fail.
- **Solution:** First release with simple bounding box check (`shapely.box` over all coordinates). Later improvement (distance tolerance, segment-polygon splitting).
- **Status:** Task 4 implements `LineString` intersection; Task 2 allows extension.

**3. Speed limit derivation from `delayBand` (Low risk):**

- **Problem:** DATEX II `delayBand` is qualitative (e.g. `upToTenMinutes`), not an exact speed value.
- **Solution:** Configurable map `_delay_band_to_speed()` in `parser.py`, default values based on German autobahn rules (100 km/h for short delays, 40 km/h for long ones). Later calibration with real driving data.
- **Status:** Implemented as configuration point (no hardcoded values), Task 2-3.

**4. DATEX II version 3.3 vs. 2.3 (Medium risk):**

- **Problem:** Denmark uses DATEX II v3.2; Sweden and Germany support v3.3, but also v2.3. Incompatibilities possible.
- **Solution:** Keep parse logic robust — use only common elements (`SituationRecord`, `validity`, `impact`, `groupOfLocations`). Fallbacks for missing fields (Task 3).
- **Status:** Documented in Task 3; schema download on v3.3.

**5. Rate limits from external APIs (Medium risk):**

- **Problem:** MDM, Dataudveksleren, Trafikverket may enforce rate limits.
- **Solution:** `httpx.AsyncClient` with `Retry` policy (Task 4: `async_retry` wrapper). Integration test with mock (Task 5).
- **Status:** Implemented in Task 4, Task 6 provides for mock test.

**6. No real-time update mechanism (Low risk, out of scope):**

- **Problem:** Feeds are only updated on `fetch_construction_zones()` (no WebSocket/AMQP).
- **Solution:** Accepted; the module is stateless. If needed later, `ConstructionProviderImpl` can be extended with `subscribe()`.
- **Status:** Explicitly described as out of scope in Task 7.

**7. No direction filtering for DE construction zones (Medium risk, accepted):**

- **Problem:** The Autobahn GmbH API provides only a single point coordinate for DE construction zones (no LineString, no direction/lane field). A construction zone on the opposite carriageway can therefore be incorrectly assigned to the route if it falls within the 500 m distance threshold.
- **Solution:** For DK/SE (with LineString geometry or `AffectedDirectionValue`), direction-aware matching is implemented (see section 5). For DE, it remains pure distance matching; this is a documented limitation of the data source, not a gap in the implementation. Should the Autobahn GmbH provide direction data in the future, the same bearing comparison logic can be adopted.
- **Status:** Accepted and documented (`_parse_autobahn_roadwork`), no open task.

---

**End of plan.**
