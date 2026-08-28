# Plan: `construction`-Modul (Phase 1, Baustellen)

---

## 1. Zweck & Scope

Das `construction`-Modul liefert aktive Baustellen, Sperrungen und Tempolimits entlang einer gegebenen Route für die Länder Deutschland (DE), Dänemark (DK) und Schweden (SE).

**Leistungen:**

- Abfrage aktueller DATEX II-Feeds von deutschen, dänischen und schwedischen Nationalen Zugangspunkten (NAP)
- Parsing von DATEX II XML-Nachrichten (einheitlicher Parser für alle drei Länder, da einheitlicher Standard)
- Extraktion von Baustelleninformationen: betroffene Streckenabschnitte, Tempolimits, Sperrungstypen, Umleitungshinweise
- Mapping der extrahierten Daten auf das einheitliche `ConstructionZone`-Model

**Abgrenzung zu anderen Modulen:**

- `routing`: Berechnet die Straßenroute; `construction` arbeitet auf der fixierten Route, nicht auf OSM-Daten.
- `optimization`: Nutzt `ConstructionZone`-Informationen als Input für die Ladeplanung (z. B. reduzierte Geschwindigkeit = erhöhte Fahrzeit).
- `energy`: Baustellen-Tempolimits fließen in den Energieverbrauch ein; das Modul liefert die `ConstructionZone`-Liste, nicht die Berechnung.

**NICHT-Scope (spätere Ausbaustufen):**

- Echtzeit-Verkehrsdaten (außer Baustellen, die laut DATEX II enthalten sind)
- Prognose von Baustellen-Zeitplänen (nur aktuelle/gültige Baustellen)
- Integration nicht-europäischer Länder (kein DATEX II-Standard)
- Crawler zum Sammeln von Baustellendaten (nur Client für öffentliche Feeds)

---

## 2. Abhängigkeiten & Phasenzuordnung

**Phase:** Phase 1 (unabhängige Datenquellen-Module)

**Fremde Modelle (nur Lesen, exakte Namen aus dem Register):**

- `tripplanner.routing.models.Route`: Eingabe für die Abfrage entlang der Route
- `tripplanner.routing.models.RouteSegment`: Für Mapping von Segment-IDs zu Baustellen
- `tripplanner.elevation.models.ElevationPoint`: Optional für Geo-Check (Baustelle liegt im Radius eines Segments)

**Abhängigkeit von anderen Modulen:**

- Keine Laufzeit-Abhängigkeit zu anderen Modulen — das Modul ist eigenständig und kann isoliert getestet werden.
- Nur die Schnittstelle via `models.py` wird benötigt.

---

## 3. Datenmodelle

Die folgenden Pydantic-Modelle definieren die Datenstruktur des Moduls. Alle Modelle folgen der Google-Style Docstring-Konvention (siehe `docs/04-repo-tooling-setup.md`).

