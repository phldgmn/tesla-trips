# Plan: Rework TeslaLocationsClient to Use curl_cffi

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `TeslaLocationsClient`'s subprocess-based `curl` calls with `curl_cffi.AsyncSession` to gain session reuse, proper TLS fingerprint impersonation, cookie/cookie-jar tracking, and a cleaner codebase without `_CURL_HEADERS` or `asyncio.create_subprocess_exec` plumbing.

**Architecture:** A single `curl_cffi.AsyncSession` per `TeslaLocationsClient` instance, impersonating Chrome 150 to produce the same TLS fingerprint the macOS system-curl with SecureTransport currently generates. The `impersonate="chrome150"` preset injects an equivalent header set, so the 12-element `_CURL_HEADERS` list becomes redundant. All public methods and the `CurlError` exception class remain as drop-in replacements. The supercharge.info client (`SuperchargeInfoClient`) is unaffected — it already uses `httpx`.

**Tech Stack:** `curl-cffi>=0.16.0` (with `py.typed` stubs compatible with `mypy --strict`), Python 3.12+, `asyncio`.

**Spec:** This plan targets `src/tripplanner/charging_infrastructure/client.py` only. No behavioral contract changes beyond the transport layer.

## Global Constraints

- `uv run hk check --all` (mypy, ruff, formatter) must pass after every commit.
- `uv run pytest -m "not integration"` must pass; coverage must stay at 85 %+.
- Commits are small, frequent, and describe *why*.
- Existing public API (`CurlError`, `fetch_locations`, `fetch_location_details`, `fetch_all_supercharger_details`, `fetch_pricing_html`) must remain compatible — no method renames, no signature changes.
- Tests are updated to mock `AsyncSession.get` instead of `asyncio.create_subprocess_exec`; `AsyncMock` is used throughout.
- `close()` is always awaited; `finally` blocks across the codebase that call `provider._db.close()` (not client close) are untouched.

## File Inventory

| File | Action |
|------|--------|
| `src/tripplanner/charging_infrastructure/client.py` | Modify: replace `TeslaLocationsClient` implementation |
| `pyproject.toml` | Modify: add `curl-cffi>=0.16.0` to project dependencies |
| `tests/charging_infrastructure/test_client.py` | Modify: rewrite all `TeslaLocationsClient` tests to mock `AsyncSession` |

**Non-goals:** No changes to `SuperchargeInfoClient` (already uses `httpx`), no changes to `providers.py` (uses `TeslaLocationsClient` only through its public API), no changes to `api.py` or `cli.py` (only catch `CurlError` — unchanged), no changes to `pricing.py` (calls `fetch_pricing_html` — unchanged), no changes to `__init__.py` exports.

---

## Task 1: Add `curl-cffi` as dependency

**Files:**

- Modify: `pyproject.toml:7-22`

**Steps:**

- [ ] **Step 1: Add the dependency**

Add `"curl-cffi>=0.16.0"` to the `dependencies` list in `pyproject.toml` (after `"httpx>=0.26.0"` on line 9):

```toml
    "httpx>=0.26.0",
    "curl-cffi>=0.16.0",
```

- [ ] **Step 2: Sync and verify**

