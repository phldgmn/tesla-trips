# Plan: Tesla Supercharger Provider (SQLite-basiert)

---

## 1. Zweck & Scope

Erweiterung des Moduls `charging_infrastructure` um einen **dauerhaften, live-aktualisierbaren Tesla-Supercharger-Provider**, der eine lokale SQLite-Datenbank als persistenten Speicher nutzt — Ablösung des reinen JSON-Snapshot-Ansatzes durch eine strukturierte, indizierte Datenbank mit zwei Tabellen: `charging_stations` (Standortdaten) und `charging_pricing` (zeitbasierte Preise).

**Kernentscheidung:** Der Provider arbeitet standardmäßig von der lokalen SQLite-Datenbank (kein Live-Request bei Normalbetrieb). Ein expliziter `refresh()`-Aufruf holt frische Daten von der öffentlichen supercharge.info-API und schreibt sie persistent in die SQLite-Datenbank. Dies folgt dem in AGENTS.md geforderten Prinzip: "lokal, persistent, nur bei Bedarf aktualisiert".

**Abgrenzung:**

- Dieses Dokument ersetzt *nicht* den bestehenden `LocalFileChargingStationProvider` — dieser bleibt als einfacher JSON-basierter Provider bestehen (für Tests ohne DB, minimales Setup).
- Der neue `TeslaChargingStationProvider` ist die Empfehlung für Produktion / Integrationstests.
- `FakeChargingStationProvider` bleibt unverändert für Unit-Tests.
- Pricing-Daten (Tesla Guest GraphQL) sind als zweite Phase vorgesehen, da die Tesla-API hinter Akamai WAF liegt und ein Browser-Fallback (Safari/Playwright) erforderlich macht.

---

## 2. Architektur

```mermaid
graph TD
    subgraph "Externe APIs"
        SCI[supercharge.info REST API]
        TGQ[Tesla Guest GraphQL API<br/>Phase 2]
    end

    subgraph "Neue Komponenten"
        CLIENT[SuperchargeInfoClient<br/>client.py]
        DB[SQLiteDatabase<br/>database.py]
        PROVIDER[TeslaChargingStationProvider<br/>providers.py]
        PRICING_MODEL[ChargingPricingTier<br/>models.py]
    end

    subgraph "Bestehend"
        PROTOCOL[ChargingStationProvider Protocol]
        LOCALFILE[LocalFileChargingStationProvider]
        FAKE[FakeChargingStationProvider]
        CHARGING_FN[charging_infrastructure.py]
    end

    SCI --> CLIENT
    CLIENT --> PROVIDER
    PROVIDER --> DB
    PROVIDENT -.->|Phase 2| TGQ
    PROVIDER -->|implementiert| PROTOCOL
    PROVIDER --> PRICING_MODEL
    CHARGING_FN --> PROVIDER
```

---

## 3. SQLite-Datenbank-Schema

Die Datenbank liegt unter `data/tesla_superchargers.db` (konfigurierbar via `data_path`).

### Tabelle: `charging_stations`

```sql
CREATE TABLE IF NOT EXISTS charging_stations (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    supercharge_info_id    INTEGER UNIQUE NOT NULL,
    tesla_location_id      TEXT,
    site_name              TEXT NOT NULL,
    latitude               REAL NOT NULL,
    longitude              REAL NOT NULL,
    country_code           TEXT NOT NULL,
    stalls_v2              INTEGER DEFAULT 0,
    stalls_v3              INTEGER DEFAULT 0,
    stalls_v3_ultra        INTEGER DEFAULT 0,
    stalls_v4              INTEGER DEFAULT 0,
    total_stalls           INTEGER DEFAULT 0,
    power_kilowatt         INTEGER DEFAULT 0,
    status                 TEXT NOT NULL DEFAULT 'OPEN',
    connector_types        TEXT NOT NULL DEFAULT '[]',
    ist_24_7               INTEGER DEFAULT 1,
    date_opened            TEXT,
    last_updated_utc       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stations_coord
    ON charging_stations(latitude, longitude);

CREATE INDEX IF NOT EXISTS idx_stations_country
    ON charging_stations(country_code);

CREATE INDEX IF NOT EXISTS idx_stations_status
    ON charging_stations(status);
```

**Mapping supercharge.info → SQLite:**

| supercharge.info-Feld | SQLite-Spalte | Transformation |
|---|---|---|
| `id` | `supercharge_info_id` | Direkt, als INTEGER |
| `locationId` | `tesla_location_id` | String, kann NULL sein |
| `name` | `site_name` | Direkt |
| `gps.latitude` | `latitude` | Direkt |
| `gps.longitude` | `longitude` | Direkt |
| `address.country` → ISO-2 | `country_code` | Aus `country` (USA→US, Germany→DE via Mapping) |
| `stalls.v2` | `stalls_v2` | 0 if missing |
| `stalls.v3` | `stalls_v3` | 0 if missing |
| `stalls.v3` (bei >250kW) | `stalls_v3_ultra` | Siehe Stall-Typ-Erkennung |
| `stalls.v4` | `stalls_v4` | 0 if missing |
| `stallCount` | `total_stalls` | Direkt |
| `powerKilowatt` | `power_kilowatt` | Per-Stall-Spitzenleistung |
| `status` | `status` | OPEN→OPEN, CONSTRUCTION→CONSTRUCTION, etc. |
| `plugs.*` | `connector_types` | JSON-Array der Plug-Typen mit count>0 |
| `dateOpened` | `date_opened` | ISO-Datum oder NULL |