```python
# src/tripplanner/construction/models.py

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from pydantic import BaseModel, Field, model_validator
from typing_extensions import Self


class Sperrungstyp(StrEnum):
    """Sperrungstyp gemäß DATEX II RoadOrCarriagewayOrLaneManagementType."""

    FULLY_CLOSED = "fullyClosed"
    PARTIALLY_CLOSED = "partiallyClosed"
    LANE_CLOSED = "laneClosed"
    TEMPORARY_SPEED_LIMIT = "temporarySpeedLimit"
    REDUCED_LANES = "reducedLanes"
    DETOUR_REQUIRED = "detrourRequired"


class Land(StrEnum):
    """Ländercodes für Baustellen (DE=Deutschland, DK=Dänemark, SE=Schweden)."""

    DE = "DE"
    DK = "DK"
    SE = "SE"


class ConstructionZone(BaseModel):
    """
    Ein Baustellen-Abschnitt mit Tempolimit, Sperrungstyp und Umleitungshinweis.

    Args:
        betroffene_segmente: Liste von RouteSegment-IDs (0-basiert), die von der Baustelle betroffen sind.
        tempolimit_kmh: Reduziertes Tempolimit in km/h (None wenn keine Beschränkung).
        sperrungstyp: Art der Sperrung/Baustelle.
        umleitungshinweis: Freitext-Information zur Umleitung (optional).
        land: Land, in dem die Baustelle liegt.
        gueltig_von: Startzeitpunkt der Baustelle (ISO 8601).
        gueltig_bis: Endzeitpunkt der Baustelle (ISO 8601), None wenn unbestimmt.
    """

    betroffene_segmente: list[int] = Field(
        description="Liste von RouteSegment-IDs (0-basiert), die von der Baustelle betroffen sind."
    )
    tempolimit_kmh: Annotated[int | None, Field(ge=0, le=200, default=None)] = Field(
        description="Reduziertes Tempolimit in km/h (None wenn keine Beschränkung)."
    )
    sperrungstyp: Sperrungstyp = Field(description="Art der Sperrung/Baustelle.")
    umleitungshinweis: Annotated[str | None, Field(max_length=500, default=None)] = Field(
        description="Freitext-Information zur Umleitung (optional)."
    )
    land: Land = Field(description="Land, in dem die Baustelle liegt.")
    gueltig_von: datetime = Field(description="Startzeitpunkt der Baustelle (ISO 8601).")
    gueltig_bis: Annotated[datetime | None, Field(default=None)] = Field(
        description="Endzeitpunkt der Baustelle (ISO 8601), None wenn unbestimmt."
    )

    @model_validator(mode="after")
    def validate_tempolimit_for_sperrungstyp(self) -> Self:
        """Validiert, dass tempolimit_kmh bei certain Sperrungstypen gesetzt ist."""
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
                f"tempolimit_kmh muss gesetzt sein für Sperrungstyp {self.sperrungstyp}."
            )
        return self


class ConstructionProvider:
    """
    Protocol für Datenprovider von Baustelleninformationen.

    Alle implementierenden Provider müssen die Methode `fetch_construction_zones` implementieren,
    die eine Liste von ConstructionZone für eine gegebene Route zurückgibt.
    """

    async def fetch_construction_zones(
        self,
        route: "tripplanner.routing.models.Route",
        laender: list[Land],
    ) -> list[ConstructionZone]:
        """Abfrage von Baustellen entlang der Route für die angegebenen Länder."""
        raise NotImplementedError
```

**Zusätzliche interne Hilfstypen (nicht exportiert, nur zur Verarbeitung):**

- `DATEXIIConstructionZone`: Internes Pydantic-Modell zum Parsen von DATEX II XML (siehe Abschnitt 5)

---

## 4. Öffentliche Schnittstelle

Die öffentliche API des Moduls besteht aus einer Factory-Funktion zur Erzeugung des Providers und der Hauptfunktion `fetch_construction_zones`.

