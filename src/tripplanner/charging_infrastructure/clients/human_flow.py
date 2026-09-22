"""Human-Flow Tesla-Client: findus-Kartenseite wie ein Mensch bedienen.

Ablauf pro Standort (nachgebildet aus echten Browser-Netzwerk-Captures):

1. Navigiert zu ``https://www.tesla.com/{locale}/findus`` (die Kartenseite).
2. Tippt (zeichenweise, mit menschlichem Tempo) eine Suchanfrage in das
   Such-Input-Feld (``input.tds-form-input-search``). Tesla feuert daraufhin
   eigene XHR/fetch-Anfragen unter ``/api/findus/*``, deren Antwort hier
   abgefangen wird (siehe ``search_locations``).
3. Waehlt ein Ergebnis aus - nachgebildet durch eine Navigation zu der
   URL-Form, die ein echter Klick auf ein Suchergebnis erzeugt (beobachtet
   im ``referrer``-Header echter Tesla-Requests:
   ``.../findus?search=<query>&location=<slug>&functionType=party``).
   Tesla feuert daraufhin ``get-location-details`` und
   ``get-charger-details`` fuer genau diesen Standort, die hier ebenfalls
   abgefangen werden (siehe ``fetch_location_details``).

Ziel: Akamai-Bot-Erkennung umgehen, die auf isolierte API-Aufrufe ohne
vorausgehende Seiten-Interaktion (Navigation, Tippen) abzielen koennte - im
Gegensatz zu ``NodriverTeslaClient``, der die JSON-Endpunkte direkt per GET
anfragt. Bei anhaltendem Fehlschlag faellt ``fetch_location_details`` auf
den direkten JSON-API-GET der Basisklasse zurueck.

``fetch_locations`` (Bulk-Standortverzeichnis eines ganzen Landes) hat kein
Such-Aequivalent auf der Kartenseite (das wuerde tausende Einzelsuchen
erfordern) und bleibt daher unveraendert von der Basisklasse geerbt, ebenso
``fetch_pricing_html`` (laedt bereits die echte Standort-Detailseite, kein
API-Endpunkt).
"""

from __future__ import annotations

import asyncio
import base64
import json
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .common import DEBUG_BODY_PREVIEW_CHARS, CurlError, _debug_log, is_waf_block, waf_retry_delay_s
from .nodriver import NodriverBrowserFetcher, NodriverTeslaClient

_SEARCH_INPUT_SELECTOR = "input.tds-form-input-search"
_SEARCH_RESPONSE_TIMEOUT_S = 15.0
_DETAIL_RESPONSE_TIMEOUT_S = 20.0
_TOTAL_TIMEOUT_S = 60.0
_TYPING_DELAY_RANGE_S = (0.05, 0.15)
_PRE_SELECT_DELAY_RANGE_S = (0.4, 1.0)

_HUMAN_FLOW_MAX_ATTEMPTS = 2
"""Retries innerhalb des (teuren) Human-Flows selbst, bevor - im Fall von
``fetch_location_details`` - auf den direkten JSON-API-GET der Basisklasse
zurueckgefallen wird (der wiederum eigene ``WAF_RETRY_MAX_ATTEMPTS`` Versuche
unternimmt). Niedriger als ``WAF_RETRY_MAX_ATTEMPTS``, weil ein Human-Flow-
Versuch (Navigation + Tippen + zweite Navigation) deutlich teurer ist als ein
einzelner JSON-GET."""


def _locale_path(locale: str) -> str:
    """Wandelt eine Locale (``de_DE``/``de-DE``) in Teslas URL-Pfadsegment.

    Args:
        locale: Locale-String, z.B. ``de_DE`` oder ``de-DE``.

    Returns:
        Kleingeschriebenes Pfadsegment mit Unterstrich, z.B. ``de_de``.
    """
    return locale.replace("-", "_").lower()


