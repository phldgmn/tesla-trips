"""HTTP-Client für die supercharge.info REST-API und die Tesla Locations-API."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import importlib
import json
import threading
import time
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any, ClassVar, Protocol, runtime_checkable
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


class CurlError(Exception):
    """HTTP-Request fehlgeschlagen (WAF-Block, Rate-Limit, Netzwerkfehler).

    Gemeinsamer Fehlertyp fuer alle Tesla-Clients (curl_cffi und nodriver),
    damit Aufrufer unabhaengig vom gewaehlten Transport-Mechanismus denselben
    Exception-Typ abfangen koennen.
    """


@runtime_checkable
class TeslaClient(Protocol):
    """Protokoll fuer Tesla-API-Clients (curl_cffi- und nodriver-Backend).

    Beide Implementierungen (`TeslaLocationsClient` und `NodriverTeslaClient`)
    bieten dieselbe Schnittstelle fuer Standortliste, Detaildaten und
    Preis-HTML. `runtime_checkable` erlaubt ``isinstance``-Pruefungen.
    """

    async def fetch_locations(
        self, country: str = "DE", view: str = "map"
    ) -> list[dict[str, Any]]: ...

    async def fetch_location_details(
        self,
        slug: str,
        in_hk_mo_tw: bool = False,
        locale: str = "de_DE",
    ) -> dict[str, Any]: ...

    async def fetch_all_supercharger_details(
        self, country: str = "DE", delay_s: float | None = None
    ) -> list[dict[str, Any]]: ...

    async def fetch_pricing_html(self, slug: str) -> str: ...

    async def close(self) -> None: ...


def create_tesla_client(
    transport: str = "nodriver",
    *,
    rate_limit_delay_s: float = 0.5,
    debug_log: Path | None = None,
) -> TeslaClient:
    """Erzeugt einen Tesla-API-Client mit dem gewuenschten Transport.

    Default ist ``nodriver`` (echter Chromium-Browser via CDP), da dieser den
    Akamai-WAF von tesla.com zuverlaessiger umgeht als curl_cffi. ``curl_cffi``
    bleibt als leichtgewichtigere Alternative verfuegbar (JA3/TLS-Fingerprint-
    Impersonation). Bei nicht installiertem ``nodriver`` wird automatisch auf
    curl_cffi zurueckgefallen.

    Args:
        transport: ``"nodriver"`` (Default) oder ``"curl_cffi"``.
        rate_limit_delay_s: Verzoegerung zwischen Detail-Requests.
        debug_log: Optionaler Dateipfad fuer Request/Response-Debug-Log.

    Returns:
        Ein ``TeslaClient``-kompatibles Objekt (nodriver oder curl_cffi).

    Raises:
        ValueError: Bei unbekanntem ``transport``-Wert.
    """
    if transport == "nodriver":
        try:
            importlib.import_module("nodriver")
        except ImportError:
            # nodriver nicht installiert -> curl_cffi als Fallback.
            transport = "curl_cffi"

    if transport == "nodriver":
        return NodriverTeslaClient(
            rate_limit_delay_s=rate_limit_delay_s,
            debug_log=debug_log,
        )
    if transport == "curl_cffi":
        return TeslaLocationsClient(
            rate_limit_delay_s=rate_limit_delay_s,
            debug_log=debug_log,
        )
    raise ValueError(
        f"Unbekannter Tesla-Client-Transport '{transport}'. Erlaubt: 'nodriver', 'curl_cffi'."
    )


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
        #        "sec-fetch-dest": "empty",
        #        "sec-fetch-mode": "cors",
        #        "sec-fetch-site": "same-origin",
        #        "referer": (
        #            "https://www.tesla.com/de_de/findus?"
        #            "bounds=61.019610081973084%2C-61.13933008750001%2C"
        #            "6.759859256346627%2C-151.9303457125"
        #        ),
    }
    """Zusaetzliche Header, die der Chrome 150-Preset nicht setzt, aber die
    Tesla API erwartet (z.B. ``accept`` und ``accept-language``). Der
    ``sec-ch-ua-*``, ``user-agent`` und ``priority`` Header werden vom
    Impersonation-Preset automatisch injiziert."""

    CurlError = CurlError
    """Alias auf den modulweiten ``CurlError`` (siehe oben)."""

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


class NodriverBrowserFetcher:
    """Verwaltet eine nodriver-Chromium-Instanz in einem Hintergrund-Thread.

    ``nodriver`` steuert einen echten Chromium-Browser via CDP und bringt eine
    eigene asyncio-Event-Loop mit, die nicht mit ``asyncio.run()`` oder der
    laufenden FastAPI-Loop kompatibel ist. Dieser Wrapper startet daher einen
    Daemon-Thread mit eigener Event-Loop und reicht Anfragen aus dem
    aufrufenden Thread via ``asyncio.run_coroutine_threadsafe`` hinein.

    Der Browser wird pro Fetcher-Instanz nur einmal gestartet und bei
    ``close()`` wieder beendet (vergleichbar mit der Session-Wiederverwendung
    des curl_cffi-Backends).
    """

    _NAVIGATION_TIMEOUT_S: float = 15.0

    def _thread_main(self) -> None:
        """Laesst die eigene Event-Loop des Daemon-Threads laufen."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            # Sicherungsnetz: Falls _shutdown_browser() nicht (vollstaendig)
            # laufen durfte, hier noch alle restlichen Tasks (v. a. der
            # CDP-Listener) abbrechen, bevor die Loop geschlossen wird.
            # Suspendierte Generatoren ueberlebender Tasks wuerden sonst
            # beim Python-Interpreter-Abschluss zerrissen und den
            # Prozess-Exit blockieren (Hang in gc_collect_main).
            loop = self._loop
            tasks = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task()]
            if tasks:
                for task in tasks:
                    task.cancel()
                with contextlib.suppress(Exception):
                    loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            loop.close()

    def __init__(self, headless: bool = True) -> None:
        """Initialisiert den Fetcher (startet den Browser noch nicht).

        Args:
            headless: Ob Chromium headless laufen soll (Default True).
        """
        self._headless = headless
        self._loop: asyncio.AbstractEventLoop | None = None  # type: ignore[assignment, no-redef]
        self._thread: threading.Thread | None = None
        self._browser: Any | None = None
        self._closed = False

    def _ensure_started(self) -> asyncio.AbstractEventLoop:
        """Startet den Daemon-Thread (idempotent) und liefert dessen Loop."""
        if self._thread is None or not self._thread.is_alive():
            self._loop = None  # type: ignore[assignment]
            self._thread = threading.Thread(
                target=self._thread_main,
                daemon=True,
                name="nodriver-browser",
            )
            self._thread.start()
        # Warten bis die Loop bereit ist.
        for _ in range(100):
            if self._loop is not None:
                break
            time.sleep(0.05)
        if self._loop is None:
            raise CurlError("nodriver-Browser-Thread konnte nicht gestartet werden")
        return self._loop

    def _submit(self, coro: Any) -> concurrent.futures.Future[Any]:
        loop = self._ensure_started()
        return asyncio.run_coroutine_threadsafe(coro, loop)

    async def _ensure_browser(self) -> Any:
        if self._browser is None:
            import nodriver as uc

            self._browser = await uc.start(headless=self._headless)  # type: ignore[attr-defined]
        return self._browser

    async def _fetch_async(self, url: str) -> tuple[int, str]:
        """Fuehrt die eigentliche CDP-Navigation in der nodriver-Loop aus.

        Der Statuscode stammt vom CDP ``Network.responseReceived``-Event der
        Haupt-Dokument-Antwort. Der Rohtext wird per
        ``Network.getResponseBody`` geholt; da Chrome den Body des
        Hauptdokuments dort in der Regel nicht mehr vorhaelt ("No resource
        with given identifier"), wird als Fallback je nach MIME-Typ das
        gerenderte JSON-Text (``document.body.innerText`` - exakt fuer in
        ``<pre>`` gerenderte JSON-Antworten) bzw. das rohe HTML
        (``get_content()``, inkl. ``__NEXT_DATA__``-Script-Inhalten) genutzt.
        """
        from nodriver import cdp

        browser = await self._ensure_browser()
        tab = browser.main_tab

        # Status-, MIME- und Request-ID der Haupt-Dokument-Antwort via
        # Network-Events einsammeln, BEVOR navigiert wird (sonst verpassen
        # wir die Response).
        captured: dict[str, Any] = {}

        def on_response(event: Any) -> None:
            """Capture the main-document response from CDP Network events."""
            if "status" in captured:
                return
            try:
                # type_ is a ResourceTypes enum (e.g. ResourceTypes.DOCUMENT),
                # not a plain string, so compare via .value when available.
                type_val = getattr(event, "type_", None)
                if type_val is not None:
                    type_str = getattr(type_val, "value", type_val)
                    if type_str != "Document":
                        return
                req_id = getattr(event, "request_id", None)
                resp = getattr(event, "response", None)
                code = getattr(resp, "status", None) if resp is not None else None
                if req_id is not None and code is not None:
                    captured["request_id"] = req_id
                    captured["status"] = int(code)
                    captured["mime_type"] = str(getattr(resp, "mime_type", "") or "")
            except Exception:
                pass

        await tab.send(cdp.network.enable())
        tab.add_handler(cdp.network.ResponseReceived, on_response)
        try:
            await tab.get(url)
            await tab  # kurzes Settle (await -> wait(0.5))
            deadline = time.monotonic() + self._NAVIGATION_TIMEOUT_S
            while "status" not in captured and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
        finally:
            tab.remove_handler(cdp.network.ResponseReceived, on_response)

        if "status" not in captured:
            raise CurlError(f"nodriver: keine HTTP-Antwort fuer {url} empfangen")

        status: int = captured["status"]
        request_id = captured.get("request_id")
        body = await self._extract_body(tab, captured, request_id)
        return status, body

    async def _extract_body(
        self,
        tab: Any,
        captured: dict[str, Any],
        request_id: Any,
    ) -> str:
        """Holt den Response-Body via CDP, mit MIME-abhaengigem Fallback.

        Zunaechst ``Network.getResponseBody``; da Chrome den Body des
        Hauptdokuments dort meist nicht mehr vorhaelt ("No resource with
        given identifier"), wird je nach MIME-Typ der Rohtext aus dem
        Dokument abgeleitet: JSON rendert Chrome in <pre>, dort ist
        ``innerText`` der exakte JSON-Text; bei HTML liefert
        ``get_content()`` das rohe HTML inkl. der Script-Inhalte
        (``__NEXT_DATA__``), die ``innerText`` verlieren wuerde.
        """
        import base64

        from nodriver import cdp

        body = ""
        if request_id is not None:
            try:
                raw_body, base64_encoded = await tab.send(
                    cdp.network.get_response_body(request_id=request_id)
                )
                if base64_encoded:
                    body = base64.b64decode(raw_body).decode("utf-8", errors="replace")
                elif raw_body:
                    body = raw_body
            except Exception:
                body = ""
        if not body:
            mime = str(captured.get("mime_type", "")).lower()
            if "json" in mime:
                inner = await tab.evaluate(
                    "document.body ? document.body.innerText : ''",
                    return_by_value=True,
                )
                body = inner if isinstance(inner, str) else ""
            else:
                body = await tab.get_content() or ""
        return body

    def fetch(self, url: str) -> tuple[int, str]:
        """Holt eine URL und liefert (Statuscode, Rohtext) synchron.

        Blockiert den aufrufenden Thread bis zur Antwort. Wirft ``CurlError``
        bei Browser-/Netzwerk-Fehlern.
        """
        if self._closed:
            raise CurlError("nodriver-Fetcher wurde bereits geschlossen")
        try:
            future = self._submit(self._fetch_async(url))
            return future.result(timeout=self._NAVIGATION_TIMEOUT_S + 15.0)  # type: ignore[no-any-return]
        except Exception as e:
            raise CurlError(f"nodriver request failed: {e}") from e

    async def _shutdown_browser(self) -> None:
        """Beendet den Browser auf der Loop-Thread.

        Reihenfolge:

        1. Chromium-Subprozess terminieren und deterministisch abwarten
           (Sonst haengt ``aclose()`` beim ``wait_closed()`` einer
           halboffenen CDP-Verbindung, weil der Peer nicht mehr
           antwortet).
        2. ``aclose()``: schliesst die CDP-Verbindung und beendet deren
           Listener-Task.
        3. Alle uebrigen Loop-Tasks abbrechen und abwarten. Damit
           enthaelt die Loop beim ``loop.close()`` keine ausstehenden
           Koroutinen mehr. Wichtig, weil sonst deren suspendierte
           Generatoren spaeter beim Python-Interpreter-Abschluss
           (``gc_collect_main``) zerrissen werden - das haengt den
           Prozess-Exit (z. B. unter pytest) minutenlang auf.
        """
        browser = self._browser
        self._browser = None
        if browser is None:
            return
        process = getattr(browser, "_process", None)
        await self._reap_chromium(process)
        with contextlib.suppress(ImportError):
            from nodriver.core import util as _nd_util

            _nd_util.__registered__instances__.discard(browser)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(browser.aclose(), timeout=5.0)
        loop = asyncio.get_running_loop()
        tasks = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    async def _reap_chromium(process: Any | None) -> None:
        """Beendet einen Chromium-Subprozess und wartet auf seinen Tod.

        ``browser._process`` ist ein ``asyncio.subprocess.Process``.
        ``terminate()`` (SIGTERM) reicht nicht aus, wenn der Prozess
        nicht auf das Signal reagiert - dann wird eskaliert. Ein
        hängenbleibender Chromium-Prozess hält die CDP-Verbindung offen,
        wodurch ``aclose()`` beim ``wait_closed()`` hängen könnte.
        """
        if process is None or getattr(process, "returncode", None) is not None:
            return
        try:
            process.terminate()
        except Exception:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except Exception:
            with contextlib.suppress(Exception):
                process.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(process.wait(), timeout=2.0)

    @staticmethod
    def _kill_process(process: Any | None) -> None:
        """Beendet einen (asyncio-)Subprozess synchron und deterministisch.

        Fallback fuer den Fall, dass die Browser-Loop nicht mehr erreichbar
        ist (z. B. Thread gestorben). Terminiert den Prozess mit SIGTERM,
        wartet kurz und eskaliert auf SIGKILL.
        """
        if process is None:
            return
        try:
            if getattr(process, "returncode", None) is None:
                process.terminate()
        except Exception:
            return
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if getattr(process, "returncode", None) is not None:
                return
            time.sleep(0.05)
        with contextlib.suppress(Exception):
            process.kill()

    def close(self) -> None:
        """Beendet den Browser und den Hintergrund-Thread."""
        if self._closed:
            return
        self._closed = True
        if self._browser is not None and self._loop is not None:
            try:
                future = asyncio.run_coroutine_threadsafe(self._shutdown_browser(), self._loop)
                future.result(timeout=15.0)
            except Exception:
                # Loop-Thread nicht erreichbar (gestorben/Loop zu) ->
                # deterministisch den Subprozess beenden und aus der
                # nodriver-atexit-Liste nehmen. Browser.stop() wuerde
                # hier einen unkontrollierbaren aclose()-Task auf der
                # toten Loop erzeugen (leakt).
                process = getattr(self._browser, "_process", None)
                self._kill_process(process)
                try:
                    from nodriver.core import util as _nd_util

                    with contextlib.suppress(Exception):
                        _nd_util.__registered__instances__.discard(self._browser)
                except ImportError:
                    pass
                self._browser = None
        if self._loop is not None:
            with contextlib.suppress(RuntimeError):
                self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None