**Stall-Typ-Erkennung aus `powerKilowatt`:**

Da supercharge.info in `stalls.v3` alle V3-Stalls zusammenfasst (auch welche, die wir als `V3_ULTRA` bezeichnen), klassifizieren wir basierend auf der Nennleistung:

| `powerKilowatt` | Stall-Typ |
|---|---|
| ≤150 kW | `V2` |
| 151-250 kW | `V3` |
| 251-350 kW | `V3_ULTRA` |
| ≥351 kW | `V4` |

Dies ist eine Näherung — exakte Typ-Klassifizierung erfordert Tesla-eigene Daten. Die Werte sind aber ausreichend, da `powerKilowatt` die per-Stall-Nennleistung ist.

**Ländermapping:**

supercharge.info verwendet ausgeschriebene Ländernamen (`"Germany"`, `"Denmark"`, `"Sweden"`). Ein festes Mapping in `database.py` wandelt in ISO-2-Codes um. Unbekannte Länder → `"XX"`.

```python
_COUNTRY_MAP: dict[str, str] = {
    "Germany": "DE", "Denmark": "DK", "Sweden": "SE",
    "Austria": "AT", "Switzerland": "CH", "Netherlands": "NL",
    "France": "FR", "Italy": "IT", "Spain": "ES",
    "Poland": "PL", "Czech Republic": "CZ", "Slovakia": "SK",
    "Hungary": "HU", "Slovenia": "SI", "Croatia": "HR",
    "Belgium": "BE", "Luxembourg": "LU", "United Kingdom": "GB",
    "Norway": "NO", "Finland": "FI", "Iceland": "IS",
    "Lithuania": "LT", "Latvia": "LV", "Estonia": "EE",
    "Romania": "RO", "Bulgaria": "BG", "Greece": "GR",
    "Portugal": "PT", "Ireland": "IE", "Andorra": "AD",
}
```

### Tabelle: `charging_pricing`

```sql
CREATE TABLE IF NOT EXISTS charging_pricing (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    supercharge_info_id  INTEGER NOT NULL,
    tier_label           TEXT NOT NULL,
    time_label           TEXT,
    currency             TEXT NOT NULL,
    amount               REAL NOT NULL,
    unit                 TEXT NOT NULL CHECK (unit IN ('kWh', 'min')),
    idle_fee_text        TEXT,
    last_updated_utc     TEXT NOT NULL,
    FOREIGN KEY (supercharge_info_id)
        REFERENCES charging_stations(supercharge_info_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pricing_station
    ON charging_pricing(supercharge_info_id);
```

**Datenherkunft (Phase 2):**
- Tesla Guest GraphQL API (`getGuestChargingSiteDetails`)
- Liegt hinter Akamai WAF — erfordert Browser-Fallback (Safari via AppleScript oder Playwright)
- Preis-Tiers: `"Charging Fees for Tesla Owner"`, `"Charging Fees for Other EV"`
- Rate Windows: zeitbasierte Preise (z. B. `"4:00 PM - 8:00 PM"`)

Ein Pricing-Eintrag repräsentiert genau einen Rate-Window eines Tiers:

| Feld | Beispiel |
|---|---|
| `tier_label` | `"Charging Fees for Tesla Owner"` |
| `time_label` | `"4:00 PM - 8:00 PM"` (oder NULL für Flatrate) |
| `currency` | `"EUR"`, `"SEK"`, `"DKK"` |
| `amount` | `0.39` (€/kWh) |
| `unit` | `"kWh"` oder `"min"` |
| `idle_fee_text` | `"0.50 €/min idle"` (oder NULL) |

### Metadaten-Tabelle (optional aber empfohlen)

```sql
CREATE TABLE IF NOT EXISTS db_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

Speichert:
- `schema_version` — für Migrationen
- `last_full_refresh_utc` — Zeitstempel des letzten vollständigen API-Abrufs
- `station_count` — Anzahl Stationen (Redundanz für schnelle Prüfung)
- `data_source` — `"supercharge.info"`

---

## 4. Neue Datenmodelle (Pydantic, in `models.py`)

Erweiterung der bestehenden `models.py` um Pricing-Modelle:

```python
class ChargingPricingTier(BaseModel):
    """Ein Preistier mit optionalen zeitbasierten Raten.

    Kann eine Flatrate (time_label=None) oder zeitabhängige Raten abbilden.
    """
    tier_label: str = Field(
        ...,
        description='Name des Preistiers, z.B. "Charging Fees for Tesla Owner"',
    )
    time_label: str | None = Field(
        default=None,
        description=(
            'Zeitfenster als Text, z.B. "4:00 PM - 8:00 PM". '
            "None = Flatrate (immer gültig)"
        ),
    )
    currency: str = Field(
        ...,
        min_length=3,
        max_length=3,
        description="Währung als ISO-4217-Code (EUR, SEK, DKK, ...)",
    )
    amount: float = Field(
        ...,
        gt=0,
        description="Preis pro Einheit (z.B. 0.39 EUR/kWh)",
    )
    unit: Literal["kWh", "min"] = Field(
        ...,
        description='Abrechnungseinheit: "kWh" (Energie) oder "min" (Zeit)',
    )
    idle_fee_text: str | None = Field(
        default=None,
        description="Idle-Fee als Rohtext, z.B. '0.50 EUR/min idle'",
    )