async def _collect_responses(
    tab: Any,
    action: Callable[[], Awaitable[None]],
    url_markers: dict[str, str],
    timeout_s: float,
    debug_log: Any = None,
) -> dict[str, str]:
    """Faengt CDP-Netzwerk-Antworten passend zu ``url_markers`` ab.

    Registriert CDP-Response-/Ladeende-Handler, fuehrt ``action`` aus und
    liest den Response-Body jedes passenden Requests bei ``Network.
    loadingFinished`` aus. ``Network.enable`` wird mit
    ``enable_durable_messages=True`` aufgerufen: unser ``action`` loest bei
    ``_run_location_and_charger_details`` typischerweise eine Navigation
    aus (Ergebnisauswahl), waehrend deren fuer die Zielseite selbst
    initiierte ``fetch()``-Antworten abgefangen werden sollen. Ohne dieses
    Flag verwirft Chrome bereits gepufferte Response-Bodies bei einer
    Navigation (auch same-origin) - ``Network.getResponseBody`` schlaegt
    dann mit "No resource with given identifier" fehl, selbst wenn der Body
    unmittelbar nach ``loadingFinished`` abgerufen wird.

    Args:
        tab: Aktiver ``nodriver``-Tab.
        action: Koroutine, die die Netzwerk-Aktivitaet ausloest (Tippen,
            Navigation, ...).
        url_markers: Mapping von Ergebnis-Name auf URL-Teilstring, nach dem
            in jeder Response-URL gesucht wird.
        timeout_s: Maximale Wartezeit auf alle Antworten.
        debug_log: Optionaler Debug-Log-Pfad (siehe ``common._debug_log``).

    Returns:
        Mapping Name -> Response-Body fuer jede erfolgreich gelesene
        Antwort; fehlende Eintraege bei Timeout oder nicht mehr
        abrufbarem Body ausgelassen.
    """
    from nodriver import cdp

    bodies: dict[str, str] = {}
    pending = dict(url_markers)
    staged: dict[Any, str] = {}
    fetch_tasks: list[asyncio.Task[None]] = []

    async def capture_body(request_id: Any, name: str) -> None:
        """Liest den Body sofort aus und traegt ihn bei Erfolg in ``bodies`` ein."""
        try:
            body = await _response_body(tab, request_id)
        except CurlError as e:
            _debug_log(
                debug_log, f"Human-Flow: Body fuer '{name}' nicht abrufbar: {e}", label="HUMANFLOW"
            )
            return
        bodies[name] = body

    def on_response(event: Any) -> None:
        """Merkt Requests vor, deren URL zu ``url_markers`` passt (bis ``on_finished`` feuert)."""
        try:
            resp = getattr(event, "response", None)
            if resp is None:
                return
            url = getattr(resp, "url", "") or ""
            req_id = getattr(event, "request_id", None)
            if req_id is None:
                return
            for name, marker in list(pending.items()):
                if marker in url:
                    staged[req_id] = name
                    del pending[name]
        except Exception:
            pass

    def on_finished(event: Any) -> None:
        """Startet den Body-Abruf, sobald ein vorgemerkter Request fertig geladen ist."""
        try:
            req_id = getattr(event, "request_id", None)
            name = staged.pop(req_id, None)
            if name is not None:
                fetch_tasks.append(asyncio.ensure_future(capture_body(req_id, name)))
        except Exception:
            pass

    await tab.send(
        cdp.network.enable(
            max_total_buffer_size=10_000_000,
            max_resource_buffer_size=5_000_000,
            enable_durable_messages=True,
        )
    )
    tab.add_handler(cdp.network.ResponseReceived, on_response)
    tab.add_handler(cdp.network.LoadingFinished, on_finished)
    try:
        await action()
        deadline = time.monotonic() + timeout_s
        while len(bodies) < len(url_markers) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        if fetch_tasks:
            await asyncio.wait(fetch_tasks, timeout=2.0)
    finally:
        tab.remove_handler(cdp.network.ResponseReceived, on_response)
        tab.remove_handler(cdp.network.LoadingFinished, on_finished)

    return bodies


async def _response_body(tab: Any, request_id: Any) -> str:
    """Holt den Response-Body eines XHR/fetch-Requests via CDP.

    Args:
        tab: Aktiver ``nodriver``-Tab.
        request_id: CDP-Request-ID der abzufragenden Antwort.

    Returns:
        Response-Body als Text (leer bei fehlendem Body).

    Raises:
        CurlError: Wenn der Body nicht abrufbar ist (z.B. bereits verworfen).
    """
    from nodriver import cdp

    try:
        raw_body, base64_encoded = await tab.send(
            cdp.network.get_response_body(request_id=request_id)
        )
    except Exception as e:
        raise CurlError(f"Human-Flow: Response-Body nicht abrufbar: {e}") from e
    if base64_encoded:
        return base64.b64decode(raw_body).decode("utf-8", errors="replace")
    return raw_body or ""


