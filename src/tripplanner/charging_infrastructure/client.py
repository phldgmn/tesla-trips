"""HTTP-Client für die supercharge.info REST-API und die Tesla Locations-API."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import quote

import httpx
from curl_cffi import AsyncSession


def _debug_log(log_path: Path | None, msg: str, label: str = "DEBUG") -> None:
    """Schreibt eine Debug-Zeile in die Logdatei (wenn konfiguriert).

    Args:
        log_path: Pfad zur Logdatei oder None (nichts tun)
        msg: Die Nachricht
        label: Label für die Zeile (z.B. "CURL", "HTTP", "JSON")
    """
    if log_path is None:
        return
    timestamp = datetime.now(UTC).isoformat(timespec="milliseconds")
    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] [{label}] {msg}\n")
    except OSError:
        pass


class SuperchargeInfoClient:
    """HTTP-Client für die supercharge.info REST-API.

    Die API ist öffentlich, benötigt keinen API-Key und blockiert keine
    einfachen HTTP-Clients (kein WAF). Dokumentierte Endpunkte:
    - /service/supercharge/allSites   -> vollstaendiger Datensatz
    - /service/supercharge/databaseInfo -> Aenderungs-Timestamp
    - /service/supercharge/allChanges  -> Delta-Aenderungen
    """

    BASE_URL: str = "https://supercharge.info/service/supercharge"

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        debug_log: Path | None = None,
    ) -> None:
        """Initialize the client.

        Args:
            client: Optional httpx.AsyncClient instance. If None, a new client
                with 30s timeout is created.
            debug_log: Optional file path for request/response debug logging.
        """
        if client is None:
            self._client: httpx.AsyncClient = httpx.AsyncClient(timeout=30.0)
            self._owns_client: bool = True
        else:
            self._client = client
            self._owns_client = False
        self._debug_log = debug_log

    async def _log_response(self, response: httpx.Response, label: str) -> None:
        """Loggt eine HTTP-Antwort fuer Debug-Zwecke."""
        if self._debug_log is None:
            return
        body = response.text[:2000]
        _debug_log(
            self._debug_log,
            f"[{label}] {response.request.method} "
            f"{response.url} -> {response.status_code}\n"
            f"  Response headers: {dict(response.headers)}\n"
            f"  Body ({len(response.content)} bytes): {body}",
            label="HTTP",
        )

    async def fetch_all_sites(self) -> list[dict[str, Any]]:
        """Fetch all supercharger sites.

        GET {BASE_URL}/allSites

        Returns:
            List of site dictionaries.

        Raises:
            httpx.HTTPStatusError: If response status is not 200.
        """
        response = await self._client.get(f"{self.BASE_URL}/allSites")
        await self._log_response(response, "allSites")
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]

    async def fetch_database_info(self) -> dict[str, Any]:
        """Fetch database information including last modification timestamp.

        GET {BASE_URL}/databaseInfo

        Returns:
            Dictionary with lastModified (ms timestamp) and lastModifiedString.
        """
        response = await self._client.get(f"{self.BASE_URL}/databaseInfo")
        await self._log_response(response, "databaseInfo")
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]

    async def fetch_all_changes(self) -> list[dict[str, Any]]:
        """Fetch all changes since last sync.

        GET {BASE_URL}/allChanges

        Returns:
            List of change dictionaries.
        """
        response = await self._client.get(f"{self.BASE_URL}/allChanges")
        await self._log_response(response, "allChanges")
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]

    async def close(self) -> None:
        """Close the underlying HTTP client if owned by this instance."""
        if self._owns_client:
            await self._client.aclose()


class TeslaLocationsClient:
    """HTTP-Client fuer die oeffentliche Tesla Locations-API via curl_cffi.

    Nutzt ``curl_cffi.AsyncSession`` mit JA3/TLS-Fingerprint-Impersonation
    (Chrome 150), um den Akamai WAF von tesla.com zu umgehen. Der
    ``impersonate``-Preset generiert automatisch die korrekten HTTP/2
    Header-Sequenz und den User-Agent — manuell gesetzte Header (wie die
    alten ``_CURL_HEADERS``) sind nicht mehr noetig, koennen aber zur
    Ueberschreibung verwendet werden.

    Ein ``AsyncSession``-Objekt wird pro Client-Instanz erzeugt und
    ueber alle Requests hinweg wiederverwendet, was Cookie-Jar-Tracking,
    TCP-Connection-Pooling und HTTP/2-Stream-Multiplexing aktiviert.

    Zwei Endpunkte:
    - fetch_locations            -> Liste aller Standorte (UUID, Slug, Typ, Koordinaten)
    - fetch_location_details     -> Detaildaten zu einem Standort (Slug-basiert)
    - fetch_pricing_html         -> Roh-HTML der oeffentlichen Standortseite

    Usage:
        client = TeslaLocationsClient()
        details = await client.fetch_all_supercharger_details("DE")
        await client.close()
    """

    BASE_URL: str = "https://www.tesla.com/api/findus"

    PRICING_BASE_URL: str = "https://www.tesla.com/findus/location/supercharger"
    """Oeffentliche Standort-Detailseite (Next.js, kein JSON-API-Endpunkt wie
    `BASE_URL`). Anders als `get-location-details` (siehe `Tesla-Supercharger-
    API.md`) enthaelt nur diese Seite die kWh-Preise, eingebettet in einem
    `<script id="__NEXT_DATA__">`-JSON-Blob - siehe `pricing.parse_pricing_tiers`
    fuer das Parsing und `docs/Tesla-Supercharger-Detail-Scraping.md` fuer die
    Herkunft dieser Struktur (reverse-engineered vom Referenz-Tool `tesla-
    pricing`)."""

    _IMPERSONATE: str = "chrome150"
    """curl_cffi Browser-Fingerprint-Preset, das dem macOS-System-curl mit
    SecureTransport entspricht (TLS JA3/HTTP2 Fingerabdruck)."""

    _BASE_HEADERS: ClassVar[dict[str, str]] = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "referer": (
            "https://www.tesla.com/de_de/findus?"
            "bounds=61.019610081973084%2C-61.13933008750001%2C"
            "6.759859256346627%2C-151.9303457125"
        ),
    }
    """Zusaetzliche Header, die der Chrome 150-Preset nicht setzt, aber die
    Tesla API erwartet (z.B. ``accept`` und ``accept-language``). Der
    ``sec-ch-ua-*``, ``user-agent`` und ``priority`` Header werden vom
    Impersonation-Preset automatisch injiziert."""

    class CurlError(Exception):
        """HTTP-Request fehlgeschlagen."""

    def __init__(
        self,
        rate_limit_delay_s: float = 0.5,
        debug_log: Path | None = None,
        client: AsyncSession | None = None,
    ) -> None:
        """Initialize the client.

        Args:
            rate_limit_delay_s: Delay in seconds between detail requests.
            debug_log: Optional file path for debug logging (requests,
                responses, errors).
            client: Optional pre-configured AsyncSession (for tests). When
                omitted, an owned session is created and closed by close().
        """
        self._delay = rate_limit_delay_s
        self._debug_log = debug_log
        if client is None:
            self._client: AsyncSession = AsyncSession(
                impersonate=self._IMPERSONATE,
                timeout=30.0,
                headers=dict(self._BASE_HEADERS),
            )
            self._owns_client: bool = True
        else:
            self._client = client
            self._owns_client = False

    async def _log_request(self, method: str, url: str) -> None:
        """Loggt eine Anfrage fuer Debug-Zwecke."""
        if self._debug_log is None:
            return
        _debug_log(self._debug_log, f"{method} {url}", label="HTTP")

    async def _log_response(self, response: Any, body_preview: str, label: str = "HTTP") -> None:
        """Loggt eine HTTP-Antwort fuer Debug-Zwecke."""
        if self._debug_log is None:
            return
        _debug_log(
            self._debug_log,
            f"{label} {response.request.method} "
            f"{response.request.url} -> {response.status_code}\n"
            f"  Body ({len(response.content)} bytes): {body_preview}",
            label,
        )

    async def _fetch(self, url: str) -> str:
        """Fuehrt GET aus und liefert den Response-Body als Text.

        Wirft ``CurlError`` bei HTTP-Fehlern (403, 429, andere Nicht-200)
        oder leeren Antworten. Network-Fehler (DNS, ConnectionRefused,
        Timeout) werden als ``CurlError`` mit der Originalnachricht weitergegeben.

        Args:
            url: Vollstaendige URL mit Query-Parametern

        Returns:
            Response-Body als Text

        Raises:
            CurlError: Bei HTTP-Fehlern, leeren Antworten oder Netzwerkfehlern
        """
        try:
            response = await self._client.get(url)
        except Exception as e:
            raise self.CurlError(f"request failed: {e}") from e

        body = response.text
        await self._log_response(response, body[:2000])

        if not body.strip():
            raise self.CurlError("empty response")

        if response.status_code == HTTPStatus.FORBIDDEN:
            raise self.CurlError("Tesla API: 403 Access Denied (mglw. rate-limited)")
        if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
            raise self.CurlError("Tesla API: 429 Too Many Requests (Rate-Limit)")
        if response.status_code != HTTPStatus.OK:
            raise self.CurlError(f"Tesla API: HTTP {response.status_code}")

        return body

    async def _fetch_json(self, url: str) -> dict[str, Any]:
        """Fuehrt GET aus und parst JSON-Antwort (siehe ``_fetch``).

        Args:
            url: Vollstaendige URL mit Query-Parametern

        Returns:
            Geparstes JSON-Dict

        Raises:
            CurlError: Bei HTTP-Fehlern, leeren Antworten oder ungültigem JSON
        """
        body = await self._fetch(url)
        try:
            parsed = json.loads(body)
            _debug_log(
                self._debug_log,
                f"JSON parsed: {type(parsed).__name__}, "
                f"top keys: {list(parsed.keys()) if isinstance(parsed, dict) else 'N/A'}",
                label="JSON",
            )
            return parsed  # type: ignore[no-any-return]
        except json.JSONDecodeError as e:
            _debug_log(
                self._debug_log,
                f"JSON parse error: {e}\nbody preview: {body[:300]}",
                label="ERROR",
            )
            raise self.CurlError(f"invalid JSON: {e}"[:200]) from e

    async def close(self) -> None:
        """Close the underlying curl_cffi session if owned by this instance."""
        if self._owns_client:
            await self._client.close()
            self._owns_client = False

    async def fetch_locations(
        self,
        country: str = "DE",
        view: str = "map",
    ) -> list[dict[str, Any]]:
        """Fetch all Tesla locations for a given country.

        Args:
            country: ISO-2 country code (DE, DK, SE, etc.)
            view: Map view parameter (default "map")

        Returns:
            List of location dicts.

        Raises:
            CurlError: Bei curl-Fehlern oder WAF-Block
        """
        url = f"{self.BASE_URL}/get-locations?country={country}&view={view}"
        data = await self._fetch_json(url)
        return data.get("data", {}).get("data", [])  # type: ignore[no-any-return]

    async def fetch_location_details(
        self,
        slug: str,
        in_hk_mo_tw: bool = False,
        locale: str = "de_DE",
    ) -> dict[str, Any]:
        """Fetch full details for a single Tesla location.

        Args:
            slug: The location_url_slug from fetch_locations().
            in_hk_mo_tw: Pass through the inHkMoTw value from the list entry.
            locale: Locale string (default "de_DE").

        Returns:
            Full detail dict (kann leer sein wenn der slug nicht aufloesbar ist).

        Raises:
            CurlError: Bei curl-Fehlern oder WAF-Block
        """
        encoded_slug = quote(slug, safe="")
        url = (
            f"{self.BASE_URL}/get-location-details"
            f"?locationSlug={encoded_slug}&functionTypes=party"
            f"&locale={locale}&isInHkMoTw={str(in_hk_mo_tw).lower()}"
        )
        try:
            data = await self._fetch_json(url)
            return data.get("data", {})  # type: ignore[no-any-return]
        except (json.JSONDecodeError, self.CurlError):
            return {}

    async def fetch_all_supercharger_details(
        self,
        country: str = "DE",
        delay_s: float | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch details for all supercharger locations in a country.

        Flow:
        1. fetch_locations(country)
        2. Filter to "supercharger" entries (exclude inCN)
        3. fetch_location_details() for each, with rate limiting

        Schlaege fehlgeschlagene Detail-Requests werden uebersprungen.

        Args:
            country: ISO-2 country code.
            delay_s: Override the default rate limit delay.

        Returns:
            List of detail dicts for supercharger locations only.
        """
        locations = await self.fetch_locations(country)
        superchargers = [
            loc
            for loc in locations
            if "supercharger" in loc.get("location_type", []) and not loc.get("inCN", False)
        ]

        effective_delay = delay_s if delay_s is not None else self._delay
        details: list[dict[str, Any]] = []
        for loc in superchargers:
            slug: str = loc.get("location_url_slug", "")
            if not slug:
                continue
            try:
                detail = await self.fetch_location_details(
                    slug,
                    in_hk_mo_tw=loc.get("inHkMoTw", False),
                )
                if not detail:
                    continue  # empty response -> skip
                detail["_uuid"] = loc.get("uuid", "")
                detail["_slug"] = slug
                details.append(detail)
            except Exception:
                continue  # skip failed detail requests
            if effective_delay > 0:
                await asyncio.sleep(effective_delay)

        return details

    async def fetch_pricing_html(self, slug: str) -> str:
        """Fetches the raw HTML of a Supercharger's public detail page.

        Args:
            slug: Der location_url_slug aus fetch_locations() /
                tesla_location_id aus der lokalen DB.

        Returns:
            Rohes HTML des Antwort-Bodys (siehe `pricing.parse_pricing_tiers`
            fuer die Extraktion der `chargerPricing`-Daten daraus).

        Raises:
            CurlError: Bei curl-Fehlern, WAF-Block (403/429) oder anderen
                Nicht-200-Antworten.
        """
        encoded_slug = quote(slug, safe="")
        url = f"{self.PRICING_BASE_URL}/{encoded_slug}"
        return await self._fetch(url)