class ChargingStationWithPricing(BaseModel):
    """ChargingStation mit zugehörigen Preisdaten."""
    station: ChargingStation
    pricing: list[ChargingPricingTier] = Field(
        default_factory=list,
        description="Preisinformationen für diese Station",
    )
```

---

## 5. Komponenten-Details

### 5.1 `client.py` — SuperchargeInfoClient

```python
class SuperchargeInfoClient:
    """HTTP-Client für die supercharge.info REST-API.

    Die API ist öffentlich, benötigt keinen API-Key und blockiert keine
    einfachen HTTP-Clients (kein WAF). Dokumentierte Endpunkte:
    - /service/supercharge/allSites   → vollständiger Datensatz
    - /service/supercharge/databaseInfo → Änderungs-Timestamp
    - /service/supercharge/allChanges  → Delta-Änderungen
    """

    BASE_URL = "https://supercharge.info/service/supercharge"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        ...

    async def fetch_all_sites(self) -> list[dict]:
        """Ruft den vollständigen Datensatz aller Supercharger-Standorte ab.

        Returns: Roh-JSON-Liste (jedes Element ein Site-Dict)
        """

    async def fetch_database_info(self) -> dict:
        """Ruft den letzten Änderungszeitstempel der Datenbank ab.

        Returns: {"lastModified": timestamp_ms, "lastModifiedString": str}
        """

    async def fetch_all_changes(self) -> list[dict]:
        """Ruft die Liste aller Änderungen/Transitions ab.

        Returns: Liste von Änderungseinträgen
        """

    async def close(self) -> None:
        """Schließt die HTTP-Client-Session."""
```

**Antwortstruktur `allSites` (Auszug):**

```json
{
  "id": 2204,
  "locationId": "ashburnsupercharger",
  "name": "Ashburn, VA",
  "status": "OPEN",
  "address": {
    "country": "USA",
    "region": "North America"
  },
  "gps": { "latitude": 39.008, "longitude": -77.502 },
  "stallCount": 8,
  "powerKilowatt": 250,
  "stalls": { "v3": 8 },
  "plugs": { "nacs": 8, "ccs1": 8, "multi": 8 },
  "dateOpened": "2020-02-20",
  "elevationMeters": 99,
  "battery": false,
  "solarCanopy": false,
  "otherEVs": true
}
```

### 5.2 `database.py` — SQLiteDatabase

```python
class SQLiteDatabase:
    """Verwaltet die lokale SQLite-Datenbank für Supercharger-Daten.

    Erzeugt Tabellen bei erstmaliger Initialisierung, bietet CRUD-Zugriff
    für Stationen und Preise. Transaktionsbasiert — refresh() läuft in
    einer einzelnen Transaktion.
    """

    def __init__(self, db_path: Path) -> None:
        """Öffnet/erzeugt die SQLite-Datenbank.

        Args:
            db_path: Pfad zur .db-Datei (Default: data/tesla_superchargers.db)
        """

    def initialize(self) -> None:
        """Erzeugt Tabellen und Indizes, falls nicht vorhanden.
        Darf mehrfach aufgerufen werden (IF NOT EXISTS).
        """

    def load_stations(
        self,
        country_filter: set[str] | None = None,
    ) -> list[dict]:
        """Lädt alle Stationen aus der DB (als Roh-Dicts für ChargingStation-Mapping).
        Wird vom Provider gecached in ChargingStation-Objekte umgewandelt.
        """

    def load_pricing(
        self,
        supercharge_info_ids: set[int] | None = None,
    ) -> dict[int, list[ChargingPricingTier]]:
        """Lädt Pricing- Daten für eine oder alle Stationen.
        Returns: dict mapping supercharge_info_id → list[ChargingPricingTier]
        """

    def replace_all_stations(self, stations: list[dict]) -> None:
        """Ersetzt den gesamten Stationsbestand in einer Transaktion.
        - Löscht alle existierenden Stationen (CASCADE löscht auch Pricing)
        - Fügt die übergebenen Stationen ein
        - Aktualisiert db_meta
        """

    def upsert_pricing(
        self,
        supercharge_info_id: int,
        tiers: list[ChargingPricingTier],
    ) -> None:
        """Ersetzt Pricing-Daten für eine Station.
        (Phase 2: separater Pricing-Refresh ohne Stations-Refresh)
        """

    def get_meta(self, key: str) -> str | None:
        """Liest einen Metadaten-Wert."""

    def set_meta(self, key: str, value: str) -> None:
        """Schreibt einen Metadaten-Wert."""

    def close(self) -> None:
        """Schließt die DB-Verbindung."""

    @property
    def station_count(self) -> int:
        """Gibt die Anzahl der gespeicherten Stationen zurück."""

    @property
    def last_refresh_utc(self) -> datetime | None:
        """Gibt den Zeitstempel des letzten Refresh zurück."""
