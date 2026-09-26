"""Gemeinsame Grundlagen: Debug-Log, CurlError, TeslaClient-Protokoll."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Protocol, runtime_checkable
from urllib.parse import quote

_logger = logging.getLogger(__name__)


DEBUG_BODY_PREVIEW_CHARS: int = 200
"""Maximum response-body characters included in scrape debug lines."""


def default_debug_log_path() -> Path:
    """Default file for ``--debug`` scrape logs: ``$TRIPPLANNER_CACHE_DIR/logs/``.

    Lives under the git-ignored cache directory so raw WAF tokens and request
    URLs never end up in the working tree root.
    """
    log_dir = Path(os.environ.get("TRIPPLANNER_CACHE_DIR", ".cache")) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "charger_debug.log"


def _debug_log(log_path: Path | None, msg: str, label: str = "DEBUG") -> None:
    """Emits a detailed scrape debug line via the standard logger and, optionally, a file.

    Always logged at DEBUG level through the ``tripplanner`` logger namespace
    (see `trip_input.app._configure_logging`, `TRIPPLANNER_LOG_LEVEL`), so
    Tesla crawling/scraping triggered from the frontend (which never sets
    `log_path`) is still fully visible whenever DEBUG logging is enabled -
    not only when a CLI caller explicitly opts into a debug log file.

    Args:
        log_path: Optional additional file path to append the line to.
        msg: Die Nachricht
        label: Label für die Zeile (z.B. "CURL", "HTTP", "JSON")
    """
    _logger.debug("[%s] %s", label, msg)
    if log_path is None:
        return
    timestamp = datetime.now(UTC).isoformat(timespec="milliseconds")
    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] [{label}] {msg}\n")
    except OSError:
        pass


_WAF_BLOCK_MARKERS: tuple[str, ...] = ("Access Denied", "errors.edgesuite.net")
"""Substrings identifying an Akamai edge WAF block page.

Tesla's WAF sometimes returns this block page with HTTP 200 (not 403/429) -
e.g. for ``get-locations``, which is otherwise expected to return JSON. A
bare status-code check therefore misses these blocks; callers must inspect
the body via ``is_waf_block`` before treating a 200 response as success.
"""

WAF_RETRY_MAX_ATTEMPTS: int = 4
"""Anzahl Gesamtversuche bei WAF-Block/Rate-Limit, bevor endgueltig
aufgegeben wird (verifiziertes Muster: neuer Browser-/Session-Fingerprint
pro Versuch umgeht Akamai zuverlaessiger als ein blosser Retry)."""

WAF_RETRY_BASE_DELAY_S: float = 1.5
"""Basis-Verzoegerung (Sekunden) fuer den exponentiellen Backoff zwischen
Retry-Versuchen (siehe ``waf_retry_delay_s``)."""


def is_waf_block(body: str) -> bool:
    """True, wenn ``body`` wie eine Akamai-WAF-Blockseite aussieht.

    Args:
        body: Response-Body (Text)

    Returns:
        True, wenn ein bekannter Block-Marker im Body vorkommt.
    """
    return any(marker in body for marker in _WAF_BLOCK_MARKERS)


def waf_retry_delay_s(attempt: int) -> float:
    """Exponentielle Backoff-Verzoegerung vor Retry-Versuch ``attempt``.

    Args:
        attempt: 1-indizierte Nummer des soeben fehlgeschlagenen Versuchs.

    Returns:
        Verzoegerung in Sekunden vor dem naechsten Versuch.
    """
    return float(WAF_RETRY_BASE_DELAY_S * (2 ** (attempt - 1)))


class CurlError(Exception):
    """HTTP-Request fehlgeschlagen (WAF-Block, Rate-Limit, Netzwerkfehler).

    Gemeinsamer Fehlertyp fuer alle Tesla-Clients (curl_cffi und Safari),
    damit Aufrufer unabhaengig vom gewaehlten Transport-Mechanismus denselben
    Exception-Typ abfangen koennen.
    """


class TeslaJsonEndpointsMixin:
    """Fetch-/Parse-Logik fuer die drei oeffentlichen Tesla-Endpunkte.

    Transport-unabhaengiger Teil der Tesla-Clients: baut die Request-URLs,
    parst die JSON-Antworten und orchestriert die Detail-Requests mit
    Rate-Limiting. Setzt voraus, dass die Subklasse ``_fetch`` (Roh-Text
    einer URL abrufen), ``_delay`` (Sekunden zwischen Detail-Requests),
    ``CurlError`` (Exception-Typ) und optional ``_debug_log`` bereitstellt.

    Geteilt von ``TeslaLocationsClient`` (curl_cffi-Transport) und
    ``SafariTeslaClient`` (Safari-Transport via AppleScript), da beide
    dieselben Tesla-Endpunkte ansprechen und sich nur im Transport
    unterscheiden.
    """

    BASE_URL: str = "https://www.tesla.com/api/findus"

    PRICING_BASE_URL: str = "https://www.tesla.com/de_de/findus/location/supercharger"
    """Oeffentliche Standort-Detailseite (Next.js, kein JSON-API-Endpunkt wie
    `get-location-details`). Anders als `get-location-details` enthaelt nur
    diese Seite die kWh-Preise, eingebettet in einem
    `<script id="__NEXT_DATA__">`-JSON-Blob - siehe `pricing.parse_pricing_tiers`
    fuer das Parsing und `docs/Tesla-Supercharger-Detail-Scraping.md` fuer die
    Herkunft dieser Struktur (reverse-engineered vom Referenz-Tool `tesla-
    pricing`). Der `de_de`-Locale-Praefix ist erforderlich: ohne Locale im
    Pfad liefert Tesla fuer manche Standorte (z. B. schwedische Supercharger
    wie Falkenberg) eine geo-/locale-abhaengige Zwischenseite ohne
    `props.pageProps.formattedData` statt der eigentlichen Standortseite,
    obwohl der Slug gueltig ist. Die konkrete Locale ist dabei irrelevant
    fuer den Seiteninhalt (Preise/Struktur sind sprachunabhaengig identisch);
    `de_de` spiegelt nur den bereits andernorts genutzten Default (siehe
    `TeslaJsonEndpointsMixin.fetch_location_details`)."""

    _delay: float
    _debug_log: Path | None
    CurlError: ClassVar[type[CurlError]]

    async def _fetch(self, url: str) -> str:
        raise NotImplementedError

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
                f"JSON parse error: {e}\nbody preview: {body[:DEBUG_BODY_PREVIEW_CHARS]}",
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
            CurlError: Bei Transport-Fehlern oder WAF-Block
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
            CurlError: Bei Transport-Fehlern oder WAF-Block
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
            except self.CurlError:
                _logger.debug("Detail request for %s failed, skipping", slug, exc_info=True)
                continue
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
            CurlError: Bei Transport-Fehlern, WAF-Block oder anderen
                Nicht-200-Antworten.
        """
        encoded_slug = quote(slug, safe="")
        url = f"{self.PRICING_BASE_URL}/{encoded_slug}"
        return await self._fetch(url)