class NodriverTeslaClient:
    """HTTP-Client fuer die oeffentliche Tesla Locations-API via nodriver.

    Nutzt einen echten Chromium-Browser (nodriver, CDP), um den Akamai-WAF von
    tesla.com zu umgehen. Anders als ``TeslaLocationsClient`` (curl_cffi mit
    TLS-Fingerprint-Impersonation) rendert er die Seiten wie ein echter
    Browser und umgeht damit auch strengere Absicherungen - dafuer pro Request
    deutlich langsamer und ressourcenintensiver.

    Schnittstellen-kompatibel zu ``TeslaLocationsClient`` (siehe
    ``TeslaClient``-Protokoll): dieselben Methoden fuer Standortliste,
    Detaildaten und Preis-HTML sowie derselbe ``CurlError``-Typ.
    """

    BASE_URL: str = "https://www.tesla.com/api/findus"

    PRICING_BASE_URL: str = "https://www.tesla.com/findus/location/supercharger"
    """Oeffentliche Standort-Detailseite (einzige Seite mit kWh-Preisen)."""

    CurlError = CurlError
    """Alias auf den modulweiten ``CurlError`` (siehe oben)."""

    def __init__(
        self,
        rate_limit_delay_s: float = 0.5,
        debug_log: Path | None = None,
        fetcher: NodriverBrowserFetcher | None = None,
    ) -> None:
        """Initialize the client.

        Args:
            rate_limit_delay_s: Delay in seconds between detail requests.
            debug_log: Optional file path for debug logging (requests,
                responses, errors).
            fetcher: Optionaler vorab konfigurierter Fetcher (fuer Tests).
                Wenn None, wird ein eigener Fetcher erzeugt und von close()
                beendet.
        """
        self._delay = rate_limit_delay_s
        self._debug_log = debug_log
        if fetcher is None:
            self._fetcher = NodriverBrowserFetcher()
            self._owns_fetcher = True
        else:
            self._fetcher = fetcher
            self._owns_fetcher = False

    async def _log_request(self, method: str, url: str) -> None:
        """Loggt eine Anfrage fuer Debug-Zwecke."""
        if self._debug_log is None:
            return
        _debug_log(self._debug_log, f"{method} {url}", label="NODRIVER")

    async def _fetch(self, url: str) -> str:
        """Fuehrt GET aus und liefert den Response-Body als Text.

        Wirft ``CurlError`` bei HTTP-Fehlern (403, 429, andere Nicht-200)
        oder leeren Antworten.

        Args:
            url: Vollstaendige URL mit Query-Parametern

        Returns:
            Response-Body als Text

        Raises:
            CurlError: Bei HTTP-Fehlern, leeren Antworten oder Netzwerkfehlern
        """
        await self._log_request("GET", url)
        status, body = await asyncio.to_thread(self._fetcher.fetch, url)

        if self._debug_log is not None:
            _debug_log(
                self._debug_log,
                f"NODRIVER GET {url} -> {status}\n  Body ({len(body)} bytes): {body[:2000]}",
                label="NODRIVER",
            )

        if not body.strip():
            raise CurlError("empty response")
        if status == HTTPStatus.FORBIDDEN:
            raise CurlError("Tesla API: 403 Access Denied (mglw. rate-limited)")
        if status == HTTPStatus.TOO_MANY_REQUESTS:
            raise CurlError("Tesla API: 429 Too Many Requests (Rate-Limit)")
        if status != HTTPStatus.OK:
            raise CurlError(f"Tesla API: HTTP {status}")

        return body

    async def _fetch_json(self, url: str) -> dict[str, Any]:
        """Fuehrt GET aus und parst JSON-Antwort (siehe ``_fetch``)."""
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
            raise CurlError(f"invalid JSON: {e}"[:200]) from e

    async def close(self) -> None:
        """Beendet den zugrunde liegenden nodriver-Fetcher (wenn owned)."""
        if self._owns_fetcher:
            await asyncio.to_thread(self._fetcher.close)

    async def fetch_locations(
        self,
        country: str = "DE",
        view: str = "map",
    ) -> list[dict[str, Any]]:
        """Fetch all Tesla locations for a given country."""
        url = f"{self.BASE_URL}/get-locations?country={country}&view={view}"
        data = await self._fetch_json(url)
        return data.get("data", {}).get("data", [])  # type: ignore[no-any-return]

    async def fetch_location_details(
        self,
        slug: str,
        in_hk_mo_tw: bool = False,
        locale: str = "de_DE",
    ) -> dict[str, Any]:
        """Fetch full details for a single Tesla location."""
        encoded_slug = quote(slug, safe="")
        url = (
            f"{self.BASE_URL}/get-location-details"
            f"?locationSlug={encoded_slug}&functionTypes=party"
            f"&locale={locale}&isInHkMoTw={str(in_hk_mo_tw).lower()}"
        )
        try:
            data = await self._fetch_json(url)
            return data.get("data", {})  # type: ignore[no-any-return]
        except (json.JSONDecodeError, CurlError):
            return {}

    async def fetch_all_supercharger_details(
        self,
        country: str = "DE",
        delay_s: float | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch details for all supercharger locations in a country."""
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
        """Fetches the raw HTML of a Supercharger's public detail page."""
        encoded_slug = quote(slug, safe="")
        url = f"{self.PRICING_BASE_URL}/{encoded_slug}"
        return await self._fetch(url)