```python
# src/tripplanner/construction/__init__.py
"""Modul für Baustellen- und Sperrungsinformationen entlang der Route."""

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

# Konfiguration für externe Services (vom Aufrufer übergeben)
DATEXII_ENDPOINTS = {
    Land.DE: "https://www.mobilithek.info/datexii/rest/v2/situations",
    Land.DK: "https://businessservice.dataudveksler.app.vd.dk/api/DateX2",
    Land.SE: "https://api.trafikinfo.trafikverket.se/v1/trafficincidents",
}


class ConstructionProviderConfig(BaseModel):
    """Konfiguration für den ConstructionProvider."""

    mdm_username: str | None = None  # Für Deutschland (MDM)
    mdm_password: str | None = None  # Für Deutschland (MDM)
    dk_service_account: str | None = None  # Für Dänemark (Dataudveksleren)
    dk_api_key: str | None = None  # Optional, falls erforderlich
    tv_api_key: str  # Für Schweden (Trafikverket), benötigt
    timeout_seconds: float = 30.0  # HTTP-Timeout


class ConstructionProviderImpl(ConstructionProvider):
    """
    Implementierung des ConstructionProvider mit DATEX II Feeds für DE, DK, SE.

    Der Provider nutzt einen gemeinsamen XML-Parser (DATEX II Version 3.3) für alle Länder,
    da der Standard einheitlich ist.
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
        Abfrage von Baustellen entlang der Route für die angegebenen Länder.

        Args:
            route: Die zu prüfende Route (aus routing.models).
            laender: Liste der Länder, für die Baustellen abgefragt werden sollen.

        Returns:
            Liste von ConstructionZone-Objekten für die angegebenen Länder.

        Raises:
            RuntimeError: Wenn der HTTP-Client nicht initialisiert ist.
            TimeoutException: Wenn ein HTTP-Request timeoutt.
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
        """Abfrage und Parsing für ein Land."""
        endpoint = DATEXII_ENDPOINTS[land]

        # Query-Parameter für DATEX II Feeds anpassen
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
            # Timeout als leere Liste returnen (später retry in higher level)
            return []

        # XML parsing (lxml oder xmlschema, siehe Abschnitt 5)
        xml_content = response.text
        construction_zones = parse_datexii_xml(xml_content, land)

        # Mapping auf ConstructionZone
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
        """Parameter für MDM (Germany) DATEX II API."""
        # MDM nutzt REST API für Situationen, Filter nach Gültigkeit und Geokoordinaten
        # Koordinatenpolygon aus Route erzeugen (bounding box)
        coords = self._route_to_bounding_box(route)
        return {
            "query": "roadworks",
            "coords": coords,
            "validity": "active",
            "format": "xml",
        }

    def _build_dk_params(self, route: routing_models.Route) -> dict[str, str]:
        """Parameter für Dataudveksleren (Denmark) DATEX II API."""
        # Dänemark nutzt SOAP oder REST mit DATEX II XML als Payload
        coords = self._route_to_bounding_box(route)
        return {
            "coords": coords,
            "startDate": (datetime.utcnow().isoformat() + "Z"),
            "format": "datex2",
        }

    def _build_se_params(self, route: routing_models.Route) -> dict[str, str]:
        """Parameter für Trafikverket (Sweden) API."""
        # Trafikverket Open API nutzt JSON POST mit DATEX II ontology
        coords = self._route_to_bounding_box(route)
        return {
            "query": f"location geometry '{coords}' AND status 'active' AND type 'roadworks'",
            "key": self._config.tv_api_key,
        }

    def _route_to_bounding_box(self, route: routing_models.Route) -> str:
        """Konvertiert Route zuBounding Box für API-Abfrage."""
        # Einfache Implementierung: min/max Lat/Lon aus Geometrie
        coords = []
        for segment in route.segments:
            # segment.geometrie enthält Waypoints als List[Tuple[float, float]]
            for lat, lon in segment.geometrie:
                coords.append((lat, lon))

        if not coords:
            return ""

        lats, lons = zip(*coords)
        return f"{min(lats)},{min(lons)},{max(lats)},{max(lons)}"  # WKT-Style BBOX

    async def _map_to_segment_ids(
        self,
        zone: "DATEXIIConstructionZoneInternal",
        route: routing_models.Route,
    ) -> list[int]:
        """
        Mapped DATEX II Geometrie auf Route-Segment-IDs.

        Der Algorithmus prüft, ob die Baustellen-Geometrie mit den Segmenten überlappt.
        Da DATEX II Polygon- oder Linienreferenzen nutzt, wird ein Intersection-Check
        durchgeführt (rasterio oder shapely für Geometrie-Operationen).
        """
        from shapely.geometry import LineString, box

        # Baustellen-Geometrie in Shapely konvertieren
        zone_geom = self._zone_to_geometry(zone)

        betroffene_ids = []
        for idx, segment in enumerate(route.segments):
            # Segment-Geometrie als LineString
            seg_geom = LineString(segment.geometrie)

            # Intersection check
            if zone_geom.intersects(seg_geom):
                betroffene_ids.append(idx)

        return betroffene_ids

    def _zone_to_geometry(self, zone: "DATEXIIConstructionZoneInternal") -> LineString:
        """Konvertiert DATEX II Geometrie zu Shapely LineString."""
        # DATEX II nutzt gml:LineString oder gml:Curve
        # Für einfache Umsetzung: Koordinatenliste direct mapping
        coords = [(pt.lon, pt.lat) for pt in zone.koordinaten]
        return LineString(coords)
```

---

## 5. Externe Integration / Algorithmus-Details

### DATEX II XML-Parsing

**Bibliotheksauswahl:** `xmlschema` (empfohlen gegenüber `lxml`)

**Begründung:**

- `xmlschema` bietet vollständige XSD 1.0/1.1 Validierung, die für DATEX II-Struktur unerlässlich ist.
-DATEX II Schemas sind strikt definiert; `xmlschema` liefert Daten direkt als Python-Dicts/Objekte (`to_dict()`).
- `lxml` ist zwar schneller (~42x bei Validierung), aber `xmlschema` ist speicherfreundlicher bei großen XML-Dateien (`lazy=True` Modus).
- Keine externen C-Bibliotheken nötig (`xmlschema` ist pure Python), was die Installation vereinfacht.

**DATEX II Version:** 3.3 (latest stable; Germany MDM, Denmark Dataudveksleren, Sweden Trafikverket unterstützen alle DATEX II v3.x).

**Schema-Download:** <https://docs.datex2.eu/downloads/modelv33/> (DATEXII_3_Situation.xsd, DATEXII_3_Common.xsd, DATEXII_3_LocationReferencing.xsd)