@runtime_checkable
class TeslaClient(Protocol):
    """Protokoll fuer Tesla-API-Clients (Safari-, curl_cffi- und nodriver-Backend).

    Alle Implementierungen (`SafariTeslaClient`, `TeslaLocationsClient`,
    `NodriverTeslaClient`) bieten dieselbe Schnittstelle fuer Standortliste,
    Detaildaten und Preis-HTML. `runtime_checkable` erlaubt ``isinstance``-
    Pruefungen.
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
    transport: str = "safari",
    *,
    rate_limit_delay_s: float = 0.5,
    debug_log: Path | None = None,
) -> TeslaClient:
    """Erzeugt einen Tesla-API-Client mit dem gewuenschten Transport.

    Default ist ``safari`` (steuert die bereits laufende, echte Safari-
    Instanz des Nutzers per AppleScript): einzig verifizierter Transport,
    der den Akamai-WAF von tesla.com zuverlaessig umgeht, ohne den
    Chromium-Fokus-Diebstahl-Bug (CDP-basierte Browser holen bei jeder
    Navigation zwangsweise den Fokus, siehe ``nodriver.py``) oder das
    NAT-Problem containerisierter Browser (Docker/OrbStack routen
    Container-Traffic ueber eine eigene, von Akamai geblockte IP - unabhaengig
    vom Netzwerk-Modus) zu haben. ``curl_cffi`` bleibt als leichtgewichtige,
    browserlose Alternative verfuegbar (JA3/TLS-Fingerprint-Impersonation),
    wird von Akamai aber inzwischen zuverlaessig geblockt. ``nodriver`` und
    ``nodriver_human`` sind als nicht-defaultige Legacy-Transporte erhalten
    (funktionsfaehig, aber mit den oben genannten strukturellen Problemen).

    Args:
        transport: ``"safari"`` (Default), ``"curl_cffi"``, ``"nodriver"``
            oder ``"nodriver_human"``.
        rate_limit_delay_s: Verzoegerung zwischen Detail-Requests.
        debug_log: Optionaler Dateipfad fuer Request/Response-Debug-Log.

    Returns:
        Ein ``TeslaClient``-kompatibles Objekt.

    Raises:
        ValueError: Bei unbekanntem ``transport``-Wert.
    """
    if transport in ("nodriver", "nodriver_human"):
        try:
            importlib.import_module("nodriver")
        except ImportError:
            # nodriver nicht installiert -> curl_cffi als Fallback.
            transport = "curl_cffi"

    if transport == "safari":
        from .safari import SafariTeslaClient

        return SafariTeslaClient(
            rate_limit_delay_s=rate_limit_delay_s,
            debug_log=debug_log,
        )
    if transport == "nodriver":
        from .nodriver import NodriverTeslaClient

        return NodriverTeslaClient(
            rate_limit_delay_s=rate_limit_delay_s,
            debug_log=debug_log,
        )
    if transport == "nodriver_human":
        from .human_flow import NodriverHumanFlowTeslaClient

        return NodriverHumanFlowTeslaClient(
            rate_limit_delay_s=rate_limit_delay_s,
            debug_log=debug_log,
        )
    if transport == "curl_cffi":
        from .tesla_curl import TeslaLocationsClient

        return TeslaLocationsClient(
            rate_limit_delay_s=rate_limit_delay_s,
            debug_log=debug_log,
        )
    raise ValueError(
        f"Unbekannter Tesla-Client-Transport '{transport}'. "
        "Erlaubt: 'safari', 'curl_cffi', 'nodriver', 'nodriver_human'."
    )