```

**Transaktionslogik `replace_all_stations`:**

```python
def replace_all_stations(self, stations: list[dict]) -> None:
    cursor = self._conn.cursor()
    cursor.execute("BEGIN")
    try:
        cursor.execute("DELETE FROM charging_pricing")
        cursor.execute("DELETE FROM charging_stations")
        for s in stations:
            cursor.execute("""
                INSERT INTO charging_stations (
                    supercharge_info_id, tesla_location_id, site_name,
                    latitude, longitude, country_code,
                    stalls_v2, stalls_v3, stalls_v3_ultra, stalls_v4,
                    total_stalls, power_kilowatt, status,
                    connector_types, ist_24_7, date_opened, last_updated_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (...))
        cursor.execute("""
            INSERT OR REPLACE INTO db_meta (key, value)
            VALUES ('last_full_refresh_utc', ?)
        """, (datetime.now(UTC).isoformat(),))
        self._conn.commit()
    except Exception:
        self._conn.rollback()
        raise
```

### 5.3 `providers.py` — TeslaChargingStationProvider

```python
class TeslaChargingStationProvider(ChargingStationProvider):
    """Implementierung, die Supercharger-Daten aus einer lokalen SQLite-DB liest
    und bei Bedarf via supercharge.info-API aktualisiert.

    Default: liest aus data/tesla_superchargers.db (erzeugt Datenbank bei
    erstmaligem Zugriff automatisch und lädt initiale Daten).

    Usage:
        provider = TeslaChargingStationProvider()
        # Automatischer Init: prüft DB → wenn leer, fetch von API
        stations = await provider.get_stations_in_radius((52.5, 13.4), 10)

        # Manuelles Refresh:
        await provider.refresh()
    """

    def __init__(
        self,
        db_path: Path | None = None,
        client: SuperchargeInfoClient | None = None,
    ) -> None:
        """Initialisiert den Provider.

        Args:
            db_path: Pfad zur SQLite-DB. Default: data/tesla_superchargers.db
            client: Optionaler HTTP-Client (für Tests mit Mock). Sonst auto.
        """

    async def refresh(self) -> int:
        """Holt aktuelle Daten von supercharge.info und schreibt sie in die DB.

        Flow:
        1. Fetch all sites via SuperchargeInfoClient
        2. Filtere auf Europe (address.region == "Europe")
        3. Mappe jedes Site auf unser internes Dict-Format
        4. Rufe SQLiteDatabase.replace_all_stations() auf
        5. Aktualisiere db_meta

        Returns:
            Anzahl der gespeicherten Stationen
        """

    async def get_stations_in_radius(
        self,
        coordinate: Coordinate,
        radius_km: float,
        country_filter: Literal["DE", "DK", "SE"] | None = None,
    ) -> list[ChargingStation]:
        """Liest aus SQLite, filtert nach Radius + Land, sortiert nach Distanz.
        Wie LocalFileChargingStationProvider, aber aus DB statt JSON.
        """

    async def get_stations_along_route(
        self,
        route: Any,
        search_radius_km: float = 2.0,
    ) -> dict[int, list[ChargingStation]]:
        """Wie LocalFileChargingStationProvider, Segment-Mittelpunkte + Radius."""
```

**Mapping supercharge.info → ChargingStation:**

```python
@staticmethod
def _site_to_charging_station(site: dict) -> ChargingStation:
    """Wandelt ein supercharge.info-Site-Dict in ein ChargingStation-Modell um."""

    # Stall-Typ-Verteilung aus powerKilowatt ableiten
    power = site.get("powerKilowatt", 250)
    if power <= 150:
        stall_type = StallType.V2
    elif power <= 250:
        stall_type = StallType.V3
    elif power <= 350:
        stall_type = StallType.V3_ULTRA
    else:
        stall_type = StallType.V4

    # Gesamt-Stalls auf den erkannten Typ mappen
    total = site.get("stallCount", 0)
    stalls = {
        StallType.V2: site.get("stalls", {}).get("v2", 0),
        StallType.V3: site.get("stalls", {}).get("v3", 0) if power <= 250 else 0,
        StallType.V3_ULTRA: site.get("stalls", {}).get("v3", 0) if 250 < power <= 350 else 0,
        StallType.V4: site.get("stalls", {}).get("v4", 0),
    }

    # Connector-Typen aus plugs extrahieren
    plugs = site.get("plugs", {})
    connector_map = {
        "nacs": ConnectorType.NACS,
        "ccs1": ConnectorType.CCS1,
        "ccs2": ConnectorType.CCS2,
        "type2": ConnectorType.TYPE2,
        "gbt": ConnectorType.GB_T,
        "chademo": ConnectorType.CHADEMO,
        "tpc": ConnectorType.TESLA,
    }
    connector_types = [
        connector_map[k] for k, v in plugs.items()
        if v and v > 0 and k in connector_map
    ]
    if not connector_types:
        connector_types = [ConnectorType.CCS2]  # Fallback für Europa

    # Status-Mapping
    status_map = {
        "OPEN": "online",
        "CONSTRUCTION": "wartung",
        "PERMIT": "online",
        "TEMP_CLOSED": "temporaer_geschlossen",
        "PLAN": "online",
    }
    status = status_map.get(site.get("status", "OPEN"), "online")

    # Ländercode
    country = _COUNTRY_MAP.get(site.get("address", {}).get("country", ""), "XX")

    return ChargingStation(
        station_id=str(site["id"]),
        name=f"Tesla Supercharger - {site['name']}",
        coordinate=(
            site["gps"]["latitude"],
            site["gps"]["longitude"],
        ),
        stalls=stalls,
        max_ladeleistung_kw=float(total * power),
        connector_types=connector_types,
        country=country,
        ist_24_7=True,
        status=status,
        letzte_datenAktualisierung=datetime.now(UTC),
    )
```

### 5.4 `charging_infrastructure.py` — Anpassung

Die bestehenden Hilfsfunktionen (`init_charging_infrastructure`, `get_stations_in_radius`, `get_stations_along_route`) müssen erweitert werden, um auch `TeslaChargingStationProvider` als Default nutzen zu können. Die `init_charging_infrastructure`-Funktion bekommt einen `provider_type`-Parameter:

```python
def init_charging_infrastructure(
    data_path: Path | None = None,
    provider_type: Literal["local_file", "tesla_db"] = "tesla_db",
) -> None:
```

Bei `"tesla_db"` wird `TeslaChargingStationProvider` mit der SQLite-DB verwendet, bei `"local_file"` der bestehende `LocalFileChargingStationProvider` mit JSON.

---

## 6. Refresh-Logik im Detail

```python
async def refresh(self) -> int:
    """Vollständiger Refresh aller Supercharger-Daten von supercharge.info."""

    # 1. Fetch alle Sites
    raw_sites = await self._client.fetch_all_sites()

    # 2. Filtere Europe
    euro_sites = [
        s for s in raw_sites
        if s.get("address", {}).get("region") == "Europe"
    ]

    # 3. Mappe in DB-Dicts
    db_records = [_site_to_db_record(s) for s in euro_sites]

    # 4. In Transaktion in DB schreiben
    self._db.replace_all_stations(db_records)

    # 5. In-Memory-Cache invalidieren
    self._stations = None

    return len(db_records)


def _site_to_db_record(site: dict) -> dict:
    """Wandelt ein supercharge.info-Dict in ein DB-Record-Dict um."""
    gps = site["gps"]
    address = site.get("address", {})
    plugs = site.get("plugs", {})

    connector_types = json.dumps([
        k for k, v in plugs.items()
        if v and v > 0 and k in (
            "nacs", "ccs1", "ccs2", "type2", "gbt", "chademo", "tpc"
        )
    ] or ["ccs2"])

    return {
        "supercharge_info_id": site["id"],
        "tesla_location_id": site.get("locationId"),
        "site_name": site["name"],
        "latitude": gps["latitude"],
        "longitude": gps["longitude"],
        "country_code": _COUNTRY_MAP.get(address.get("country", ""), "XX"),
        "stalls_v2": site.get("stalls", {}).get("v2", 0),
        "stalls_v3": site.get("stalls", {}).get("v3", 0),
        "stalls_v3_ultra": 0,  # supercharge.info hat kein separates v3ultra
        "stalls_v4": site.get("stalls", {}).get("v4", 0),
        "total_stalls": site.get("stallCount", 0),
        "power_kilowatt": site.get("powerKilowatt", 250),
        "status": site.get("status", "OPEN"),
        "connector_types": connector_types,
        "ist_24_7": 1,
        "date_opened": site.get("dateOpened"),
        "last_updated_utc": datetime.now(UTC).isoformat(),
    }
```

---

## 7. Schnittstelle zu bestehenden Komponenten

### `__init__.py` — neue Exports

```python
from .database import SQLiteDatabase
from .client import SuperchargeInfoClient
from .providers import (
    LocalFileChargingStationProvider,
    FakeChargingStationProvider,
    TeslaChargingStationProvider,
)
from .models import (
    ChargingStation,
    ChargingStationProvider,
    ChargingPricingTier,
    ChargingStationWithPricing,
    ConnectorType,
    StallType,
)

__all__ = [
    "ChargingStation",
    "ChargingPricingTier",
    "ChargingStationProvider",
    "ChargingStationWithPricing",
    "ConnectorType",
    "FakeChargingStationProvider",
    "LocalFileChargingStationProvider",
    "SQLiteDatabase",
    "StallType",
    "SuperchargeInfoClient",
    "TeslaChargingStationProvider",
    "get_charging_stations_along_route",
    "get_charging_stations_in_radius",
    "init_charging_infrastructure",
]
```

### Abhängigkeiten zu anderen Modulen

| Modul | Nutzung |
|---|---|
| `tripplanner.geo` | `Coordinate`, `haversine_distance_m()` für Radius-Suche |
| `tripplanner.routing.models` | `Route` (für `get_stations_along_route`) |
| `httpx` | HTTP-Client für supercharge.info API (bereits in `pyproject.toml`) |
| `sqlite3` | Python-Standardbibliothek — keine neue Dependency |

---

## 8. Teststrategie

### 8.1 Neue Tests

| Test-Datei | Was wird getestet |
|---|---|
| `tests/charging_infrastructure/test_client.py` | `SuperchargeInfoClient` mit aufgezeichneter API-Response |
| `tests/charging_infrastructure/test_database.py` | `SQLiteDatabase`: Tabellen-Erzeugung, CRUD, Transaktion, Ländermapping |
| `tests/charging_infrastructure/test_providers.py` (erweitert) | `TeslaChargingStationProvider`: Refresh, Mapping, Radius-Suche, Länderfilter |

### 8.2 Test-Fixtures (neu)

| Fixture-Datei | Inhalt |
|---|---|
| `tests/fixtures/charging_infrastructure/supercharge_info_response_3sites.json` | 3 supercharge.info-Sites (DE, DK, SE, verschiedene Stall-Typen) für Mock-Tests |
| `tests/fixtures/charging_infrastructure/supercharge_info_dbinfo.json` | Beispiel-Response von `/databaseInfo` |

### 8.3 Testfälle `test_client.py`

```python
class TestSuperchargeInfoClient:
    """Tests für den HTTP-Client der supercharge.info-API."""

    async def test_fetch_all_sites_returns_list(self, mock_client):
        """Prüft, dass allSites eine Liste zurückgibt."""
        sites = await mock_client.fetch_all_sites()
        assert isinstance(sites, list)
        assert len(sites) > 0

    async def test_fetch_all_sites_structure(self, mock_client, sample_site):
        """Prüft die Struktur eines Site-Eintrags (Pflichtfelder)."""
        sites = await mock_client.fetch_all_sites()
        site = sites[0]
        assert "id" in site
        assert "gps" in site
        assert "latitude" in site["gps"]
        assert "longitude" in site["gps"]
        assert "address" in site
        assert "region" in site["address"]
        assert "stallCount" in site
        assert "powerKilowatt" in site

    async def test_fetch_database_info(self, mock_client):
        """Prüft databaseInfo-Endpunkt."""
        info = await mock_client.fetch_database_info()
        assert "lastModified" in info
        assert isinstance(info["lastModified"], int)
```

### 8.4 Testfälle `test_database.py`

```python
class TestSQLiteDatabase:
    """Tests für den SQLite-Datenbank-Manager."""

    async def test_initialize_creates_tables(self, tmp_db):
        """Prüft, dass bei initialize() alle Tabellen existieren."""
        tmp_db.initialize()
        tables = tmp_db._cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t[0] for t in tables}
        assert "charging_stations" in table_names
        assert "charging_pricing" in table_names
        assert "db_meta" in table_names

    async def test_replace_all_stations(self, tmp_db, sample_db_records):
        """Prüft replace_all_stations in Transaktion."""
        tmp_db.initialize()
        count = tmp_db.replace_all_stations(sample_db_records)
        assert count == len(sample_db_records)
        assert tmp_db.station_count == len(sample_db_records)

    async def test_replace_all_stations_is_idempotent(self, tmp_db, sample_db_records):
        """Zweimaliges replace_all_stations = einmal."""
        tmp_db.initialize()
        tmp_db.replace_all_stations(sample_db_records)
        tmp_db.replace_all_stations(sample_db_records)
        assert tmp_db.station_count == len(sample_db_records)

    async def test_load_stations_country_filter(self, tmp_db, sample_db_records):
        """Prüft country_filter beim Laden."""
        tmp_db.initialize()
        tmp_db.replace_all_stations(sample_db_records)
        de_only = tmp_db.load_stations(country_filter={"DE"})
        assert all(r["country_code"] == "DE" for r in de_only)
```

### 8.5 Testfälle `test_providers.py` (erweitert)

```python
class TestTeslaChargingStationProvider:
    """Tests für TeslaChargingStationProvider."""

    async def test_refresh_filters_europe(self, provider_with_mock_client):
        """Prüft, dass nur europäische Sites in die DB gelangen."""
        count = await provider_with_mock_client.refresh()
        assert count > 0
        # USA-Sites aus der Mock-Response wurden rausgefiltert

    async def test_get_stations_in_radius(self, provider_with_seeded_db):
        """Radius-Suche aus der SQLite-DB."""
        stations = await provider_with_seeded_db.get_stations_in_radius(
            (52.5, 13.4), 10.0
        )
        assert len(stations) >= 1
        assert stations[0].country == "DE"

    async def test_get_stations_in_radius_empty(self, provider_with_seeded_db):
        """Leeres Ergebnis bei zu kleinem Radius."""
        stations = await provider_with_seeded_db.get_stations_in_radius(
            (48.0, 2.0), 1.0  # Paris, wahrscheinlich keine Stationen im Fixture
        )
        assert len(stations) == 0

    async def test_station_mapping(self, provider_with_seeded_db):
        """Prüft korrektes Mapping von supercharge.info → ChargingStation."""
        stations = await provider_with_seeded_db.get_stations_in_radius(
            (52.5, 13.4), 200.0
        )
        station = stations[0]
        assert isinstance(station.station_id, str)
        assert isinstance(station.max_ladeleistung_kw, float)
        assert station.max_ladeleistung_kw > 0
        assert len(station.stalls) > 0

    async def test_auto_init_creates_db_if_empty(self, tmp_path):
        """Bei leerer DB+fehlendem Client: automatischer Init-Versuch."""
        db_path = tmp_path / "test_empty.db"
        # Provider mit leerem DB-Pfad und client=None
        # Sollte keine Exception werfen, sondern leere Stationsliste liefern
        provider = TeslaChargingStationProvider(db_path=db_path)
        stations = await provider.get_stations_in_radius((52.5, 13.4), 10.0)
        assert stations == []
```

### 8.6 Test-Fixture für supercharge.info API-Response

Die Test-Fixture enthält 3 minimale, realistische Site-Einträge — einen pro Land (DE, DK, SE). Ein vierter Eintrag ist "USA" (nicht Europa) um den Europe-Filter zu testen:

```json
[
  {
    "id": 3506,
    "locationId": "BarcelonaUrbanessupercharger",
    "name": "Barcelona, Spain - L'Illa Diagonal",
    "status": "OPEN",
    "address": {
      "country": "Spain",
      "region": "Europe"
    },
    "gps": { "latitude": 41.3895, "longitude": 2.1337 },
    "stallCount": 4,
    "powerKilowatt": 125,
    "stalls": { "v2": 4 },
    "plugs": { "ccs2": 4, "type2": 4 },
    "dateOpened": "2021-07-01"
  },
  {
    "id": 1234,
    "name": "Testville, USA",
    "status": "OPEN",
    "address": {
      "country": "USA",
      "region": "North America"
    },
    "gps": { "latitude": 39.0, "longitude": -77.5 },
    "stallCount": 8,
    "powerKilowatt": 250,
    "stalls": { "v3": 8 },
    "plugs": { "nacs": 8 },
    "dateOpened": "2022-01-01"
  },
  {
    "id": 5678,
    "locationId": "kopenhagensupercharger",
    "name": "Copenhagen, Denmark",
    "status": "OPEN",
    "address": {
      "country": "Denmark",
      "region": "Europe"
    },
    "gps": { "latitude": 55.6761, "longitude": 12.5683 },
    "stallCount": 8,
    "powerKilowatt": 250,
    "stalls": { "v3": 8 },
    "plugs": { "ccs2": 8, "type2": 8 },
    "dateOpened": "2019-06-15"
  },
  {
    "id": 9012,
    "locationId": "malmosupercharger",
    "name": "Malmö, Sweden",
    "status": "OPEN",
    "address": {
      "country": "Sweden",
      "region": "Europe"
    },
    "gps": { "latitude": 55.5941, "longitude": 13.0039 },
    "stallCount": 8,
    "powerKilowatt": 250,
    "stalls": { "v3": 8 },
    "plugs": { "ccs2": 8, "type2": 8 },
    "dateOpened": "2020-03-20"
  }
]
```

---

## 9. Aufgaben-Checkliste

### Phase 1: Datenmodelle

- [ ] Task 1.1: `src/tripplanner/charging_infrastructure/models.py` erweitern um `ChargingPricingTier` und `ChargingStationWithPricing`
- [ ] Task 1.2: Tests für neue Modelle schreiben (Validierung, Serialisierung)

### Phase 2: Client

- [ ] Task 2.1: `src/tripplanner/charging_infrastructure/client.py` — `SuperchargeInfoClient` mit allen 3 Endpunkten
- [ ] Task 2.2: `tests/fixtures/charging_infrastructure/supercharge_info_response_3sites.json` erstellen
- [ ] Task 2.3: `tests/charging_infrastructure/test_client.py` schreiben (mock-basiert)

### Phase 3: Datenbank

- [ ] Task 3.1: `src/tripplanner/charging_infrastructure/database.py` — `SQLiteDatabase` mit Schema, CRUD, Transaktionen
- [ ] Task 3.2: `tests/charging_infrastructure/test_database.py` schreiben (mit `tmp_path`+SQLite in-Memory)

### Phase 4: Provider

- [ ] Task 4.1: `src/tripplanner/charging_infrastructure/providers.py` erweitern um `TeslaChargingStationProvider`
- [ ] Task 4.2: `__init__.py` aktualisieren (neue Exports)
- [ ] Task 4.3: `charging_infrastructure.py` anpassen (Unterstützung für `TeslaChargingStationProvider` in `init_charging_infrastructure`)
- [ ] Task 4.4: `tests/charging_infrastructure/test_providers.py` erweitern um `TeslaChargingStationProvider`-Tests

### Phase 5: Integration & Verifikation

- [ ] Task 5.1: `uv run hk check --all` — Linting, Formatting, Type-Checking
- [ ] Task 5.2: `uv run pytest -m "not integration"` — alle Tests grün
- [ ] Task 5.3: Coverage-Schwelle (85 %) prüfen
- [ ] Task 5.4: Integrationstest: `TeslaChargingStationProvider.refresh()` gegen echte supercharge.info-API (`@pytest.mark.integration`)

---

## 10. Risiken & offene Fragen

1. **supercharge.info-API-Verfügbarkeit:** Die API ist inoffiziell (community-betrieben). Ein Ausfall oder eine API-Änderung kann den Refresh unterbrechen.
   - *Minderung:* Lokale SQLite-DB bleibt erhalten; der Provider funktioniert auch ohne Refresh mit den zuletzt gespeicherten Daten.

2. **Stall-Typ-Klassifizierung:** supercharge.info unterscheidet nicht zwischen V3 und V3_ULTRA in `stalls.v3`. Die Klassifizierung per `powerKilowatt` ist eine Näherung.
   - *Minderung:* Die Ladekurven-Auswahl in `battery` (Phase 4) kann bei Bedarf auf `powerKilowatt` pro Stall zurückgreifen, nicht auf den Enum-Namen.

3. **Ländermapping-Vollständigkeit:** supercharge.info verwendet ausgeschriebene Ländernamen (z. B. "Czech Republic"). Das Mapping muss alle europäischen Länder abdecken.
   - *Minderung:* Unbekannte Länder → `"XX"`. Diese Stationen werden dann bei `country_filter` nicht gefunden, aber der Provider funktioniert trotzdem.

4. **Pricing-Phase-2:** Das Pricing erfordert einen Browser (Akamai WAF). Das bedeutet:
   - Entwicklungsaufwand für Safari-Automation (AppleScript) und/oder Playwright-Fallback
   - Abhängigkeit von macOS für Safari (Playwright als Cross-Plattform-Fallback)
   - Kein Pricing-Refresh in CI (kein echter Browser verfügbar)
   - *Minderung:* Pricing-Daten sind optional; Stationsdaten (ohne Pricing) sind der primäre Deliverable.

5. **Datenbank-Größe:** supercharge.info hat ~10.000+ Sites global, davon schätzungsweise 3.000-4.000 in Europa. SQLite handhabt dies problemlos (< 10 MB).
   - Keine Performance-Bedenken.

---

## 11. Abgrenzung zum bestehenden `LocalFileChargingStationProvider`

| Aspekt | `LocalFileChargingStationProvider` | `TeslaChargingStationProvider` |
|---|---|---|
| Speicher | JSON-Datei | SQLite-Datenbank |
| Refresh | Manuell (Datei ersetzen) | `refresh()` via supercharge.info-API |
| Pricing | Nicht unterstützt | `charging_pricing`-Tabelle (Phase 2) |
| Indizierung | Keine (O(n)-Scan) | B-Tree auf lat/lon, country, status |
| Transaktionen | Keine (atomares File-Write) | SQL-Transaktionen (Rollback bei Fehler) |
| Datenqualität | Handkuratierter Snapshot | Live von supercharge.info |
| Abhängigkeiten | Keine außer `json` | `sqlite3`, `httpx` |
| Ziel | Unit-Tests, minimale Setup | Produktion, Integrationstests |

Beide Provider implementieren `ChargingStationProvider` und sind über das Protocol austauschbar. Die `init_charging_infrastructure()`-Funktion kann zwischen beiden umschalten.

---

## 12. Datei-Übersicht (neu / geändert)

```
src/tripplanner/charging_infrastructure/
├── __init__.py              # GEÄNDERT: neue Exports
├── models.py                # GEÄNDERT: neue Pricing-Modelle
├── client.py                # NEU: SuperchargeInfoClient
├── database.py              # NEU: SQLiteDatabase
├── providers.py             # GEÄNDERT: TeslaChargingStationProvider
└── charging_infrastructure.py  # GEÄNDERT: init-Unterstützung für neuen Provider

tests/charging_infrastructure/
├── conftest.py              # GEÄNDERT: neue Fixtures für DB + Client
├── test_client.py           # NEU
├── test_database.py         # NEU
├── test_providers.py        # GEÄNDERT: TeslaChargingStationProvider-Tests
└── test_models.py           # GEÄNDERT: Pricing-Modell-Tests

tests/fixtures/charging_infrastructure/
├── tesla_supercharger_snapshot.json  # unverändert
├── route_sample.json                 # unverändert
└── supercharge_info_response_3sites.json  # NEU

data/
├── supercharger_snapshot.json        # unverändert
└── (tesla_superchargers.db)          # NEU: wird bei refresh() erzeugt
```