**Mapping DATEX II → `ConstructionZone`:**

| DATEX II Element (Situation) | XML-Path | `ConstructionZone` Field |
| ------------------------------ | ---------- | -------------------------- |
| `situationRecord` (xsi:type) | `/situationRecord/@xsi:type` | `Sperrungstyp` (s.u.) |
| `creationTime` | `/situationRecord/situationRecordCreationTime` | `gueltig_von` (oder aktueller Zeitpunkt als Fallback) |
| `validity` -> `validityTimeSpec` | `/situationRecord/validity/validityTimeSpecification` | `gueltig_von`, `gueltig_bis` |
| `impact` -> `delays` | `/situationRecord/impact/delays/delayBand` | `tempolimit_kmh` (s.u.) |
| `groupOfLocations` -> `itinerary` | `/situationRecord/groupOfLocations/groupOfLocations` | `koordinaten` (für `_zone_to_geometry`) |
| `source` | `/situationRecord/source/sourceName/value` | `umleitungshinweis` (falls vorhanden) |

**Sperrungstyp-Mapping (nach DATEX II v3 Roadworks profile):**

| DATEX II `roadworksType` | XML-Value | `Sperrungstyp` |
| -------------------------- | ----------- | ---------------- |
| `fullyClosed` | `fullyClosed` | `FULLY_CLOSED` |
| `partiallyClosed` | `partiallyClosed` | `PARTIALLY_CLOSED` |
| `laneClosed` | `laneClosed` | `LANE_CLOSED` |
| `temporarySpeedLimit` | `temporarySpeedLimit` | `TEMPORARY_SPEED_LIMIT` |
| `reducedLanes` | `reducedLanes` | `REDUCED_LANES` |
| `detrourRequired` | `detrourRequired` | `DETROUR_REQUIRED` |

**Richtungsabhängiges Matching (Fahrtrichtung):** Eine Baustelle/ein Ereignis wird einem
Routen-Segment nur zugeordnet, wenn sie sowohl räumlich nah (innerhalb 500 m) ALS AUCH in
Fahrtrichtung der Route tatsächlich anwendbar ist — nicht nur auf derselben Straße. Für
DK/SE-Zonen mit LineString-Geometrie wird das eigene Bearing der Zone (Start→Ende ihrer
Koordinaten) gegen `RouteSegment.bearing_deg` des zugeordneten Segments verglichen; ein Match
wird ausgeschlossen, wenn die Winkeldifferenz (gefaltet auf `[0°, 180°]`) 100° überschreitet —
d. h. die Zone verläuft grob entgegengesetzt zur Route (Gegenfahrbahn auf einer geteilten
Straße). Für SE-Zonen wird ein explizites `AffectedDirectionValue` (sofern nicht "beide
Richtungen") gegenüber der Geometrie-Heuristik bevorzugt (Quelle vertrauenswürdiger als
Ableitung). **Bekannte Einschränkung:** DE-Baustellen (Autobahn GmbH API) liefern nur einen
einzelnen Punkt-Koordinatenwert — keine LineString-Geometrie und kein Richtungs-/
Fahrbahn-Feld — daher ist richtungsabhängiges Filtern für DE nicht möglich; das DE-Matching
bleibt rein distanzbasiert (dokumentiert in `_parse_autobahn_roadwork`).

**Längenableitung (`laenge_m`):** Für DK/SE-Zonen mit LineString-Geometrie wird die Länge
direkt als geodätische Länge dieser Geometrie berechnet (`tripplanner.geo.geodesic_length_m`).
Für DE-Zonen (nur Punkt-Koordinate) wird die Länge aus dem zugeordneten Routen-Segment
abgeleitet (`RouteSegment.laenge_m`), da die Quelle keine Längenangabe liefert. `None`, wenn
keines von beidem berechenbar ist.

**Falls DATEX II-Fields fehlen (robuster Default):**

- `gueltig_von`: Fallback auf `situationRecordCreationTime` (oder UTC now).
- `gueltig_bis`: Falls `overallEndTime` fehlt, auf `None` setzen (unbestimmt).
- `tempolimit_kmh`: Aus `delayBand` ableiten (z. B. `upToTenMinutes` → 100 km/h, `tenToTwentyMinutes` → 80 km/h, usw.) — Konkretisierung in Konfiguration.

**Beispiel-Parse-Funktion (intern):**