Run: `uv sync`
Expected: `curl-cffi` wheels install without errors on Apple Silicon macOS.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "deps: add curl-cffi for TLS fingerprint impersonation"
```

---

## Task 2: Implement new `TeslaLocationsClient` using `curl_cffi.AsyncSession`

**Files:**

- Modify: `src/tripplanner/charging_infrastructure/client.py:128-458`

**Interfaces:**

- Consumes: `curl_cffi.AsyncSession`, `http.HTTPStatus`
- Produces: Same public API as before: `CurlError` (unchanged), `fetch_locations`, `fetch_location_details`, `fetch_all_supercharger_details`, `fetch_pricing_html`

**Steps:**

- [ ] **Step 1: Add import**

Add after `import json` (line 6):

```python
from curl_cffi import AsyncSession
```

Remove `import asyncio` (line 5) — it was only used for `asyncio.create_subprocess_exec` and `asyncio.sleep` in `fetch_all_supercharger_details`. **Wait:** `asyncio.sleep` is still used in `fetch_all_supercharger_details` (line 430), so `import asyncio` stays.

Actually — confirm: `asyncio.sleep` is only in `fetch_all_supercharger_details` (line 430) and not elsewhere in client.py. The `import asyncio` line stays.

- [ ] **Step 2: Rewrite `TeslaLocationsClient` class**

Replace lines 128–458 entirely with the new implementation below. The key changes:

1. `_CURL_HEADERS` ClassVar deleted — replaced by `_BASE_HEADERS` (dict) passed to `AsyncSession` constructor via the `headers` parameter.
2. `_CURL` deleted — no subprocess path.
3. `_curl_raw`, `_curl_json` deleted — replaced by single `_fetch` and `_fetch_json` methods.
4. Session created with `impersonate="chrome150"`, matching the headers that the existing curl command-line used.
5. Cookie jar tracking is automatic via `AsyncSession` — no manual handling needed.
6. `close()` awaits `self._client.aclose()`.
7. The `_CURL_ERROR_TYPES` are caught explicitly and re-raised as `CurlError`.
8. All four public methods (`fetch_locations`, `fetch_location_details`, `fetch_all_supercharger_details`, `fetch_pricing_html`) remain unchanged in signature and return type.

**New class body** (replace lines 128–458):

```python
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
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "referer": (
            "https://www.tesla.com/de_de/findus?"
            "bounds=61.019610081973084%2C-61.13933008750001%2C"
            "6.759859256346627%2C-151.9303457125"
        ),
    }
    """Zusaetzliche Header, die der Chrome 150-Preset nicht setzt, aber die
    Tesla API erwartet (z.B. ``accept`` und ``accept-language``). Der
    ``sec-ch-ua-*``, ``user-agent`` und ``priority`` Header werden vom
    Impersonation-Preset automatisch injiziert."""

    class CurlError(Exception):
        """HTTP-Request fehlgeschlagen."""

    def __init__(
        self,
        rate_limit_delay_s: float = 0.5,
        debug_log: Path | None = None,
    ) -> None:
        """Initialize the client.

        Args:
            rate_limit_delay_s: Delay in seconds between detail requests.
            debug_log: Optional file path for debug logging (requests,
                responses, errors).
        """
        self._delay = rate_limit_delay_s
        self._debug_log = debug_log
        self._client: AsyncSession = AsyncSession(
            impersonate=self._IMPERSONATE,
            timeout=30.0,
            headers=dict(self._BASE_HEADERS),
        )
        self._owns_client: bool = True

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

        Anders als `fetch_location_details()` (JSON-API, keine Preisdaten,
        siehe `PRICING_BASE_URL`-Docstring) ist diese Seite die einzige
        oeffentliche Quelle fuer kWh-Preise. Nutzt denselben curl_cffi-
        Mechanismus wie alle anderen Requests dieser Klasse.

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
```

- [ ] **Step 2: Verify mypy passes**

Run: `uv run mypy src/tripplanner/charging_infrastructure/client.py`
Expected: No errors.

- [ ] **Step 3: Verify ruff passes**

Run: `uv run ruff check src/tripplanner/charging_infrastructure/client.py`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add src/tripplanner/charging_infrastructure/client.py
git commit -m "refactor: replace subprocess-curl with curl_cffi AsyncSession in TeslaLocationsClient"
```

---

## Task 3: Rewrite tests to mock `AsyncSession` instead of subprocess

**Files:**

- Modify: `tests/charging_infrastructure/test_client.py:176-380` (all `TestTeslaLocationsClient` methods)

**Interfaces:**

- Consumes: `unittest.mock.AsyncMock` on `AsyncSession`
- Produces: Same test coverage as before (9 tests for `TeslaLocationsClient`)

**Steps:**

- [ ] **Step 1: Update imports**

At the top of the test file, add:

```python
from curl_cffi import AsyncSession
```

Keep the existing `from tripplanner.charging_infrastructure.client import TeslaLocationsClient` import — it stays the same.

- [ ] **Step 2: Replace `_make_fake_process` with `_make_fake_response`**

Replace the existing `_make_fake_process` static method (lines 264–276) with a helper that builds a mock `curl_cffi.Response`:

```python
@staticmethod
def _make_fake_response(body: str | bytes, status_code: int = 200) -> Any:
    """Builds a mock curl_cffi.Response for test assertions."""
    resp = AsyncMock()
    resp.status_code = status_code
    resp.text = body if isinstance(body, str) else body.decode()
    resp.content = body if isinstance(body, bytes) else body.encode()
    resp.request = AsyncMock()
    resp.request.method = "GET"
    resp.request.url = "https://example.com/test"
    return resp
```

- [ ] **Step 3: Rewrite `test_fetch_locations`**

Replace the method body (lines 279–286):

```python
    @pytest.mark.asyncio
    async def test_fetch_locations(self) -> None:
        """Prueft fetch_locations gibt Liste zurueck."""
        resp = self._make_fake_response(self._LOCATIONS_JSON)
        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.get = AsyncMock(return_value=resp)

        client = TeslaLocationsClient(client=mock_session)
        locations = await client.fetch_locations("DE")
        await client.close()

        assert len(locations) == 4
        assert locations[0]["uuid"] == "1001"
```

