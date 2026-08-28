"""Integrationstests fuer das nodriver-Backend (echter Chromium via CDP).

Nutzt einen echten (headlosen) Chromium-Browser gegen einen lokalen
HTTP-Server - keine Live-Requests gegen externe Dienste (siehe AGENTS.md).
Deckt damit genau jene Abzugswege ab, die die Unit-Tests nicht erreichen
koennen: die Response-Body-Extraktion in ``NodriverBrowserFetcher``
(``Network.getResponseBody`` -> MIME-abhaengiger Fallback auf
``innerText``/``get_content()``), die Status-Handling-Logik von
``NodriverTeslaClient._fetch`` und das saubere Herunterfahren von Browser
und Hintergrund-Thread.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tripplanner.charging_infrastructure.client import (
    CurlError,
    NodriverBrowserFetcher,
    NodriverTeslaClient,
)
from tripplanner.charging_infrastructure.pricing import parse_pricing_tiers

LOCATIONS_JSON = {
    "data": {
        "data": [
            {
                "uuid": "1001",
                "location_url_slug": "testsupercharger",
                "location_type": ["supercharger"],
                "latitude": 52.5,
                "longitude": 13.4,
                "inCN": False,
                "inHkMoTw": False,
            }
        ]
    }
}

DETAILS_JSON = {"data": {"marketing": {"display_name": "Test SC"}}}


def _pricing_html() -> str:
    """Wie bei www.tesla.com: chargerPricing liegt in einem
    ``<script id="__NEXT_DATA__">``, NICHT im sichtbaren Seitentext.

    Format entspricht dem parsebaren ``__NEXT_DATA__``-Layout (siehe
    ``tests/trip_input/test_cli.py::_pricing_html``).
    """
    payload = {
        "props": {
            "pageProps": {
                "formattedData": {
                    "chargerPricing": [
                        {"label": "Charging Fees for Tesla Owner", "price": "EUR 0,40/kWh"}
                    ]
                }
            }
        }
    }
    return (
        "<html><head><title>Test Supercharger</title></head><body><h1>Test Supercharger</h1>"
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'
        "</body></html>"
    )


class _Handler(BaseHTTPRequestHandler):
    """Liefert Test-Endpunkte mit dem MIME-Typ der Tesla-Originalendpunkte."""

    def do_GET(self) -> None:
        if self.path.startswith("/api/findus/get-locations"):
            self._send(200, "application/json", json.dumps(LOCATIONS_JSON))
        elif self.path.startswith("/api/findus/get-location-details"):
            self._send(200, "application/json", json.dumps(DETAILS_JSON))
        elif self.path.startswith("/findus/location/supercharger/testsupercharger"):
            self._send(200, "text/html", _pricing_html())
        elif self.path.startswith("/blocked"):
            self._send(403, "text/html", "<html>Access Denied</html>")
        elif self.path.startswith("/throttled"):
            self._send(429, "text/plain", "rate limited")
        else:
            self._send(404, "text/html", "<html>Not Found</html>")

    def _send(self, status: int, content_type: str, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:
        """Suppress default stderr access logs."""


@pytest.fixture
def base_url() -> str:
    """Lokaler HTTP-Server auf einem freien Port."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5.0)


@pytest.fixture
def client(base_url: str, monkeypatch: pytest.MonkeyPatch) -> NodriverTeslaClient:
    """NodriverTeslaClient mit echtem Fetcher gegen den lokalen Server."""
    monkeypatch.setattr(NodriverTeslaClient, "BASE_URL", f"{base_url}/api/findus")
    monkeypatch.setattr(
        NodriverTeslaClient, "PRICING_BASE_URL", f"{base_url}/findus/location/supercharger"
    )
    fetcher = NodriverBrowserFetcher()
    c = NodriverTeslaClient(fetcher=fetcher)
    try:
        yield c
    finally:
        fetcher.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fetch_locations_via_real_browser(client: NodriverTeslaClient) -> None:
    """JSON-Endpunkt: Status und Body koennen aus der echten Navigation."""
    locations = await client.fetch_locations("DE")
    assert len(locations) == 1
    assert locations[0]["location_url_slug"] == "testsupercharger"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fetch_pricing_html_keeps_next_data(client: NodriverTeslaClient) -> None:
    """HTML-Endpunkt: __NEXT_DATA__-Script-Inhalt bleibt erhaeltbar.

    Regression: Der Fallback auf ``document.body.innerText`` (sichtbarer
    Text) wuerde den ``__NEXT_DATA__``-Script-Inhalt verlieren und die
    Preis-Extraktion (``parse_pricing_tiers``) brechen.
    """
    html = await client.fetch_pricing_html("testsupercharger")
    assert "__NEXT_DATA__" in html
    tiers = parse_pricing_tiers(html)
    assert len(tiers) == 1
    assert tiers[0].currency == "EUR"
    assert tiers[0].amount == pytest.approx(0.40)
    assert tiers[0].unit == "kWh"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_non_200_statuses_raise_curl_error(client: NodriverTeslaClient) -> None:
    """403/429/404 loesen CurlError mit dem jeweiligen Statuscode aus."""
    fetcher: NodriverBrowserFetcher = client._fetcher  # type: ignore[assignment]
    origin = client.BASE_URL.rsplit("/api", 1)[0]
    for path, expected in (("/blocked", "403"), ("/throttled", "429"), ("/nope", "HTTP 404")):
        status, _body = fetcher.fetch(f"{origin}{path}")
        assert status in (403, 429, 404)
        with pytest.raises(CurlError, match=expected):
            await client._fetch(f"{origin}{path}")  # type: ignore[private]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_close_terminates_browser_and_thread() -> None:
    """close() beendet Browser-Subprozess und Hintergrund-Thread sauber.

    Regression: ``Browser.stop()`` ist synchron - ``run_coroutine_threadsafe(
    stop())`` wirft TypeError, der still geschluckt wurde; der Chromium-
    Subprozess lief weiter.

    Hinweis: ``_ensure_browser()`` und ``close()`` laufen hier in
    Worker-Threads (``asyncio.to_thread``) - genau wie in Produktion, wo
    ``NodriverTeslaClient._fetch`` per ``asyncio.to_thread`` aufruft. In
    einer laufenden Main-Loop blockiert ``concurrent.futures.Future.result()``
    die Loop und deadlocked mit dem asyncio-Child-Watcher beim Starten
    des Chromium-Subprozesses.
    """
    fetcher = NodriverBrowserFetcher()
    # Browser in der Loop-Thread starten (Worker-Thread wartet darauf).
    pid_box: list[int] = []
    ready = threading.Event()

    def start_browser() -> None:
        loop = fetcher._ensure_started()
        browser = asyncio.run_coroutine_threadsafe(fetcher._ensure_browser(), loop).result(
            timeout=60.0
        )
        pid_box.append(browser._process.pid)
        ready.set()

    try:
        await asyncio.to_thread(start_browser)
        assert ready.wait(timeout=5.0) and pid_box, "Browser startete nicht"
        pid = pid_box[0]

        # close() in Worker-Thread - wie in Produktion (to_thread in _fetch).
        await asyncio.to_thread(fetcher.close)
    finally:
        fetcher.close()  # idempotent; sichert Abbruch-Pfade ab

    assert fetcher._thread is None
    assert fetcher._browser is None
    # Chromium-Subprozess ist beendet (os.kill(pid, 0) wirft ProcessLookupError).
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            break
        time.sleep(0.2)
    else:
        pytest.fail("Chromium-Prozess laeuft weiterhin nach close()")