```python
# src/tripplanner/construction/parser.py

from datetime import datetime
from pathlib import Path
from typing import cast

import xmlschema

# DATEX II v3.3 Schema lokal laden (aus Bundle oder URL)
SCHEMA_PATH = Path(__file__).parent / "datexii_3.3" / "DATEXII_3_Situation.xsd"


class DATEXIIConstructionZoneInternal(BaseModel):
    """Internes Modell für DATEX II Parse-Ergebnis."""

    sperrungstyp: str  # DATEX II roadworksType
    gueltig_von: datetime
    gueltig_bis: datetime | None
    koordinaten: list[tuple[float, float]]  # Lat, Lon
    umleitungshinweis: str | None
    tempolimit_kmh: int | None  # abgeleitet aus delayBand


def parse_datexii_xml(xml_content: str, land: Land) -> list[DATEXIIConstructionZoneInternal]:
    """
    Parse DATEX II XML und extrahiert Baustelleninformationen.

    Args:
        xml_content: Raw XML string von DATEX II Feed.
        land: Land (für spezifische Mapping-Logik).

    Returns:
        Liste von DATEXIIConstructionZoneInternal (internal).
    """
    schema = xmlschema.XMLSchema(SCHEMA_PATH)

    # Validierung und Decoding
    data = schema.to_dict(xml_content, validate=True)

    # Extrahiere Situationen
    situations = data.get("situation", [])
    if not isinstance(situations, list):
        situations = [situations] if situations else []

    zones: list[DATEXIIConstructionZoneInternal] = []

    for sit in situations:
        sr = sit.get("situationRecord", {})
        if not sr:
            continue

        # Extract type (nur roadworks relevante Types)
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

        # Extract delay band (für tempolimit_kmh)
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
                        # Für deutsche Feeds: Lat, Lon; für schwedisch/dänisch: Lon, Lat
                        if land == Land.DE:
                            lat, lon = float(pt[1]), float(pt[0])
                        else:
                            lat, lon = float(pt[0]), float(pt[1])
                        koordinaten.append((lat, lon))

        # Extract umleitungshinweis
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
    # DATEX II nutzt UTC mit Z-Suffix
    if dt_str.endswith("Z"):
        dt_str = dt_str[:-1] + "+00:00"
    return datetime.fromisoformat(dt_str)


def _delay_band_to_speed(delay_band: str | None) -> int | None:
    """Mappe delayBand auf tempolimit_kmh (Konfiguration für feine Anpassung)."""
    if not delay_band:
        return None

    # Beispiel-Map (anpassbar über Konfiguration)
    band_map = {
        "upToTenMinutes": 100,
        "tenToTwentyMinutes": 80,
        "twentyToFortyMinutes": 60,
        "overFortyMinutes": 40,
    }
    return band_map.get(delay_band, 60)
```

### Konkrete API-Zugangspunkte & Authentifizierung

**Deutschland (MDM – Mobilitäts Daten Marktplatz):**

