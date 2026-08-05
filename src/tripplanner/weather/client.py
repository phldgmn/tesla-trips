"""Open-Meteo Client für Wetter-Abfragen."""

from collections.abc import Sequence
from datetime import datetime

import httpx

from tripplanner.weather.models import OpenMeteoResponse, WeatherQuery, WeatherSample

HOURLY_PARAMS = [
    "temperature_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "precipitation",
    "snowfall",
    "surface_pressure",
    "relative_humidity_2m",
    "shortwave_radiation",
    "cloud_cover",
]

_KMH_TO_MPS = 1000.0 / 3600.0


class OpenMeteoClient:
    """HTTP-Client für Open-Meteo Forecast API."""

    BASE_URL = "https://api.open-meteo.com/v1/forecast"
    TIMEOUT_S = 15.0

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        """Initialisiert den Client.

        Args:
            client: Optionaler httpx.AsyncClient. Wenn None, wird ein neuer Client erstellt.
        """
        self._client = client or httpx.AsyncClient(timeout=self.TIMEOUT_S)

    async def fetch_forecast(
        self,
        queries: Sequence[WeatherQuery],
    ) -> list[OpenMeteoResponse]:
        """Abruf von Wetterdaten für mehrere Standorte + Zeitpunkte.

        Args:
            queries: Liste von Wetterabfragen (Koordinate + Zeitpunkt).

        Returns:
            Liste von OpenMeteoResponse, in gleicher Reihenfolge wie queries.
            Bei fehlenden Daten für einen Punkt wird None zurückgegeben.
        """
        if not queries:
            return []

        # Gruppieren nach Koordinate (doppelte Standorte sparen API-Calls)
        coords: dict[tuple[float, float], list[tuple[int, WeatherQuery]]] = {}
        for idx, q in enumerate(queries):
            key = (q.koordinate[0], q.koordinate[1])
            coords.setdefault(key, []).append((idx, q))

        results: list[OpenMeteoResponse | None] = [None] * len(queries)

        for (lat, lon), entries in coords.items():
            # Zeitbereich: earliest bis latest Zeitpunkt für diesen Standort
            times = [q.zeitpunkt.isoformat() for _, q in entries]
            time_min = min(times)
            time_max = max(times)

            url = (
                f"{self.BASE_URL}?"
                f"latitude={lat}&longitude={lon}&"
                f"hourly={','.join(HOURLY_PARAMS)}&"
                f"start_date={time_min[:10]}&"
                f"end_date={time_max[:10]}"
            )

            resp = await self._client.get(url)
            resp.raise_for_status()
            data = OpenMeteoResponse(**resp.json())

            # Zeitindex lookup für jeden Query-Punkt
            time_to_idx = {t: i for i, t in enumerate(data.hourly["time"])}
            for idx, q in entries:
                time_idx = time_to_idx.get(q.zeitpunkt.isoformat()[:16])
                if time_idx is not None:
                    results[idx] = data

        return [r for r in results if r is not None]

    async def close(self) -> None:
        """Schließt den HTTP-Client."""
        await self._client.aclose()


def _extract_sample_from_response(
    response: OpenMeteoResponse,
    zeitpunkt: datetime,
) -> WeatherSample | None:
    """Extrahiert ein WeatherSample aus einer OpenMeteoResponse.

    Args:
        response: OpenMeteoResponse mit hourly-Daten.
        zeitpunkt: Gewünschter Zeitpunkt.

    Returns:
        WeatherSample oder None, wenn der Zeitpunkt nicht gefunden wird.
    """
    time_to_idx = {t: i for i, t in enumerate(response.hourly["time"])}
    iso_str = zeitpunkt.isoformat()
    # Open-Meteo verwendet "YYYY-MM-DDTHH:MM", Query verwendet "YYYY-MM-DDTHH:MM:SS"
    # Wir suchen nach Prefix-Match (erste 16 Zeichen)
    time_idx = None
    for t, idx in time_to_idx.items():
        if t == iso_str[:16]:  # "YYYY-MM-DDTHH:MM"
            time_idx = idx
            break

    if time_idx is None:
        return None

    hourly = response.hourly

    def get_value(key: str, default: float = 0.0) -> float:
        """Hilfsfunktion zum Extrahieren eines Werts mit Typkonvertierung."""
        value = hourly.get(key, [default] * len(response.hourly.get("time", [])))[time_idx]
        if value is None:
            return default
        return float(value)

    # Windgeschwindigkeit von km/h in m/s umrechnen
    wind_speed_kmh = get_value("wind_speed_10m", 0.0)
    wind_speed_ms = wind_speed_kmh * _KMH_TO_MPS

    return WeatherSample(
        koordinate=(response.latitude, response.longitude),
        zeitpunkt=zeitpunkt,
        temperatur_c=get_value("temperature_2m", 0.0),
        windgeschwindigkeit_ms=wind_speed_ms,
        windrichtung_deg=get_value("wind_direction_10m", 0.0),
        niederschlag_mm=get_value("precipitation", 0.0),
        schneefall_cm=get_value("snowfall", 0.0),
        luftdruck_hpa=get_value("surface_pressure", 1013.25),
        luftfeuchtigkeit_pct=get_value("relative_humidity_2m", 0.0),
        globalstrahlung_wm2=get_value("shortwave_radiation", 0.0),
        bewoelkung_pct=get_value("cloud_cover", 0.0),
    )
