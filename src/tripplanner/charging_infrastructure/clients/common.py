"""Gemeinsame Grundlagen: Debug-Log, CurlError, TeslaClient-Protokoll."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


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