async def _type_into_search(tab: Any, query: str) -> None:
    """Tippt ``query`` zeichenweise mit menschlichem Tempo in das Such-Input.

    Args:
        tab: Aktiver ``nodriver``-Tab (bereits auf der findus-Kartenseite).
        query: Suchbegriff.

    Raises:
        CurlError: Wenn das Such-Input nicht gefunden wurde.
    """
    search_input = await tab.select(_SEARCH_INPUT_SELECTOR, timeout=15)
    if search_input is None:
        raise CurlError(f"Human-Flow: Such-Input '{_SEARCH_INPUT_SELECTOR}' nicht gefunden")
    await search_input.click()
    for char in query:
        await search_input.send_keys(char)
        await asyncio.sleep(random.uniform(*_TYPING_DELAY_RANGE_S))


async def _run_search(browser: Any, base_url: str, query: str, locale: str, debug_log: Any) -> str:
    """Navigiert zur Kartenseite und sucht ``query`` im Such-Input.

    Tippt ``query`` in die Suche und liefert den Body der resultierenden
    Such-JSON-Antwort.

    Args:
        browser: Gestartetes ``nodriver``-``Browser``-Objekt.
        base_url: Basis-URL der findus-Kartenseite (Default
            ``https://www.tesla.com``; ueberschreibbar fuer Tests gegen
            einen lokalen Server).
        query: Suchbegriff.
        locale: Tesla-Locale fuer den URL-Pfad.
        debug_log: Optionaler Debug-Log-Pfad (siehe ``common._debug_log``).

    Returns:
        Roh-Body der Such-Antwort.

    Raises:
        CurlError: Wenn keine passende Antwort abgefangen wurde.
    """
    tab = browser.main_tab
    findus_url = f"{base_url}/{_locale_path(locale)}/findus"
    _debug_log(debug_log, f"Navigiere zu {findus_url}", label="HUMANFLOW")
    await tab.get(findus_url)
    await tab

    async def action() -> None:
        await _type_into_search(tab, query)

    bodies = await _collect_responses(
        tab, action, {"search": "/api/findus/"}, _SEARCH_RESPONSE_TIMEOUT_S, debug_log
    )
    if "search" not in bodies:
        raise CurlError(f"Human-Flow: keine Such-Antwort fuer '{query}' empfangen")
    body = bodies["search"]
    _debug_log(
        debug_log,
        f"HUMANFLOW search '{query}'\n"
        f"  Body ({len(body)} bytes): {body[:DEBUG_BODY_PREVIEW_CHARS]}",
        label="HUMANFLOW",
    )
    return body


@dataclass(frozen=True)
class _LocationLookup:
    """Such-/Auswahlparameter fuer einen Standort-Detail-Human-Flow.

    Buendelt die Argumente fuer ``_run_location_and_charger_details``, um
    die Argumentanzahl der Funktion klein zu halten.
    """

    location_slug: str
    search_query: str
    function_type: str
    locale: str
    base_url: str


async def _run_location_and_charger_details(
    browser: Any,
    lookup: _LocationLookup,
    debug_log: Any,
) -> dict[str, str]:
    """Navigiert Kartenseite -> Suche -> Ergebnisauswahl fuer einen Standort.

    Liefert die dabei abgefangenen Roh-Bodies von ``get-location-details``
    und ``get-charger-details``.

    Args:
        browser: Gestartetes ``nodriver``-``Browser``-Objekt.
        lookup: Such-/Auswahlparameter (Slug, Suchbegriff, functionType,
            Locale, Basis-URL).
        debug_log: Optionaler Debug-Log-Pfad (siehe ``common._debug_log``).

    Returns:
        ``{"location-details": <body>, "charger-details": <body>}``.

    Raises:
        CurlError: Wenn eine der beiden Antworten nicht abgefangen wurde.
    """
    tab = browser.main_tab
    findus_url = f"{lookup.base_url}/{_locale_path(lookup.locale)}/findus"
    _debug_log(debug_log, f"Navigiere zu {findus_url}", label="HUMANFLOW")
    await tab.get(findus_url)
    await tab
    await _type_into_search(tab, lookup.search_query)
    await asyncio.sleep(random.uniform(*_PRE_SELECT_DELAY_RANGE_S))

    # Nachbildung der Ergebnisauswahl: dieselbe URL-Form, die ein echter
    # Klick auf das Suchergebnis erzeugt (siehe Modul-Docstring).
    selected_url = (
        f"{findus_url}?search={quote(lookup.search_query)}"
        f"&location={quote(lookup.location_slug)}&functionType={lookup.function_type}"
    )

    async def action() -> None:
        await tab.get(selected_url)
        await tab

    bodies = await _collect_responses(
        tab,
        action,
        {"location-details": "get-location-details", "charger-details": "get-charger-details"},
        _DETAIL_RESPONSE_TIMEOUT_S,
        debug_log,
    )
    missing = [name for name in ("location-details", "charger-details") if name not in bodies]
    if missing:
        raise CurlError(f"Human-Flow: keine Antwort fuer {', '.join(missing)} empfangen")
    for name, body in bodies.items():
        _debug_log(
            debug_log,
            f"HUMANFLOW {name}\n  Body ({len(body)} bytes): {body[:DEBUG_BODY_PREVIEW_CHARS]}",
            label="HUMANFLOW",
        )
    return bodies


