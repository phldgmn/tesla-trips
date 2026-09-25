"""HTTP-Client fuer die oeffentliche Tesla Locations-API via curl_cffi."""

from __future__ import annotations

import asyncio
import contextlib
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from curl_cffi import AsyncSession

from .common import (
    DEBUG_BODY_PREVIEW_CHARS,
    WAF_RETRY_MAX_ATTEMPTS,
    CurlError,
    TeslaJsonEndpointsMixin,
    _debug_log,
    is_waf_block,
    waf_retry_delay_s,
)


class TeslaLocationsClient(TeslaJsonEndpointsMixin):
    """HTTP-Client fuer die oeffentliche Tesla Locations-API via curl_cffi.

    Uses ``curl_cffi.AsyncSession`` mit JA3/TLS-Fingerprint-Impersonation
    (Chrome 150), um den Akamai WAF von tesla.com zu umgehen. Der
    ``impersonate``-Preset generiert automatisch die korrekten HTTP/2
    Header-Sequenz und den User-Agent — manuell gesetzte Header (wie die
    alten ``_CURL_HEADERS``) sind nicht more noetig, koennen aber zur
    Ueberschreibung uses werden.

    Ein ``AsyncSession``-Objekt wird pro Client-Instanz erzeugt und
    ueber alle Requests hinweg wiederuses, was Cookie-Jar-Tracking,
    TCP-Connection-Pooling und HTTP/2-Stream-Multiplexing aktiviert.

    Die drei oeffentlichen Endpunkte (fetch_locations, fetch_location_details,
    fetch_pricing_html) sowie deren Orchestrierung (fetch_all_supercharger_details)
    sind in ``TeslaJsonEndpointsMixin`` geteilt; diese Klasse liefert nur den
    curl_cffi-Transport (``_fetch``).

    Usage:
        client = TeslaLocationsClient()
        details = await client.fetch_all_supercharger_details("DE")
        await client.close()
    """

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
            self._client: AsyncSession = self._new_session()
            self._owns_client: bool = True
        else:
            self._client = client
            self._owns_client = False

    async def _log_request(self, method: str, url: str) -> None:
        """Loggt eine request fuer Debug-Zwecke."""
        _debug_log(self._debug_log, f"{method} {url}", label="HTTP")

    async def _log_response(self, response: Any, body_preview: str, label: str = "HTTP") -> None:
        """Loggt eine HTTP-response fuer Debug-Zwecke."""
        _debug_log(
            self._debug_log,
            f"{label} {response.request.method} "
            f"{response.request.url} -> {response.status_code}\n"
            f"  Body ({len(response.content)} bytes): {body_preview}",
            label,
        )

    async def _rotate_session(self) -> None:
        """Ersetzt die Session durch eine frische Instanz vor einem Retry.

        Neue Session = neuer TLS/Header-Fingerprint und leere Cookies, was
        wiederholte Akamai-WAF-Bloecke auf demselben Fingerprint umgeht
        (siehe ``_fetch``-Retry-Logik). Wird uebersprungen, wenn die Session
        extern injiziert wurde (nicht ``_owns_client`` - z.B. in Tests oder
        bei geteilten Sessions, deren Lebenszyklus der caller verwaltet).
        """
        if not self._owns_client:
            return
        old_client = self._client
        self._client = self._new_session()
        with contextlib.suppress(Exception):
            await old_client.close()

    async def _fetch(self, url: str) -> str:
        """Fuehrt GET aus und liefert den Response-Body als Text.

        Wiederholt bei Akamai-WAF-Bloecken (auch bei HTTP 200 - Tesla liefert
        die "Access Denied"-Blockseite nicht zuverlaessig mit 403/429) und bei
        403/429/leeren responseen bis zu ``WAF_RETRY_MAX_ATTEMPTS`` mal, mit
        exponentiellem Backoff und einer frischen Session (neuer Fingerprint,
        keine Cookies) pro Versuch. Netzwerkfehler und andere HTTP-Fehler
        (404, 500, ...) sind nicht retry-faehig und werfen sofort.

        Args:
            url: Vollstaendige URL mit Query-Parametern

        Returns:
            Response-Body als Text

        Raises:
            CurlError: Bei anhaltenden WAF-Bloecken/Rate-Limits oder anderen
                HTTP-Fehlern, leeren responseen oder Netzwerkfehlern
        """
        last_error = self.CurlError("kein Versuch unternommen")
        for attempt in range(1, WAF_RETRY_MAX_ATTEMPTS + 1):
            try:
                response = await self._client.get(url)
            except Exception as e:
                raise self.CurlError(f"request failed: {e}") from e

            body = response.text
            await self._log_response(response, body[:DEBUG_BODY_PREVIEW_CHARS])

            if is_waf_block(body):
                last_error = self.CurlError("Tesla API: WAF-Block (Access Denied)")
            elif not body.strip():
                last_error = self.CurlError("empty response")
            elif response.status_code == HTTPStatus.FORBIDDEN:
                last_error = self.CurlError("Tesla API: 403 Access Denied (mglw. rate-limited)")
            elif response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
                last_error = self.CurlError("Tesla API: 429 Too Many Requests (Rate-Limit)")
            elif response.status_code != HTTPStatus.OK:
                raise self.CurlError(f"Tesla API: HTTP {response.status_code}")
            else:
                return body

            if attempt < WAF_RETRY_MAX_ATTEMPTS:
                await self._rotate_session()
                await asyncio.sleep(waf_retry_delay_s(attempt))

        raise last_error

    def _new_session(self) -> AsyncSession:
        """Create an owned curl_cffi session (imported lazily: optional extra)."""
        try:
            from curl_cffi import AsyncSession
        except ImportError as exc:
            raise ImportError(
                "curl_cffi is not installed. Install the scraping extra: `uv sync --extra scraping`"
            ) from exc
        return AsyncSession(
            impersonate=self._IMPERSONATE,
            timeout=30.0,
            headers=dict(self._BASE_HEADERS),
        )

    async def close(self) -> None:
        """Close the underlying curl_cffi session if owned by this instance."""
        if self._owns_client:
            await self._client.close()
            self._owns_client = False
