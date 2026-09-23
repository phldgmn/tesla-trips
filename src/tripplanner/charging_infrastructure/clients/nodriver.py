"""HTTP-Client fuer die oeffentliche Tesla Locations-API via nodriver."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import quote

from .common import (
    DEBUG_BODY_PREVIEW_CHARS,
    WAF_RETRY_MAX_ATTEMPTS,
    CurlError,
    _debug_log,
    is_waf_block,
    waf_retry_delay_s,
)

_logger = logging.getLogger(__name__)

_T = TypeVar("_T")


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
            try:
                import nodriver as uc
            except ImportError as exc:
                raise ImportError(
                    "nodriver is not installed. Install the scraping extra: "
                    "`uv sync --extra scraping`"
                ) from exc

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
                _logger.debug("ResponseReceived handler failed", exc_info=True)

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
                _logger.debug("Network.getResponseBody failed", exc_info=True)
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
        except (ProcessLookupError, OSError):
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except TimeoutError:
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
        except (ProcessLookupError, OSError):
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
                _logger.debug("Browser shutdown via loop failed", exc_info=True)
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

    def restart(self) -> None:
        """Beendet die aktuelle Chromium-Instanz und erzwingt einen frischen Browser-Start.

        Anders als ``close()`` bleiben Hintergrund-Thread und Event-Loop
        erhalten - nur der Browser-Prozess wird ersetzt. Neuer Prozess =
        neuer CDP-Fingerprint und leere Cookies, was wiederholte Akamai-WAF-
        Bloecke auf demselben Fingerprint umgeht (siehe ``NodriverTeslaClient
        ._fetch``-Retry-Logik).
        """
        if self._closed or self._loop is None or self._browser is None:
            return
        with contextlib.suppress(Exception):
            future = asyncio.run_coroutine_threadsafe(self._shutdown_browser(), self._loop)
            future.result(timeout=15.0)

    def run(
        self,
        coro_factory: Callable[[Any], Awaitable[_T]],
        timeout_s: float,
    ) -> _T:
        """Fuehrt eine beliebige Koroutine mit Zugriff auf den Browser aus.

        Anders als ``fetch()`` (reine GET-Navigation einer einzelnen URL)
        erlaubt dies mehrstufige Interaktionen (Tippen, Klicken, mehrere
        Netzwerk-Intercepts) auf derselben Browser-Instanz, ohne die Thread-/
        Browser-Lifecycle-Logik dieser Klasse zu duplizieren - genutzt vom
        Human-Flow-Client (siehe ``human_flow.py``).

        Args:
            coro_factory: Erhaelt das gestartete ``nodriver``-``Browser``-
                Objekt und liefert eine Koroutine, deren Ergebnis
                zurueckgegeben wird.
            timeout_s: Maximale Wartezeit in Sekunden.

        Returns:
            Das Ergebnis der von ``coro_factory`` gelieferten Koroutine.

        Raises:
            CurlError: Bei Timeout, Browser-/Netzwerk-Fehlern, oder wenn der
                Fetcher bereits geschlossen wurde.
        """
        if self._closed:
            raise CurlError("nodriver-Fetcher wurde bereits geschlossen")
        try:
            future = self._submit(self._run_with_browser(coro_factory))
            return future.result(timeout=timeout_s)  # type: ignore[no-any-return]
        except Exception as e:
            raise CurlError(f"nodriver human-flow failed: {e}") from e

    async def _run_with_browser(self, coro_factory: Callable[[Any], Awaitable[_T]]) -> _T:
        browser = await self._ensure_browser()
        return await coro_factory(browser)


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

    PRICING_BASE_URL: str = "https://www.tesla.com/de_de/findus/location/supercharger"
    """Oeffentliche Standort-Detailseite (einzige Seite mit kWh-Preisen). Der
    `de_de`-Locale-Praefix ist erforderlich - siehe
    `common.TeslaJsonEndpointsMixin.PRICING_BASE_URL` fuer die Begruendung
    (ohne Locale liefert Tesla fuer manche Standorte eine geo-abhaengige
    Zwischenseite ohne `formattedData`)."""

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
        _debug_log(self._debug_log, f"{method} {url}", label="NODRIVER")

    async def _fetch(self, url: str) -> str:
        """Fuehrt GET aus und liefert den Response-Body als Text.

        Wiederholt bei Akamai-WAF-Bloecken (auch bei HTTP 200 - Tesla liefert
        die "Access Denied"-Blockseite nicht zuverlaessig mit 403/429) und bei
        403/429/leeren Antworten bis zu ``WAF_RETRY_MAX_ATTEMPTS`` mal, mit
        exponentiellem Backoff und einem frischen Browser-Prozess (neuer
        Fingerprint, keine Cookies) pro Versuch - verifiziertes Muster gegen
        Akamai (siehe ``docs/Tesla-Supercharger-Detail-Scraping.md``). Andere
        HTTP-Fehler (404, 500, ...) sind nicht retry-faehig und werfen sofort.

        Args:
            url: Vollstaendige URL mit Query-Parametern

        Returns:
            Response-Body als Text

        Raises:
            CurlError: Bei anhaltenden WAF-Bloecken/Rate-Limits oder anderen
                HTTP-Fehlern, leeren Antworten oder Netzwerkfehlern
        """
        last_error = CurlError("nodriver: kein Versuch unternommen")
        for attempt in range(1, WAF_RETRY_MAX_ATTEMPTS + 1):
            await self._log_request("GET", url)
            status, body = await asyncio.to_thread(self._fetcher.fetch, url)

            _debug_log(
                self._debug_log,
                f"NODRIVER GET {url} -> {status}\n"
                f"  Body ({len(body)} bytes): {body[:DEBUG_BODY_PREVIEW_CHARS]}",
                label="NODRIVER",
            )

            if is_waf_block(body):
                last_error = CurlError("Tesla API: WAF-Block (Access Denied)")
            elif not body.strip():
                last_error = CurlError("empty response")
            elif status == HTTPStatus.FORBIDDEN:
                last_error = CurlError("Tesla API: 403 Access Denied (mglw. rate-limited)")
            elif status == HTTPStatus.TOO_MANY_REQUESTS:
                last_error = CurlError("Tesla API: 429 Too Many Requests (Rate-Limit)")
            elif status != HTTPStatus.OK:
                raise CurlError(f"Tesla API: HTTP {status}")
            else:
                return body

            if attempt < WAF_RETRY_MAX_ATTEMPTS:
                _debug_log(
                    self._debug_log,
                    f"Versuch {attempt}/{WAF_RETRY_MAX_ATTEMPTS} fehlgeschlagen "
                    f"({last_error}), Browser wird neu gestartet und erneut versucht.",
                    label="RETRY",
                )
                await asyncio.to_thread(self._fetcher.restart)
                await asyncio.sleep(waf_retry_delay_s(attempt))

        raise last_error

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
                f"JSON parse error: {e}\nbody preview: {body[:DEBUG_BODY_PREVIEW_CHARS]}",
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
            except self.CurlError:
                _logger.debug("Detail request for %s failed, skipping", slug, exc_info=True)
                continue
            if effective_delay > 0:
                await asyncio.sleep(effective_delay)

        return details

    async def fetch_pricing_html(self, slug: str) -> str:
        """Fetches the raw HTML of a Supercharger's public detail page."""
        encoded_slug = quote(slug, safe="")
        url = f"{self.PRICING_BASE_URL}/{encoded_slug}"
        return await self._fetch(url)
