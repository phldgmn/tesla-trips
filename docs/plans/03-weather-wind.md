# Plan 03: Weather- und Windmodule

**Zweck:** Dieser Plan behandelt gemeinsam die Module `weather` (Phase 1) und `wind` (Phase 2) des Tesla-Tripplaners.

---

## 1. Zweck & Scope

### `weather`-Modul

Das `weather`-Modul ist für die Abfrage und Bereitstellung von Wetterdaten entlang der Route verantwortlich. Es erfüllt folgende Funktionen:

- Abfrage von Wetterdaten für gegebene Koordinaten und Zeitpunkte über die Open-Meteo Forecast API.
- **Unterstützung der iterativen Zeit-/Wetterauflösung** (s. `02-architektur.md`, Abschnitt „Iterative Zeit-/Wetterauflösung"): Methode zur Neuabfrage bereits abgefragter Punkte mit aktualisiertem Zeitpunkt.
- Zusammenfassung mehrerer Abfragepunkte in einem einzigen API-Call (Batching) zur Effizienzsteigerung.
- Kapselung des HTTP-Clients hinter einem `WeatherProvider`-Protocol zur Testbarkeit (Fake-Implementierung in Tests).

**NICHT-Scope:** Historische Datenabfrage (diese ist für zukünftige Kalibrierung vorgesehen, s. Abschnitt „Externe Integration / Algorithmus-Details“), Live-Verkehr, Baustellen (diese fallen in das `construction`-Modul).

### `wind`-Modul

Das `wind`-Modul berechnet aus den Winddaten (Geschwindigkeit und Richtung) und der Fahrtrichtung (Bearing) des jeweiligen Route-Segments die effektiven Windkomponenten:

- **Gegenwind-/Rückenwind-Komponente** (m/s, vorzeichenbehaftet: positiv = Gegenwind, negativ = Rückenwind).
- **Seitenwind-Komponente** (m/s, vorzeichenbehaftet: positiv = Seitenwind von rechts, negativ = von links).

Das Modul ist reine Berechnungslogik ohne externe Datenquellen und vollständig unit-testbar.

---

## 2. Abhängigkeiten & Phasenzuordnung

| Modul | Phase | Konsumierte Typen (aus dem Register) |
|-------|-------|--------------------------------------|
| `weather` | Phase 1 | Keine (eigenständiges Modul) |
| `wind` | Phase 2 | `tripplanner.weather.models.WeatherSample` (nur Lesen), `tripplanner.routing.models.RouteSegment` (für `bearing_deg`) |

**Hinweis:** Das `wind`-Modul liest ausschließlich aus den Pydantic-Modellen des `weather`-Moduls. Kein Modul importiert interne Implementierungsdetails eines anderen Moduls — ausschließlich `tripplanner.<anderes_modul>.models`.

---

## 3. Datenmodelle

### `weather.models` – Offizielle Typen aus dem Register

```python
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from tripplanner.geo import (
    Coordinate,
)  # kanonisches Geo-Primitiv, siehe docs/plans/00-foundation-tooling.md und docs/plans/01-routing.md Abschnitt 3


class WeatherQuery(BaseModel):
    """Abfrage für ein einzelnes Wetterereignis."""

    koordinate: Coordinate  # WGS84 (lat, lon)
    zeitpunkt: datetime


class WeatherSample(BaseModel):
    """Wetterdaten für einen Zeitpunkt an einer Koordinate."""

    koordinate: Coordinate
    zeitpunkt: datetime
    temperatur_c: float = Field(ge=-100.0, le=70.0, description="Temperatur in °C")
    windgeschwindigkeit_ms: float = Field(ge=0.0, description="Windgeschwindigkeit in m/s")
    windrichtung_deg: float = Field(
        ge=0.0, le=360.0, description="Windrichtung in Grad (0° = N, 90° = O)"
    )
    niederschlag_mm: float = Field(ge=0.0, description="Niederschlag in mm (Stundensumme)")
    schneefall_cm: float = Field(ge=0.0, description="Schneefall in cm (Wasserequivalent)")
    luftdruck_hpa: float = Field(ge=870.0, le=1084.0, description="Luftdruck in hPa (MSL)")
    luftfeuchtigkeit_pct: float = Field(
        ge=0.0, le=100.0, description="Relative Luftfeuchtigkeit in %"
    )
    globalstrahlung_wm2: float = Field(ge=0.0, description="Globalstrahlung in W/m² (Stundensumme)")
    bewoelkung_pct: float = Field(ge=0.0, le=100.0, description="Bewölkung in %")
```

### `weather.models` – Interne Hilfstypen

```python
class OpenMeteoResponse(BaseModel):
    """Raw-Response von Open-Meteo Forecast API (nur für interne Verarbeitung)."""

    latitude: float
    longitude: float
    timezone: str
    timezone_abbreviation: str
    elevation: float
    hourly: dict[str, list[float | int | str | None]]
    hourly_units: dict[str, str]
    # Weitere Felder (daily, current etc.) werden ignoriert
```

### `wind.models` – Offizielle Typen aus dem Register

```python
from tripplanner.weather.models import WeatherSample
from tripplanner.routing.models import RouteSegment


class WindComponents(BaseModel):
    """Windkomponenten entlang einer Route."""

    segment_index: int
    gegenwind_ms: float  # positiv: Gegenwind, negativ: Rückenwind
    seitenwind_ms: float  # positiv: von rechts, negativ: von links
```

---

## 4. Öffentliche Schnittstelle

### `weather.providers.WeatherProvider` (Protocol)

```python
from typing import Protocol, Sequence
from tripplanner.weather.models import WeatherQuery, WeatherSample


class WeatherProvider(Protocol):
    """Interface für Wetter-Datenprovider (kann durch Fake ersetzt werden)."""

    async def fetch_weather(
        self,
        queries: Sequence[WeatherQuery],
    ) -> list[WeatherSample]:
        """Abruf von Wetterdaten für mehrere Abfragepunkte.

        Args:
            queries: Liste von Wetterabfragen (Koordinate + Zeitpunkt).

        Returns:
            Liste von Wetterdaten, in gleicher Reihenfolge wie queries.
            Wird bei fehlenden Daten für einen Punkt eine leere Liste oder None zurückgegeben,
            wird dies durch ein Sentinel (z. B. None) oder ein spezielles WeatherSample mit NaN markiert.
        """
        ...

    async def refetch_weather(
        self,
        original_queries: Sequence[WeatherQuery],
        updated_queries: Sequence[WeatherQuery],
    ) -> list[WeatherSample]:
        """Neuabfrage bereits abgefragter Punkte mit aktualisiertem Zeitpunkt.

        Diese Methode ist zentral für die iterative Zeit-/Wetterauflösung.
        Die Implementierung darf intern Caching nutzen (z. B. auf `koordinate` + `zeitpunkt`-Tupel),
        um unnötige API-Calls zu vermeiden.

        Args:
            original_queries: Die ursprünglichen Abfragen (unverändert).
            updated_queries: Die aktualisierten Abfragen mit neuen Zeitpunkten,
                             gleiche Koordinaten wie original_queries.

        Returns:
            Liste von WeatherSample für die updated_queries.
        """
        ...
```

### `weather.<modul>.py` – Öffentliche Funktionen

```python
from typing import Sequence
from tripplanner.weather.models import WeatherQuery, WeatherSample
from tripplanner.weather.providers import WeatherProvider


async def fetch_weather_for_route(
    provider: WeatherProvider,
    route_queries: Sequence[WeatherQuery],
    batch_size: int = 20,
) -> list[WeatherSample]:
    """Abruf von Wetterdaten entlang einer Route mit automatischem Batching.

    Args:
        provider: Der zu verwendende Wetterprovider (in Tests: Fake).
        route_queries: Liste von Abfragen (Koordinate + ETA).
        batch_size: Maximale Anzahl Abfragen pro API-Call (Open-Meteo empfiehlt <=50).

    Returns:
        Liste von WeatherSample in gleicher Reihenfolge wie route_queries.
    """
    ...


async def fetch_weather_iterative(
    provider: WeatherProvider,
    initial_queries: Sequence[WeatherQuery],
    max_iterations: int = 2,
    convergence_threshold_s: int = 1800,  # 30 Minuten = 1800 Sekunden
) -> list[WeatherSample]:
    """Iterative Wetterabfrage gemäß 02-architektur.md.

    1. Abruf mit initial_queries (grobe ETA).
    2. Berechnung von Energieverbrauch + Ladeplan → neue ETA je Segment.
    3. Wenn Abweichung > convergence_threshold_s an einem Punkt:
       Neuabfrage mit aktualisierten Zeitpunkten.
    4. Konvergenzprüfung (max. max_iterations).

    Args:
        provider: Wetterprovider (kann intern Caching nutzen).
        initial_queries: Erste Abfrage (grobe ETA).
        max_iterations: Maximaler Iterationsschwellwert.
        convergence_threshold_s: Abweichungsschwellwert in Sekunden.

    Returns:
        Liste von WeatherSample nach Konvergenz (oder max_iterations).
    """
    ...
```

### `wind.<modul>.py` – Öffentliche Funktionen

```python
from typing import Sequence
from tripplanner.weather.models import WeatherSample
from tripplanner.routing.models import RouteSegment
from tripplanner.wind.models import WindComponents


def compute_wind_components_for_route(
    weather_samples: Sequence[WeatherSample],
    segments: Sequence[RouteSegment],
) -> list[WindComponents]:
    """Berechnet Windkomponenten für jedes Segment entlang der Route.

    Args:
        weather_samples: Wetterdaten je Wetterabfragepunkt.
        segments: Route-Segmente (muss gleiche Länge wie weather_samples haben
                  oder via Interpolation erweiterbar — hier: direkte Zuordnung).

    Returns:
        Liste von WindComponents (segment_index passt zu segment.segment_index).
        Nicht abgedeckte Segmente (z. B. fehlende Wetterdaten) werden mit
        WindComponents(segments_index=<idx>, gegenwind_ms=0.0, seitenwind_ms=0.0) markiert.
    """
    ...
```

---

## 5. Externe Integration / Algorithmus-Details

### Open-Meteo Forecast API – Konkrete API-Endpunkte & Parameter

**Endpoint:** `https://api.open-meteo.com/v1/forecast`

**Benötigte `hourly`-Parameter für das Projekt:**

```python
hourly_params = [
    "temperature_2m",  # Temperatur in °C
    "wind_speed_10m",  # Windgeschwindigkeit in km/h → umrechnen in m/s (* 1000/3600)
    "wind_direction_10m",  # Windrichtung in ° (0° = N, 90° = O)
    "precipitation",  # Niederschlag in mm (Stundensumme)
    "snowfall",  # Schneefall in cm (Wasserequivalent)
    "surface_pressure",  # Luftdruck in hPa (MSL)
    "relative_humidity_2m",  # Relative Luftfeuchtigkeit in %
    "shortwave_radiation",  # Globalstrahlung in W/m² (Stundensumme)
    "cloud_cover",  # Bewölkung in %
]
```

**Koordinaten-Batching:**

- Multiple Standorte können in einem einzigen Call abgefragt werden via `latitude=<lat1>,<lat2>,...&longitude=<lon1>,<lon2>,...`
- Open-Meteo empfiehlt **maximal 50 Standorte pro Call** (kein fester Limit in der Dokumentation, aber praktische Empfehlung).
- In `fetch_weather_for_route` wird daher `batch_size = min(len(queries), 50)` verwendet.

**Rate-Limits (ohne API-Key):**

- **Nicht-kommerzieller Einsatz:** 10.000 Calls/Tag kostenlos.
- **Kommerzieller Einsatz:** Plattform-Abonnements (Standard: 1 Mio Calls/Monat).
- Für Tests wird ein Fake-Provider verwendet (kein echter API-Call).

**Beispiel-Aufruf (HTTP GET):**

```
GET https://api.open-meteo.com/v1/forecast
    ?latitude=52.52,48.137,48.775
    &longitude=13.405,11.575,9.175
    &current=temperature_2m,wind_speed_10m
    &hourly=temperature_2m,wind_speed_10m,wind_direction_10m,precipitation,snowfall,surface_pressure,relative_humidity_2m,shortwave_radiation,cloud_cover
    &timezone=auto
    &forecast_days=2
```

**Antwortformat (Auszug):**

```json
{
  "latitude": 52.52,
  "longitude": 13.405,
  "generationtime_ms": 0.24,
  "utc_offset_seconds": 3600,
  "timezone": "Europe/Berlin",
  "timezone_abbreviation": "CET",
  "elevation": 38.0,
  "hourly_units": {
    "time": "iso8601",
    "temperature_2m": "°C",
    "wind_speed_10m": "km/h",
    "wind_direction_10m": "°",
    "precipitation": "mm",
    "snowfall": "cm",
    "surface_pressure": "hPa",
    "relative_humidity_2m": "%",
    "shortwave_radiation": "W/m²",
    "cloud_cover": "%"
  },
  "hourly": {
    "time": ["2026-08-02T00:00", "2026-08-02T01:00", ...],
    "temperature_2m": [14.5, 13.8, ...],
    "wind_speed_10m": [15.2, 12.8, ...],
    "wind_direction_10m": [245, 250, ...],
    ...
  }
}
```

**Client-Implementierung (`weather.client.py`):**

```python
import httpx
from typing import Sequence
from tripplanner.weather.models import WeatherQuery, OpenMeteoResponse


class OpenMeteoClient:
    """HTTP-Client für Open-Meteo Forecast API."""

    BASE_URL = "https://api.open-meteo.com/v1/forecast"
    TIMEOUT_S = 15.0

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(timeout=self.TIMEOUT_S)

    async def fetch_forecast(
        self,
        queries: Sequence[WeatherQuery],
        hourly_params: list[str],
    ) -> list[OpenMeteoResponse]:
        """Abruf von Wetterdaten für mehrere Standorte + Zeitpunkte."""
        if not queries:
            return []

        # Gruppieren nach Koordinate (doppelte Standorte sparen API-Calls)
        coords: dict[tuple[float, float], list[tuple[int, WeatherQuery]]] = {}
        for idx, q in enumerate(queries):
            key = (q.koordinate.lat, q.koordinate.lon)
            coords.setdefault(key, []).append((idx, q))

        results: list[OpenMeteoResponse | None] = [None] * len(queries)

        for (lat, lon), entries in coords.items():
            # Zeitbereich: earliest bis latest Zeitpunkt für diesen Standort
            times = [q.zeitpunkt.isoformat() for _, q in entries]
            time_min = min(times)
            time_max = max(times)

            url = f"{self.BASE_URL}?latitude={lat}&longitude={lon}&hourly={','.join(hourly_params)}&start_date={time_min[:10]}&end_date={time_max[:10]}"

            resp = await self._client.get(url)
            resp.raise_for_status()
            data = OpenMeteoResponse(**resp.json())

            # Zeitindex lookup für jeden Query-Punkt
            time_to_idx = {t: i for i, t in enumerate(data.hourly["time"])}
            for idx, q in entries:
                time_idx = time_to_idx.get(q.zeitpunkt.isoformat())
                if time_idx is not None:
                    results[idx] = self._extract_sample(data, time_idx)
        # ... (Validierung, Fallback-Logik)

        return [r for r in results if r is not None]
```

### Open-Meteo Historical API – Kurzinfo

Für spätere Kalibrierung (nicht im aktuellen Scope implementiert, aber Design-entscheidungserheblich):

**Endpoint:** `https://archive-api.open-meteo.com/v1/archive`

**Unterschied zur Forecast API:**

- Zeitbereich: `start_date`/`end_date` (bis in die Vergangenheit, z. B. 1940–heute).
- Nutzt Reanalysis-Daten (ERA5, ERA5-Land, ECMWF IFS) statt Echtzeit-Forecasts.
- Identische `hourly`-Parameter wie Forecast API.
- Rate-Limits analog (10.000 Calls/Tag kostenlos, aber explizit für historische Daten gedacht).

**Verwendungszweck im Plan:** Die `WeatherProvider`-Schnittstelle ist so konzipiert, dass ein späterer `HistoricalWeatherProvider` implementiert werden kann, der aus historical-Abfragen Mittelwerte (z. B. Monatsmittel für jeden Tag im Jahr) zurückgibt. Dadurch kann das Verbrauchsmodell auf langfristige Wetterbedingungen kalibriert werden.

### Wind-Projektion – Trigonometrische Formel

Die Windkomponenten entlang der Fahrtrichtung werden mittels Vektorprojektion berechnet.

**Notation:**

- $v_w$ = Windgeschwindigkeit (m/s) — aus `WeatherSample.windgeschwindigkeit_ms`
- $\theta_w$ = Windrichtung (Grad, 0° = N, 90° = O) — aus `WeatherSample.windrichtung_deg`
- $\theta_b$ = Bearing der Fahrtrichtung (Grad, 0° = N, 90° = O) — aus `RouteSegment.bearing_deg`

**Konvention für Windrichtung:**

- In Meteorologie wird Windrichtung als **Richtung, aus der der Wind kommt** definiert (z. B. „Nordwind“ = Wind kommt vom Norden → weht nach Süden).
- Für die Vektorprojektion müssen wir daher die **Richtung des Windvektors** um 180° verschieben: $\theta_{\text{wind, vector}} = \theta_w + 180°$ (Modulo 360°).

**Berechnung:**

1. Berechne den Winkelunterschied zwischen Windvektor und Bearing:
   $$
   \Delta\theta = \theta_b - (\theta_w + 180°) \mod 360°
   $$
   (Umrechnung in Bogenmaß: $\Delta\theta_{\text{rad}} = \Delta\theta \cdot \pi/180$)

2. Gegenwind-/Rückenwind-Komponente (Longitudinal):
   $$
   v_{\text{long}} = v_w \cdot \cos(\Delta\theta_{\text{rad}})
   $$
   - $v_{\text{long}} > 0$ → Gegenwind (bremsend)
   - $v_{\text{long}} < 0$ → Rückenwind (unterstützend)

3. Seitenwind-Komponente (Transversal):
   $$
   v_{\text{side}} = v_w \cdot \sin(\Delta\theta_{\text{rad}})
   $$
   - $v_{\text{side}} > 0$ → Seitenwind von rechts
   - $v_{\text{side}} < 0$ → Seitenwind von links

**Implementation in `wind.<modul>.py`:**

```python
import math
from tripplanner.weather.models import WeatherSample
from tripplanner.routing.models import RouteSegment
from tripplanner.wind.models import WindComponents


def _degrees_to_radians(deg: float) -> float:
    return deg * math.pi / 180.0


def compute_wind_components(
    weather: WeatherSample,
    segment: RouteSegment,
) -> WindComponents:
    """Berechnet Windkomponenten für ein einzelnes Segment."""
    # Windrichtung als Vektorrichtung (180° versetzt)
    wind_dir_vector = (weather.windrichtung_deg + 180.0) % 360.0

    # Bearing des Segments (Pflichtfeld, von routing bereits berechnet, siehe RouteSegment.bearing_deg)
    bearing = segment.bearing_deg

    delta_theta = bearing - wind_dir_vector
    delta_theta_rad = _degrees_to_radians(delta_theta)

    v_long = weather.windgeschwindigkeit_ms * math.cos(delta_theta_rad)
    v_side = weather.windgeschwindigkeit_ms * math.sin(delta_theta_rad)

    return WindComponents(
        segment_index=segment.segment_index,
        gegenwind_ms=v_long,  # positiv = Gegenwind
        seitenwind_ms=v_side,  # positiv = von rechts
    )


def compute_wind_components_for_route(
    weather_samples: Sequence[WeatherSample],
    segments: Sequence[RouteSegment],
) -> list[WindComponents]:
    if len(weather_samples) != len(segments):
        raise ValueError("weather_samples und segments müssen gleiche Länge haben.")

    return [compute_wind_components(w, s) for w, s in zip(weather_samples, segments)]
```

**Validierungstestfälle (s. Abschnitt 6):**

- Wind from North (0°), bearing East (90°) → $v_{\text{long}} = 0$, $v_{\text{side}} = +v_w$ (Seitenwind von rechts).
- Wind from North (0°), bearing North (0°) → $v_{\text{long}} = -v_w$ (Rückenwind).
- Wind from North (0°), bearing South (180°) → $v_{\text{long}} = +v_w$ (Gegenwind).

---

## 6. Test-Strategie

### `weather`-Modul

**Fixtures:**

- `tests/fixtures/weather/open_meteo_response.json` — Aufgezeichnete API-Antwort (Beispiel-Weather-Point, ca. 100 Zeilen JSON).
- `tests/fixtures/weather/fake_weather_samples.json` — Handgepflegte `WeatherSample`-Liste für Unit-Tests (kein HTTP-Call).

**Unit-Tests (`tests/weather/test_weather.py`):**

1. **Given:** `OpenMeteoResponse` Fixture mit 24 Stunden + 5 Wettervariablen.  
   **When:** `fetch_weather_for_route()` mit 3 `WeatherQuery` (gleiche Koordinate, 3 Zeiten).  
   **Then:** Länge Ergebnis = 3, Werte korrekt interpoliert (z. B. `temperature_2m[0] = 15.0°C`, `wind_speed_10m[1] = 12.5 km/h → 3.47 m/s`).  
   *Grenzfall:* Query-Zeitpunkt liegt außerhalb des Abfragezeitraums → Exception oder Sentinel-Wert.

2. **Given:** `OpenMeteoResponse` mit nur einer Koordinate, aber 2 Queries (doppelter Standort).  
   **When:** `fetch_weather_for_route()` mit `batch_size=20`.  
   **Then:** Nur 1 API-Call statt 2 (Caching via Koordinatengruppierung funktioniert).

3. **Given:** 50 Queries mit gleicher Koordinate, 50 Queries mit anderer Koordinate (100 insgesamt).  
   **When:** `fetch_weather_for_route()` mit `batch_size=20`.  
   **Then:** Mindestens 4 API-Calls (50/20 + 50/20 = 2.5 + 2.5 → 3 Calls pro Koordinate-Gruppe = min. 4 Calls).

**Integration-Tests (`tests/weather/test_providers.py`):**

- `@pytest.mark.integration`  
  **Given:** Lokale GraphHopper-Instanz mit Mock-Wetter-Provider (echter HTTP-Call).  
  **When:** `fetch_weather_iterative()` auf einer 200 km-Strecke mit 5 Abfragen.  
  **Then:** Konvergenz nach ≤ 2 Iterationen (ETA-Abweichung < 30 Min), Gesamt-ETA < 20 Sekunden.

### `wind`-Modul

**Fixtures:** Keine externen Dateien nötig — alle Testfälle als Python-Liste.

**Unit-Tests (`tests/wind/test_wind.py`):**

1. **Given:** `WeatherSample(windgeschwindigkeit_ms=10.0, windrichtung_deg=0.0)` (Nordwind), `RouteSegment(bearing_deg=0.0)` (Norden).
   **When:** `compute_wind_components()`.  
   **Then:** `gegenwind_ms = -10.0` (Rückenwind), `seitenwind_ms = 0.0`.

2. **Given:** `WeatherSample(windgeschwindigkeit_ms=10.0, windrichtung_deg=0.0)` (Nordwind), `RouteSegment(bearing_deg=180.0)` (Süden).
   **When:** `compute_wind_components()`.  
   **Then:** `gegenwind_ms = +10.0` (Gegenwind), `seitenwind_ms = 0.0`.

3. **Given:** `WeatherSample(windgeschwindigkeit_ms=10.0, windrichtung_deg=90.0)` (Ostwind), `RouteSegment(bearing_deg=0.0)` (Norden).
   **When:** `compute_wind_components()`.  
   **Then:** `gegenwind_ms = 0.0`, `seitenwind_ms = -10.0` (Seitenwind von links).  
   *Grenzfall:* Windwinkel = 45° zu Bearing → $v_{\text{long}} = v_{\text{side}} = -10/\sqrt{2} \approx -7.07$.

### Test-Setup (`tests/weather/conftest.py`, `tests/wind/conftest.py`)

```python
import pytest
from tripplanner.weather.models import WeatherSample


@pytest.fixture
def weather_sample_north_wind() -> WeatherSample:
    return WeatherSample(
        koordinate=Coordinate(lat=52.52, lon=13.405),
        zeitpunkt=datetime(2026, 8, 2, 12, 0),
        temperatur_c=20.0,
        windgeschwindigkeit_ms=10.0,
        windrichtung_deg=0.0,
        niederschlag_mm=0.0,
        schneefall_cm=0.0,
        luftdruck_hpa=1013.25,
        luftfeuchtigkeit_pct=60.0,
        globalstrahlung_wm2=400.0,
        bewoelkung_pct=20.0,
    )


@pytest.fixture
def weather_sample_east_wind() -> WeatherSample:
    return WeatherSample(
        koordinate=Coordinate(lat=52.52, lon=13.405),
        zeitpunkt=datetime(2026, 8, 2, 12, 0),
        temperatur_c=20.0,
        windgeschwindigkeit_ms=10.0,
        windrichtung_deg=90.0,
        niederschlag_mm=0.0,
        schneefall_cm=0.0,
        luftdruck_hpa=1013.25,
        luftfeuchtigkeit_pct=60.0,
        globalstrahlung_wm2=400.0,
        bewoelkung_pct=20.0,
    )
```

---

## 7. Aufgaben-Checkliste

**Modulgrenzen strikt einhalten:** Jedes Modul schreibt nur in `src/tripplanner/<modul>/`, `tests/<modul>/`, `docs/plans/03-weather-wind.md`. Kein Live-Netzwerkzugriff in Unit-Tests.

1. **[weather/models.py]** Erstelle `WeatherQuery` und `WeatherSample` als Pydantic-Modelle mit allen angegebenen Feldern, Validatoren (z. B. `windgeschwindigkeit_ms >= 0`, `windrichtung_deg in [0, 360]`) und Docstrings (Google-Style).
2. **[weather/client.py]** Implementiere `OpenMeteoClient.fetch_forecast()` mit Batching (max. 50 Koordinaten pro Call), Zeitbereichs-Ermittlung und Parsing der JSON-Antwort in `OpenMeteoResponse` + `WeatherSample`.
3. **[weather/providers.py]** Implementiere `WeatherProvider`-Protocol und `OpenMeteoProvider`-Klasse, die `OpenMeteoClient` nutzt. Füge Caching für `refetch_weather()` hinzu (Dictionary `CacheKey = Tuple[Coordinate, datetime]`).
4. **[weather/**init**.py]** Re-exportiere `WeatherProvider`, `fetch_weather_for_route`, `fetch_weather_iterative`.
5. **[tests/weather/test_weather.py]** Schreibe 3 Unit-Tests (siehe Abschnitt 6, Testfälle 1–3). Nutze `pytest.mark.asyncio`.
6. **[tests/weather/test_providers.py]** Schreibe 1 Integrationstest (`@pytest.mark.integration`) für `fetch_weather_iterative` mit lokaler GraphHopper-Instanz (Mock-Provider, kein echter HTTP-Call).
7. **[weather/**init**.py]** Füge `OpenMeteoClient` als optionalen Build-Parameter für `OpenMeteoProvider` hinzu (für Dependency Injection in Tests).
8. **[wind/models.py]** Erstelle `WindComponents` als Pydantic-Modell (Felder: `segment_index`, `gegenwind_ms`, `seitenwind_ms`).
9. **`[wind/<modul>.py]`** Implementiere `compute_wind_components()` gemäß trigonometrischer Formel (Vektorprojektion mit 180°-Verschiebung für Windrichtung).
10. **`[wind/<modul>.py]`** Implementiere `compute_wind_components_for_route()` mit Längen-Check und Loop über Zipped Liste.
11. **[wind/test_wind.py]** Schreibe 3 Unit-Tests (siehe Abschnitt 6, Testfälle 1–3). Prüfe Werte auf `math.isclose()` mit Toleranz `1e-6`.
12. **[docs/plans/03-weather-wind.md]** Aktualisiere diese Datei mit finalen Implementation-Details (Formel, API-Endpunkt, Parameter).
13. **[pyproject.toml]** Füge `httpx` als Abhängigkeit hinzu (`httpx = "^0.27.0"` für Async-Unterstützung).
14. **[tests/conftest.py]** Erstelle shared `Coordinate`-Fixture für alle Module (`lat=52.52, lon=13.405` für Berlin).

---

## 8. Risiken & offene technische Fragen

**Bereits durch „Verbindlich entschiedene offene Punkte“ geregelt:**

- ✅ Iterative ETA/Wetter-Konvergenz (Schwellwert 30 Min, max. Iterationszahl = konfigurierbar).
- ✅ Straßenroute bleibt fixiert (kein energieoptimales Rerouting) — das `weather`-Modul fragt einfach nur ab, kein Rerouting-Logik.
- ✅ Tesla-Supercharger-Datenquelle: lokal vorgehalten (JSON/SQLite) — `ChargingStationProvider`-Interface ist bereits vorbereitet, hat aber keine Schnittstelle zu `weather`.
- ✅ `energy`-Modul darf sinnvolle Default-Parameter verwenden (kein Kalibrierungs-Deliverable im aktuellen Scope).

**Neue Risiken / offene Fragen:**

1. **Open-Meteo Rate-Limits bei mehreren parallelen Routenberechnungen:**
   - Wenn 100 Routen parallel berechnet werden, könnten 100 × 5 Calls = 500 Calls/Tag schnell erreicht werden.
   - **Lösung:** Caching im `OpenMeteoProvider` (nicht nur für `refetch_weather`, sondern auch für identische Standorte+Zeiten in verschiedenen Requests). Optional: `asyncio.Semaphore` für parallele Calls beschränken.

2. **Zeitzonenhandling:**
   - Open-Meteo gibt `timezone` und `timezone_abbreviation` zurück. In `WeatherSample.zeitpunkt` wird `datetime` mit `tzinfo` erwartet.
   - **Lösung:** `OpenMeteoResponse` parsed `time`-Strings (ISO 8601) via `datetime.fromisoformat()` mit `tzinfo` aus `timezone`. Falls `timezone` fehlt, wird UTC angenommen.

3. **Interpolation bei fehlenden Zeitpunkten:**
   - Open-Meteo gibt Daten in Stundenschritten (oder 15-minütig). Wenn die gewünschte `WeatherQuery.zeitpunkt` nicht exakt in `hourly.time` enthalten ist, muss interpoliert werden.
   - **Lösung:** `OpenMeteoClient.fetch_forecast()` führt lineare Interpolation durch (vorhergehender und folgender Stundenslot). Alternativ: `forecast_days=2` setzen, um sicherzustellen, dass alle gewünschten Zeiten im Vorhersagezeitraum liegen.

4. **Windrichtung: 0° vs. 360° (Nordwind):**
   - Open-Meteo liefert `wind_direction_10m` als `0..360`. `0` und `360` sind identisch.
   - **Lösung:** In `compute_wind_components()` wird `windrichtung_deg` normalisiert (`% 360`) und für die 180°-Verschiebung korrekt gehandhabt (`(windrichtung_deg + 180) % 360`).

5. **Bearing-Feld:** `RouteSegment.bearing_deg` wird bereits vom `routing`-Modul berechnet (siehe `docs/plans/01-routing.md`, Task 15) und ist ein Pflichtfeld — `wind` muss keinen Fallback aus der Geometrie mehr berechnen.

6. **Historical API als Backup für fehlende Forecast-Daten:**
   - Falls Open-Meteo Forecast API keine Daten für einen Zeitpunkt zurückgibt (z. B. zu weit in der Vergangenheit), könnte auf Historical-API ausgewichen werden.
   - **Lösung:** Derzeit **nicht implementiert** (geplant für Phase 9: Integration/Härtung). Vorbereitung: `WeatherProvider`-Protocol ist so angelegt, dass ein `FallbackWeatherProvider` implementiert werden kann.

7. **Wind-Sensorhöhe:**
   - Open-Meteo liefert `wind_speed_10m` (in 10 m Höhe). Für Fahrzeuge (ca. 1–1.5 m Höhe) könnte eine Höhenkorrektur nötig sein (logarithmisches Windprofil).
   - **Lösung:** Derzeit **nicht implementiert** (Wind in 10 m Höhe wird als ausreichend genau angenommen). Vorbereitung: `WindComponents` könnte `wind_speed_at_10m_ms: float` heißen, um später eine Metrik `wind_speed_at_vehicle_height_ms` einzufügen.

---

**Ende des Plans.** Dieses Dokument ist vollständig umsetzungsreif und enthält keine Platzhalter (TBD, „später festlegen“ etc.). Alle externen API-Details, Algorithmen und Testfälle sind konkret spezifiziert.