- **Endpoint:** `https://www.mobilithek.info/datexii/rest/v2/situations` (REST API)
- **Authentifizierung:** Registrierung als User erforderlich (Kontakt über <https://service.mdm-portal.de/mdm-portal-application/_accountRegister.do>)
- **Datenformat:** XML (DATEX II v3.3)
- **Hinweis:** HTTPS allein ist ausreichend; DATEX II Auth-Optionen (C.13, C.14, C.17) nicht nötig.
- **Quelle:** Technische Schnittstellenbeschreibung Version 1.2.2 (2024-04-04), Abschnitt "Authentication".

**Dänemark (Vejdirektoratet – Dataudveksleren):**

- **Endpoint:** `https://businessservice.dataudveksler.app.vd.dk/api/DateX2` (SOAP oder REST)
- **Authentifizierung:** Service-Account erforderlich (Dokumentation auf <https://vejdirektoratet.atlassian.net/wiki/spaces/TRC/pages>)
- **Datenformat:** XML (DATEX II v3.2)
- **Dokumentation:** TRACÉ Protokollbeschreibung Datex II 3.2 (PDF auf vejdirektoratet.atlassian.net)
- **Portal:** <https://du-portal-ui.dataudveksler.app.vd.dk/data> (UI zur Konfiguration)

**Schweden (Trafikverket – NVDB):**

- **Endpoint:** `https://api.trafikinfo.trafikverket.se/v1/trafficincidents` (Open API)
- **Authentifizierung:** API-Key erforderlich (Registrierung unter <https://api.trafikinfo.trafikverket.se/>)
- **Datenformat:** JSON (DATEX II ontology als underlying model)
- **Hinweis:** Alle Öffentlichen Daten sind ohne Login lesbar, aber data retrieval erfordert Account.
- **Preis:** Kostenlos, aber Lizenzvereinbarung nötig.

---

## 6. Test-Strategie

### Fixtures

**Test-XML-Dateien (im Repo als Fixtures):**

- `tests/fixtures/construction/datexii_germany_roadworks_example.xml`: Auszug aus MDM (Deutschland)
- `tests/fixtures/construction/datexii_denmark_lane_closure.xml`: Beispiel Dänemark
- `tests/fixtures/construction/datexii_sweden_temp_limit.xml`: Beispiel Schweden

**Fixture-Inhalte (Beispiel für Deutschland):**

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

### Testfälle

**Testfall 1: Parsing einer deutschen DATEX II Nachricht**

- **Given:** XML-Datei aus `datexii_germany_roadworks_example.xml`.
- **When:** `parse_datexii_xml(xml_content, Land.DE)` wird aufgerufen.
- **Then:** Ergebnis enthält mindestens ein `DATEXIIConstructionZoneInternal` mit:
  - `sperrungstyp` = `MaintenanceWorks`
  - `gueltig_von` = `2024-03-20T21:01:00+00:00`
  - `gueltig_bis` = `2024-03-21T03:00:00+00:00`
  - `koordinaten` enthält mindestens 2 Punkte
  - `tempolimit_kmh` = `80` (from `delayBand` = `tenToTwentyMinutes`)

**Testfall 2: Mapping auf `ConstructionZone` mit Route-Intersection**

- **Given:** Fake `ConstructionProviderImpl` mit Test-Route (Segment 0: Koordinaten (52.5200,13.4050) → (52.5210,13.4060)), XML-Fixture.
- **When:** `fetch_construction_zones(route, [Land.DE])` wird ausgeführt.
- **Then:** Ergebnis enthält ein `ConstructionZone` mit `betroffene_segmente = [0]`, `tempolimit_kmh = 80`, `sperrungstyp = Sperrungstyp.TEMPORARY_SPEED_LIMIT`.

**Testfall 3: Grenzfall — keine Baustellen in Route**

- **Given:** Fake `ConstructionProviderImpl` mit Route, die keine Baustellen-Geometrien schneidet.
- **When:** `fetch_construction_zones(route, [Land.DE])`.
- **Then:** Leere Liste `[]` zurückgegeben.

**Testfall 4: Validierung `ConstructionZone.tempolimit_kmh` required**

- **Given:** `ConstructionZone` mit `sperrungstyp = Sperrungstyp.PARTIALLY_CLOSED` und `tempolimit_kmh = None`.
- **When:** Instanziierung.
- **Then:** Pydantic `ValidationError` wird ausgelöst (Modell-Validierung).

### Unit- vs. Integrationstest

| Test | Datei | Decorator |
| ------ | ------- | ----------- |
| `test_parse_datexii_germany()` | `tests/construction/test_parser.py` | — |
| `test_parse_datexii_denmark()` | `tests/construction/test_parser.py` | — |
| `test_parse_datexii_sweden()` | `tests/construction/test_parser.py` | — |
| `test_delay_band_to_speed()` | `tests/construction/test_parser.py` | — |
| `test_fetch_construction_zones_no_overlap()` | `tests/construction/test_providers.py` | `@pytest.mark.integration` |
| `test_fetch_construction_zones_with_overlap()` | `tests/construction/test_providers.py` | `@pytest.mark.integration` |

---

## 7. Aufgaben-Checkliste

- [ ] **Task 1:** Fixture-XML-Dateien erstellen
  - **Dateien:** `tests/fixtures/construction/datexii_germany_roadworks_example.xml`, `datexii_denmark_lane_closure.xml`, `datexii_sweden_temp_limit.xml`
  - **Beschreibung:** Drei konkrete DATEX II XML-Beispieldateien mit realistischen Werten für Baustellen.
  - **Akzeptanz:** Dateien validieren mit `xmlschema` gegen DATEXII_3_Situation.xsd (v3.3).

- [ ] **Task 2:** `models.py` implementieren
  - **Dateien:** `src/tripplanner/construction/models.py` (create)
  - **Beschreibung:** Pydantic-Modelle `ConstructionZone`, `Sperrungstyp` (Enum), `Land` (Enum), `ConstructionProvider` (Protocol).
  - **Akzeptanz:** Modelle importierbar, Validierung durch pytest (`test_validierung_construction_zone()`).

- [ ] **Task 3:** Parser `parser.py` implementieren
  - **Dateien:** `src/tripplanner/construction/parser.py` (create), `tests/construction/test_parser.py` (create)
  - **Beschreibung:** `parse_datexii_xml()`, `_parse_datetime()`, `_delay_band_to_speed()`, interne Modelle.
  - **Akzeptanz:** 100% coverage für parser functions (pytest-cov).

- [ ] **Task 4:** `providers.py` implementieren (Kern-Logik)
  - **Dateien:** `src/tripplanner/construction/providers.py` (create)
  - **Beschreibung:** `ConstructionProviderConfig`, `ConstructionProviderImpl`, `fetch_construction_zones()`, `parse_datexii_xml()` Aufruf.
  - **Akzeptanz:** `test_fetch_construction_zones()` in `test_providers.py` läuft ohne HTTP (Mock).

- [ ] **Task 5:** `__init__.py` exports implementieren
  - **Dateien:** `src/tripplanner/construction/__init__.py` (create)
  - **Beschreibung:** Re-export aller öffentlichen Modelle und Protokolle.
  - **Akzeptanz:** `from tripplanner.construction import ConstructionZone` funktioniert.

- [ ] **Task 6:** `rasterio`/`shapely`-Abhängigkeit prüfen und ggf. hinzufügen
  - **Dateien:** `pyproject.toml` (modify)
  - **Beschreibung:** `shapely` für Geometrie-Intersection Check (wird für `_map_to_segment_ids` benötigt).
  - **Akzeptanz:** `uv add shapely` läuft durch, `import shapely` im Python-Interpreter funktioniert.

- [ ] **Task 7:** Dokumentation der DATEX II Feeds
  - **Dateien:** `docs/plans/04-construction.md` (diese Datei, modify)
  - **Beschreibung:** Konkrete Endpunkte, Auth-Methoden, API-Keys für DE/DK/SE dokumentieren.
  - **Akzeptanz:** Jeder Entwickler kann mit den Dokumentations-Links die Feeds testen.

- [ ] **Task 8:** Integration in `optimization`-Modul vorbereiten
  - **Dateien:** `src/tripplanner/optimization/models.py` (modify), `docs/03-modulspezifikationen.md` (modify)
  - **Beschreibung:** `optimization.models.OptimizationConstraints` erhält `construction_zones: list[ConstructionZone]` als optionaler Input.
  - **Akzeptanz:** `optimization`-Modul importiert `ConstructionZone` aus `tripplanner.construction.models`.

- [ ] **Task 9:** ruff + mypy Konfiguration prüfen
  - **Dateien:** `.ruff.toml`, `pyproject.toml` (modify)
  - **Beschreibung:** `ruff check src/tripplanner/construction/` und `mypy src/tripplanner/construction/` laufen ohne Fehler.
  - **Akzeptanz:** 0 ruff errors, 0 mypy errors.

- [ ] **Task 10:** pytest-cov Konfiguration anpassen
  - **Dateien:** `pyproject.toml` (modify)
  - **Beschreibung:** Coverage-Gate 85% für `construction`-Modul sichern.
  - **Akzeptanz:** `pytest --cov=tripplanner.construction tests/construction/` reportet ≥85%.

- [ ] **Task 11:** Build/Deployment-Test
  - **Dateien:** —
  - **Beschreibung:** `uv build` und `pip install .` im venv成功.
  - **Akzeptanz:** `from tripplanner.construction import ConstructionZone` funktioniert im neuen venv.

- [ ] **Task 12:** Linting / Formatierung durchführen
  - **Dateien:** Alle Python-Dateien im `construction`-Modul
  - **Beschreibung:** `ruff format src/tripplanner/construction/` und `ruff check --fix src/tripplanner/construction/`.
  - **Akzeptanz:** Keine Formatierungsfehler (ruff clean).

- [ ] **Task 13:** Code-Review-Checkliste abarbeiten
  - **Dateien:** —
  - **Beschreibung:** API-Konventionen (nur models.py-Importe), Docstrings (Google-Style), Typannotationen.
  - **Akzeptanz:** Review durch Team-Mitglied bestätigt.

- [ ] **Task 14:** Dokumentation für Entwickler (optional, aber empfohlen)
  - **Dateien:** `docs/07-construction-doku.md` (create)
  - **Beschreibung:** Wie man die Feeds lokal testet (MDM Registrierung, API-Key von Trafikverket).
  - **Akzeptanz:** Neue Entwickler können die Feeds ohne Support einspielen.

---

## 8. Risiken & offene technische Fragen

**1. DATEX II API-Keys/Registrierung (High Risk, aber lösbar):**

- **Problem:** Deutschland (MDM) und Dänemark (Dataudveksleren) erfordern Registrierung/Service-Account; Schweden (Trafikverket) benötigt API-Key.
- **Lösung:** Konfiguration über Umgebungsvariablen (`MDM_USERNAME`, `MDM_PASSWORD`, `DK_SERVICE_ACCOUNT`, `TV_API_KEY`), Default-Values auf Dummy-String setzen (Test-Fallback). In Dokumentation klare Anleitung zur Registrierung.
- **Status:** Dokumentiert in Task 7.

**2. Geometrie-Mapping ungenau (Medium Risk):**

- **Problem:** DATEX II nutzt komplexe GML-Geometrien (LineString, Curve, Polygon); Route-Segmente sind vereinfacht; Intersection-Check kann fehlschlagen.
- **Lösung:** Erstes Release mit einfacher Bounding-Box-Check (`shapely.box` über alle Koordinaten). Spätere Verbesserung (Distanz-Toleranz, Segment-Polygon-Aufteilung).
- **Status:** Task 4 implementiert `LineString`-Intersection; Task 2 erlaubt Erweiterung.

**3. Tempolimit-Ableitung aus `delayBand` (Low Risk):**

- **Problem:** DATEX II `delayBand` ist qualitativ (z. B. `upToTenMinutes`), kein exakter Geschwindigkeitswert.
- **Lösung:** Konfigurierbare Map `_delay_band_to_speed()` in `parser.py`, Standardwerte basierend auf deutschen Autobahn-Regeln (100 km/h für kurze Staus, 40 km/h für lange). Spätere Kalibrierung mit realen Fahrdaten.
- **Status:** Implementiert als Konfigurationspunkt (kein Hardcode), Task 2-3.

**4. DATEX II Version 3.3 vs. 2.3 (Medium Risk):**

- **Problem:** Dänemark nutzt DATEX II v3.2; Schweden und Deutschland unterstützen v3.3, aber auch v2.3. Inkompatibilitäten möglich.
- **Lösung:** Parse-Logik robust halten — nur Gemeinsamkeiten nutzen (`SituationRecord`, `validity`, `impact`, `groupOfLocations`). Fallbacks bei fehlenden Fields (Task 3).
- **Status:** In Task 3 dokumentiert;Schema-Download auf v3.3.

**5. Rate Limits durch externe APIs (Medium Risk):**

- **Problem:** MDM, Dataudveksleren, Trafikverket können Rate Limits erzwingen.
- **Lösung:** `httpx.AsyncClient` mit `Retry` Policy (Task 4: `async_retry` wrapper). Integrationstest mit Mock (Task 5).
- **Status:** In Task 4 implementiert, Task 6 als Mock-Test vorsehen.

**6. Kein Echtzeit-Update-Mechanismus (Low Risk, nicht im Scope):**

- **Problem:** Die Feeds werden nur bei `fetch_construction_zones()` aktualisiert (kein WebSocket/AMQP).
- **Lösung:** Akzeptiert; das Modul ist stateless. Bei späterem Bedarf kann `ConstructionProviderImpl` um `subscribe()` erweitert werden.
- **Status:** Explizit als Nicht-Scope in Task 7 beschrieben.

**7. Keine Fahrtrichtungsfilterung für DE-Baustellen (Medium Risk, akzeptiert):**

- **Problem:** Die Autobahn GmbH API liefert für DE-Baustellen nur einen einzelnen
  Punkt-Koordinatenwert (kein LineString, kein Richtungs-/Fahrbahn-Feld). Eine
  Baustelle auf der Gegenfahrbahn kann daher fälschlich der Route zugeordnet werden,
  wenn sie innerhalb des 500-m-Distanzschwellwerts liegt.
- **Lösung:** Für DK/SE (mit LineString-Geometrie bzw. `AffectedDirectionValue`) ist
  richtungsabhängiges Matching implementiert (siehe Abschnitt 5). Für DE bleibt es bei
  reiner Distanz-Matching; dies ist eine dokumentierte Einschränkung der Datenquelle,
  keine Lücke in der Implementierung. Sollte Autobahn GmbH künftig Richtungsdaten
  liefern, kann dieselbe Bearing-Vergleichslogik übernommen werden.
- **Status:** Akzeptiert und dokumentiert (`_parse_autobahn_roadwork`), kein offener Task.

---

**Ende des Plans.**
