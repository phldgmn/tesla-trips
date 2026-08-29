"""Integrationstests fuer den Human-Flow-Client (echter Chromium via CDP).

Nutzt einen echten (headlosen) Chromium-Browser gegen einen lokalen
HTTP-Server, der eine findus-Kartenseiten-Attrappe (Such-Input +
Client-JS) serviert - keine Live-Requests gegen externe Dienste (siehe
AGENTS.md). Deckt den Teil ab, den die Unit-Tests in ``test_client.py``
(injizierter Fake-Fetcher) nicht erreichen koennen: dass das echte
Zeichen-fuer-Zeichen-Tippen in ``input.tds-form-input-search`` per CDP
tatsaechlich ein ``input``-Event ausloest, das die Seiten-JS zu einem
abfangbaren ``fetch`` bewegt - und dass die Ergebnisauswahl-Navigation
(URL mit ``location``-Query-Parameter) die beiden Detail-Fetches ausloest.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tripplanner.charging_infrastructure.client import (
    NodriverBrowserFetcher,
    NodriverHumanFlowTeslaClient,
)

SEARCH_JSON = {
    "data": [{"location_url_slug": "testsupercharger", "title": "Test City Supercharger"}]
}
LOCATION_DETAILS_JSON = {"data": {"marketing": {"display_name": "Test City Supercharger"}}}
CHARGER_DETAILS_JSON = {"data": {"stall_count": 8}}

_FINDUS_PAGE = """<!doctype html>
<html>
<body>
<input type="text" class="tds-form-input-search" />
<script>
(function () {
  var input = document.querySelector('.tds-form-input-search');
  input.addEventListener('input', function () {
    fetch('/api/findus/search?q=' + encodeURIComponent(input.value));
  });
  var params = new URLSearchParams(window.location.search);
  var loc = params.get('location');
  if (loc) {
    fetch('/api/findus/get-location-details?locationSlug=' + encodeURIComponent(loc)
      + '&functionTypes=party');
    fetch('/api/findus/get-charger-details?locationSlug=' + encodeURIComponent(loc)
      + '&programType=supercharger');
  }
})();
</script>
</body>
</html>"""


class _HumanFlowHandler(BaseHTTPRequestHandler):
    """Bildet die fuer den Human-Flow relevanten findus-Endpunkte nach."""

    def do_GET(self) -> None:
        if self.path.startswith("/de_de/findus"):
            self._send(200, "text/html", _FINDUS_PAGE)
        elif self.path.startswith("/api/findus/search"):
            self._send(200, "application/json", json.dumps(SEARCH_JSON))
        elif self.path.startswith("/api/findus/get-location-details"):
            self._send(200, "application/json", json.dumps(LOCATION_DETAILS_JSON))
        elif self.path.startswith("/api/findus/get-charger-details"):
            self._send(200, "application/json", json.dumps(CHARGER_DETAILS_JSON))
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
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HumanFlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5.0)


@pytest.fixture
def client(base_url: str) -> NodriverHumanFlowTeslaClient:
    """``NodriverHumanFlowTeslaClient`` mit echtem Fetcher gegen den lokalen Server."""
    fetcher = NodriverBrowserFetcher()
    c = NodriverHumanFlowTeslaClient(fetcher=fetcher, base_url=base_url)
    try:
        yield c
    finally:
        fetcher.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_locations_via_real_browser(client: NodriverHumanFlowTeslaClient) -> None:
    """Zeichenweises Tippen in ``input.tds-form-input-search`` loest die
    Such-XHR aus und deren JSON-Antwort wird abgefangen."""
    results = await client.search_locations("Test City", locale="de_DE")
    assert results == SEARCH_JSON["data"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fetch_location_details_via_real_browser(
    client: NodriverHumanFlowTeslaClient,
) -> None:
    """Suche -> Ergebnisauswahl-Navigation loest beide Detail-Fetches aus;
    beide Antworten werden abgefangen und zusammengefuehrt."""
    detail = await client.fetch_location_details(
        "testsupercharger", locale="de_DE", search_query="Test City"
    )
    assert detail["marketing"]["display_name"] == "Test City Supercharger"
    assert detail["_charger_details"] == {"stall_count": 8}
