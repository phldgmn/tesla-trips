"""Gemeinsame Grundlagen: Debug-Log, CurlError, TeslaClient-Protokoll."""

from __future__ import annotations

import importlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

_logger = logging.getLogger(__name__)


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
        from .nodriver import NodriverTeslaClient

        return NodriverTeslaClient(
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
        f"Unbekannter Tesla-Client-Transport '{transport}'. Erlaubt: 'nodriver', 'curl_cffi'."
    )
