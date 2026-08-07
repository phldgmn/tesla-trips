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
    """HTTP-Client fuer die oeffentliche Tesla Locations-API via System-curl.

    Ruft die Tesla API ueber das systemeigene ``curl``-Binary auf (via
    subprocess), weil Python-HTTP-Clients (httpx, curl_cffi) von Akamai WAF
    anhand des TLS-Fingerprints als Bot erkannt werden. Der macOS-System-curl
    verwendet SecureTransport und wird wie ein echter Browser behandelt.

    Zwei Endpunkte:
    - get-locations          -> Liste aller Standorte (UUID, Slug, Typ, Koordinaten)
    - get-location-details   -> Detaildaten zu einem Standort (Slug-basiert)

    Usage:
        client = TeslaLocationsClient()
        details = await client.fetch_all_supercharger_details("DE")
    """

    BASE_URL: str = "https://www.tesla.com/api/findus"

    # Exakte Header von der funktionierenden curl-Kommandozeile
    _CURL_HEADERS: ClassVar[list[str]] = [
        "-H",
        "accept: application/json, text/plain, */*",
        "-H",
        "accept-language: de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
        "-H",
        "priority: u=1, i",
        "-H",
        'sec-ch-ua: "Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"',
        "-H",
        "sec-ch-ua-mobile: ?0",
        "-H",
        'sec-ch-ua-platform: "macOS"',
        "-H",
        "sec-fetch-dest: empty",
        "-H",
        "sec-fetch-mode: cors",
        "-H",
        "sec-fetch-site: same-origin",
        "-H",
        (
            "referer: https://www.tesla.com/de_de/findus?"
            "bounds=61.019610081973084%2C-61.13933008750001%2C"
            "6.759859256346627%2C-151.9303457125"
        ),
        "-H",
        (
            "user-agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/150.0.0.0 Safari/537.36"
        ),
    ]

    _CURL: str = "/usr/bin/curl"

    class CurlError(Exception):
        """System-curl Aufruf fehlgeschlagen."""

    def __init__(
        self,
        rate_limit_delay_s: float = 0.5,
        debug_log: Path | None = None,
    ) -> None:
        """Initialize the client.

        Args:
            rate_limit_delay_s: Delay in seconds between detail requests.
            debug_log: Optional file path for debug logging (curl commands,
                responses, errors).
        """
        self._delay = rate_limit_delay_s
        self._debug_log = debug_log

    async def _curl_json(
        self,
        url: str,
    ) -> dict[str, Any]:
        """Fuehrt curl aus und parst JSON-Antwort.

        Erfasst den HTTP-Statuscode ueber '-w' und prueft ihn vor
        dem JSON-Parsing, um HTML-FehlerSeiten (403, 429) zu erkennen.

        Args:
            url: Vollstaendige URL mit Query-Parametern

        Returns:
            Geparstes JSON-Dict

        Raises:
            CurlError: Bei curl-Fehlern, leeren Antworten oder HTTP-Fehlern
        """
        cmd = [
            self._CURL,
            "-s",
            "-w",
            "\n%{http_code}",
            *self._CURL_HEADERS,
            url,
        ]

        _debug_log(self._debug_log, f"curl {' '.join(cmd)}", label="CURL")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        raw = stdout.decode()
        err_output = stderr.decode().strip()

        _debug_log(
            self._debug_log,
            f"exit={proc.returncode} stdout={len(raw)}B stderr={err_output or '(none)'}",
            label="CURL",
        )

        if proc.returncode != 0:
            msg = err_output or "unknown error"
            _debug_log(self._debug_log, f"curl error: {msg}", label="ERROR")
            raise self.CurlError(f"curl exit {proc.returncode}: {msg}")

        raw_stripped: str = raw.strip()
        if not raw_stripped:
            _debug_log(self._debug_log, "empty response", label="ERROR")
            raise self.CurlError("empty response")

        # Letzte Zeile ist der HTTP-Statuscode (von -w)
        *body_lines, status_str = raw_stripped.rsplit("\n", 1)
        body = "\n".join(body_lines)
        try:
            status_code = int(status_str.strip())
        except (ValueError, TypeError):
            status_code = 0

        _debug_log(
            self._debug_log,
            f"HTTP {status_code} body={len(body)}B",
            label="CURL",
        )

        if status_code == HTTPStatus.FORBIDDEN:
            _debug_log(self._debug_log, f"403 body: {body[:500]}", label="ERROR")
            raise self.CurlError("Tesla API: 403 Access Denied (mglw. rate-limited)")
        if status_code == HTTPStatus.TOO_MANY_REQUESTS:
            _debug_log(self._debug_log, f"429 body: {body[:500]}", label="ERROR")
            raise self.CurlError("Tesla API: 429 Too Many Requests (Rate-Limit)")
        if status_code != HTTPStatus.OK:
            _debug_log(
                self._debug_log,
                f"HTTP {status_code} body: {body[:500]}",
                label="ERROR",
            )
            raise self.CurlError(f"Tesla API: HTTP {status_code}")

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
        data = await self._curl_json(url)
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
            data = await self._curl_json(url)
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