class NodriverHumanFlowTeslaClient(NodriverTeslaClient):
    """``NodriverTeslaClient``-Variante fuer den Karten-Suche-Detail-Flow.

    Nur ``fetch_location_details`` (und das zusaetzliche ``search_locations``)
    nutzen den Human-Flow; alle anderen Methoden (``fetch_locations``,
    ``fetch_all_supercharger_details``, ``fetch_pricing_html``, ``close``)
    sind unveraendert von ``NodriverTeslaClient`` geerbt - siehe Modul-
    Docstring fuer die Begruendung. Da ``fetch_all_supercharger_details``
    intern ``fetch_location_details`` pro Standort aufruft, nutzt es damit
    automatisch den Human-Flow fuer jeden einzelnen Standort.
    """

    def __init__(
        self,
        rate_limit_delay_s: float = 0.5,
        debug_log: Path | None = None,
        fetcher: NodriverBrowserFetcher | None = None,
        base_url: str = "https://www.tesla.com",
    ) -> None:
        """Initialize the client.

        Args:
            rate_limit_delay_s: Delay in seconds between detail requests
                (siehe Basisklasse).
            debug_log: Optionaler Dateipfad fuer Request/Response-Debug-Log.
            fetcher: Optionaler vorab konfigurierter Fetcher (fuer Tests).
            base_url: Basis-URL der findus-Kartenseite (Default
                ``https://www.tesla.com``; ueberschreibbar fuer Tests gegen
                einen lokalen Server).
        """
        super().__init__(
            rate_limit_delay_s=rate_limit_delay_s, debug_log=debug_log, fetcher=fetcher
        )
        self._base_url = base_url

    async def search_locations(self, query: str, locale: str = "de_DE") -> list[dict[str, Any]]:
        """Sucht Standorte ueber das echte Such-Input der Kartenseite.

        Tippt ``query`` in ``input.tds-form-input-search`` und faengt die
        resultierende Such-JSON-Antwort ab, statt Teslas komplette
        Bulk-Standortliste (``fetch_locations``) abzurufen und client-seitig
        zu filtern - insbesondere nuetzlich zur Aufloesung stale
        numerischer Slugs, ohne die (haeufiger blockierte) Bulk-API zu
        belasten.

        Args:
            query: Suchbegriff (z.B. Stadt-/Standortname).
            locale: Tesla-Locale fuer den URL-Pfad (z.B. ``de_DE``).

        Returns:
            Liste der gefundenen Standort-Dicts (Tesla-Suchergebnis-Format).
            Leer, wenn die Suche kein auswertbares JSON lieferte.

        Raises:
            CurlError: Bei anhaltendem WAF-Block oder wenn keine Antwort
                empfangen wurde.
        """
        last_error = CurlError("kein Versuch unternommen")
        for attempt in range(1, _HUMAN_FLOW_MAX_ATTEMPTS + 1):
            try:
                body = await asyncio.to_thread(
                    self._fetcher.run,
                    lambda browser: _run_search(
                        browser, self._base_url, query, locale, self._debug_log
                    ),
                    _TOTAL_TIMEOUT_S,
                )
            except CurlError as e:
                last_error = e
                if attempt < _HUMAN_FLOW_MAX_ATTEMPTS:
                    await asyncio.to_thread(self._fetcher.restart)
                    await asyncio.sleep(waf_retry_delay_s(attempt))
                    continue
                raise
            if is_waf_block(body):
                last_error = CurlError("Tesla API: WAF-Block (Access Denied) im Human-Flow")
                if attempt < _HUMAN_FLOW_MAX_ATTEMPTS:
                    await asyncio.to_thread(self._fetcher.restart)
                    await asyncio.sleep(waf_retry_delay_s(attempt))
                    continue
                raise last_error
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                return []
            results = data.get("data", data) if isinstance(data, dict) else data
            return results if isinstance(results, list) else []
        raise last_error

    async def fetch_location_details(
        self,
        slug: str,
        in_hk_mo_tw: bool = False,
        locale: str = "de_DE",
        search_query: str | None = None,
        function_type: str = "party",
    ) -> dict[str, Any]:
        """Holt Standort- und Ladepunkt-Details ueber den Human-Flow.

        Sucht - statt den JSON-API-Endpunkt direkt anzufragen - erst ueber
        das echte Such-Input nach ``search_query`` (Default: ``slug``, was
        i.d.R. bereits ausreichend Treffer liefert), navigiert dann zur
        Detailansicht von ``slug`` und liefert die dabei abgefangenen
        ``get-location-details``-Daten (angereichert um die
        ``get-charger-details``-Daten unter dem Zusatzschluessel
        ``_charger_details``). Bei anhaltendem Fehlschlag (WAF-Block,
        Such-Input nicht gefunden, ...) faellt der Aufruf auf den direkten
        JSON-API-GET der Basisklasse zurueck.

        Args:
            slug: ``location_url_slug`` des Standorts.
            in_hk_mo_tw: Wie bei der Basisklasse (nur fuer den Fallback-Pfad
                relevant).
            locale: Tesla-Locale.
            search_query: Suchbegriff fuer das Such-Input; Default ``slug``.
            function_type: Tesla-``functionType``-Parameter (``party`` fuer
                Supercharger).

        Returns:
            ``data``-Teil der ``get-location-details``-Antwort (leer bei
            leerer/kaputter Antwort, wie bei der Basisklasse), angereichert
            um ``_charger_details``.
        """
        query = search_query if search_query is not None else slug
        lookup = _LocationLookup(
            location_slug=slug,
            search_query=query,
            function_type=function_type,
            locale=locale,
            base_url=self._base_url,
        )
        last_error = CurlError("kein Versuch unternommen")
        for attempt in range(1, _HUMAN_FLOW_MAX_ATTEMPTS + 1):
            try:
                bodies = await asyncio.to_thread(
                    self._fetcher.run,
                    lambda browser: _run_location_and_charger_details(
                        browser, lookup, self._debug_log
                    ),
                    _TOTAL_TIMEOUT_S,
                )
            except CurlError as e:
                last_error = e
                if attempt < _HUMAN_FLOW_MAX_ATTEMPTS:
                    await asyncio.to_thread(self._fetcher.restart)
                    await asyncio.sleep(waf_retry_delay_s(attempt))
                    continue
                _debug_log(
                    self._debug_log,
                    f"Human-Flow endgueltig fehlgeschlagen ({last_error}) - "
                    "Fallback auf direkten JSON-API-GET.",
                    label="HUMANFLOW",
                )
                return await super().fetch_location_details(slug, in_hk_mo_tw, locale)

            location_body = bodies["location-details"]
            if is_waf_block(location_body):
                last_error = CurlError("Tesla API: WAF-Block (Access Denied) im Human-Flow")
                if attempt < _HUMAN_FLOW_MAX_ATTEMPTS:
                    await asyncio.to_thread(self._fetcher.restart)
                    await asyncio.sleep(waf_retry_delay_s(attempt))
                    continue
                return await super().fetch_location_details(slug, in_hk_mo_tw, locale)

            try:
                location_data: dict[str, Any] = json.loads(location_body).get("data", {})
            except json.JSONDecodeError:
                return {}
            try:
                charger_data = json.loads(bodies["charger-details"]).get("data", {})
            except json.JSONDecodeError:
                charger_data = {}
            if charger_data:
                location_data["_charger_details"] = charger_data
            return location_data

        return await super().fetch_location_details(slug, in_hk_mo_tw, locale)