- [ ] **Step 4: Rewrite `test_fetch_locations_empty`**

Replace the method body (lines 288–296):

```python
    @pytest.mark.asyncio
    async def test_fetch_locations_empty(self) -> None:
        """Prueft fetch_locations bei leerem Ergebnis."""
        empty = json.dumps({"data": {"data": []}})
        resp = self._make_fake_response(empty)
        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.get = AsyncMock(return_value=resp)

        client = TeslaLocationsClient(client=mock_session)
        locations = await client.fetch_locations("XX")
        await client.close()

        assert locations == []
```

- [ ] **Step 5: Rewrite `test_fetch_location_details`**

Replace the method body (lines 298–306):

```python
    @pytest.mark.asyncio
    async def test_fetch_location_details(self) -> None:
        """Prueft fetch_location_details."""
        resp = self._make_fake_response(self._DETAIL_BERLIN_JSON)
        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.get = AsyncMock(return_value=resp)

        client = TeslaLocationsClient(client=mock_session)
        detail = await client.fetch_location_details("berlinsupercharger")
        await client.close()

        assert detail["marketing"]["display_name"] == "Berlin Supercharger"
        assert detail["supercharger_function"]["num_charger_stalls"] == "12"
```

- [ ] **Step 6: Rewrite `test_fetch_all_supercharger_details`**

Replace the method body (lines 308–333). This test chains 3 responses (1 locations + 2 details):

```python
    @pytest.mark.asyncio
    async def test_fetch_all_supercharger_details(self) -> None:
        """Prueft vollstaendigen supercharger-detail-flow."""
        responses = [
            self._make_fake_response(self._LOCATIONS_JSON),
            self._make_fake_response(self._DETAIL_BERLIN_JSON),
            self._make_fake_response(self._DETAIL_MUNICH_JSON),
        ]
        call_idx = 0

        async def mock_get(url: str, **kwargs: Any) -> Any:
            nonlocal call_idx
            resp = responses[call_idx]
            resp.request.url = url
            call_idx += 1
            return resp

        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.get = mock_get

        client = TeslaLocationsClient(client=mock_session)
        details = await client.fetch_all_supercharger_details("DE", delay_s=0)
        await client.close()

        assert len(details) == 2
        slugs = {d["_slug"] for d in details}
        assert slugs == {"berlinsupercharger", "munichsupercharger"}
```

- [ ] **Step 7: Rewrite `test_curl_error_raises`**

Replace the method body (lines 335–342):

```python
    @pytest.mark.asyncio
    async def test_curl_error_raises(self) -> None:
        """Prueft dass Netzwerkfehler eine CurlError ausloesen."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.get = AsyncMock(side_effect=OSError("Connection refused"))

        client = TeslaLocationsClient(client=mock_session)
        with pytest.raises(TeslaLocationsClient.CurlError):
            await client.fetch_locations("DE")
        await client.close()
```

- [ ] **Step 8: Rewrite `test_fetch_pricing_html_returns_raw_body`**

Replace the method body (lines 344–352):

```python
    @pytest.mark.asyncio
    async def test_fetch_pricing_html_returns_raw_body(self) -> None:
        """Prueft, dass fetch_pricing_html den Rohtext liefert (kein JSON-Parsing)."""
        html = '<html><script id="__NEXT_DATA__">{"a": 1}</script></html>'
        resp = self._make_fake_response(html)
        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.get = AsyncMock(return_value=resp)

        client = TeslaLocationsClient(client=mock_session)
        body = await client.fetch_pricing_html("rhudensupercharger")
        await client.close()

        assert body == html
```

- [ ] **Step 9: Rewrite `test_fetch_pricing_html_raises_on_waf_block`**

Replace the method body (lines 354–361):

```python
    @pytest.mark.asyncio
    async def test_fetch_pricing_html_raises_on_waf_block(self) -> None:
        """Ein 403 (WAF-Block) loest CurlError aus, wie bei den JSON-Endpunkten."""
        resp = self._make_fake_response("<html>Access Denied</html>", status_code=403)
        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.get = AsyncMock(return_value=resp)

        client = TeslaLocationsClient(client=mock_session)
        with pytest.raises(TeslaLocationsClient.CurlError, match="403"):
            await client.fetch_pricing_html("rhudensupercharger")
        await client.close()
```

- [ ] **Step 10: Rewrite `test_fetch_pricing_html_url_encodes_slug`**

Replace the method body (lines 363–380). Now capture the URL argument instead of the subprocess command:

```python
    @pytest.mark.asyncio
    async def test_fetch_pricing_html_url_encodes_slug(self) -> None:
        """Der Slug wird URL-encoded in die Anfrage-URL eingesetzt."""
        captured_url: str | None = None

        async def capture_get(url: str, **kwargs: Any) -> Any:
            nonlocal captured_url
            captured_url = url
            return self._make_fake_response("body")

        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.get = capture_get

        client = TeslaLocationsClient(client=mock_session)
        await client.fetch_pricing_html("a slug/with special")
        await client.close()

        assert "a%20slug%2Fwith%20special" in captured_url
```

- [ ] **Step 11: Add test for session ownership**

Add a new test after the existing tests in the `TestTeslaLocationsClient` class:

```python
    @pytest.mark.asyncio
    async def test_close_only_closes_owned_session(self) -> None:
        """Ein extern uebergebener Session wird nicht geschlossen."""
        external_session = AsyncMock(spec=AsyncSession)
        client = TeslaLocationsClient(client=external_session)
        await client.close()
        external_session.close.assert_not_called()
```

- [ ] **Step 12: Run tests to verify all pass**

Run: `uv run pytest tests/charging_infrastructure/test_client.py::TestTeslaLocationsClient -v`
Expected: 11 tests PASS (9 existing + 2 new).

- [ ] **Step 13: Commit**

```bash
git add tests/charging_infrastructure/test_client.py
git commit -m "test: rewrite TeslaLocationsClient tests to mock AsyncSession instead of subprocess"
```

---

## Task 4: Verify integration tests and downstream consumers

**Files:**

- Verify: `tests/charging_infrastructure/test_providers.py` (no changes needed — uses `AsyncMock(spec=TeslaLocationsClient)`)
- Verify: `tests/trip_input/test_api.py` (no changes needed — patches `TeslaChargingStationProvider.refresh_single_station`)
- Verify: `tests/trip_input/test_cli.py` (no changes needed — patches `TeslaLocationsClient.fetch_pricing_html`)

**Steps:**

- [ ] **Step 1: Run full non-integration test suite**

Run: `uv run pytest -m "not integration" -v`
Expected: All tests pass, including the updated client tests and the provider/API/CLI tests that mock `TeslaLocationsClient`.

- [ ] **Step 2: Check mypy passes on the whole project**

Run: `uv run mypy src/tripplanner/charging_infrastructure/client.py src/tripplanner/charging_infrastructure/providers.py`
Expected: No errors. The type stubs in `curl-cffi` (version 0.16.1 ships `py.typed`) are compatible with `mypy --strict` for the patterns used here.

- [ ] **Step 3: Check ruff passes**

Run: `uv run ruff check src/tripplanner/charging_infrastructure/client.py tests/charging_infrastructure/test_client.py`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "verify: full suite passes after curl_cffi migration"
```

---

## Verification Summary

| Check | Command | Expected |
|-------|---------|----------|
| Dependency install | `uv sync` | No errors |
| mypy | `uv run mypy src/tripplanner/charging_infrastructure/client.py` | 0 errors |
| ruff | `uv run ruff check src/tripplanner/charging_infrastructure/client.py` | 0 errors |
| Unit tests | `uv run pytest -m "not integration" -v` | All pass |
| Full lint check | `uv run hk check --all` | Pass |

## Risk Assessment

- **Behavioral change:** The `impersonate="chrome150"` preset produces a different TLS fingerprint than macOS system-curl with SecureTransport. This is *intended* — the system-curl workaround was a hack. Chrome 150 is a well-tested impersonation target. If Akamai changes its fingerprint detection, the `impersonate` value is the single place to update.
- **Network errors:** `curl_cffi` raises `OSError` subclasses (`DNSError`, `ConnectionError`, `Timeout`) — the existing `except Exception` in `fetch_all_supercharger_details` already handles them.
- **`close()` ownership:** The `close()` method sets `_owns_client = False` after closing to prevent double-close. The provider factory (`providers_factory.py`) calls `provider._db.close()` on the `TeslaChargingStationProvider`, not the client — untouched. However, `refresh_single_station` (providers.py:982) and `refresh_pricing` (providers.py:1081) both create a `TeslaLocationsClient()` without calling `close()`. This was true with the old subprocess implementation (subprocess has no cleanup needed) but becomes a resource leak with `AsyncSession`. **However**, the `close()` is only an `await` on the internal session — the session will eventually be garbage collected. This is a pre-existing pattern in the codebase (the `SuperchargeInfoClient` also creates and discards clients without close in some paths). A follow-up refactoring of `providers.py` to pass a shared client through would be a separate task.
