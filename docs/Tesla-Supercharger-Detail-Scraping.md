# Tesla Supercharger Scraping — How It Works

## Overview

`tesla-pricing` is a CLI tool that estimates the real Tesla Supercharger cost of a road trip planned
in ABRP (A Better Route Planner). It does this by scraping two Tesla-owned data sources that plain
HTTP clients cannot access (Akamai WAF blocks curl/requests even with browser headers), plus one
public exchange-rate API.

**The pipeline, end to end:**

```
ABRP .xlsx  →  trip.py  →  locations.py (local directory cache)
                                    ↓
                            resolve.py (if station ID stale)
                                    ↓
                            fetch.py / safari_fetch.py (real browser → tesla.com)
                                    ↓
                            pricing.py (parse __NEXT_DATA__, cache result)
                                    ↓
                            estimate.py (compute cost per stop)
                                    ↓
                            write back to .xlsx
```

---

## 1. Input: ABRP Trip Export (.xlsx)

### Required format

A `.xlsx` exported from [ABRP](https://abetterrouteplanner.com/). The tool reads:

| Column | Purpose |
|---|---|
| `Name` / `Name des Wegpunkts` | Waypoint label, e.g. `Falkenberg, Sweden [Tesla]` |
| `Ankunfts-SoC` / `Arrival SoC` | Battery % upon arrival |
| `Abfahrts-SoC` / `Departure SoC` | Battery % upon departure |
| `Kosten` / `Cost` | Existing cost column — this tool writes into it for Tesla stops |
| `Ladekarte` / `Charge card` | Cleared for Tesla stops (Supercharger isn't billed through a card) |

A waypoint is identified as a Tesla Supercharger stop if its name ends with `[Tesla]` (ABRP's
convention). The `Tesla Supercharger` prefix is stripped before matching (see `locations.py`).

### How it's parsed (`trip.py`)

`parse_trip(path)` scans rows looking for a header row with known column names (German and English
variants supported via `_HEADER_SYNONYMS`). It returns a `Trip` dataclass containing:

- `stops: list[Stop]` — each with `row`, `name`, `soc_arrival`, `soc_departure`, and a computed
  `energy_kwh` (= `(soc_dep - soc_arr) / 100 × battery_capacity`)
- `source: Path`, `sheet_name: str`, `summary_row: int | None` (the ABRP totals row at the bottom)

---

## 2. Location Directory: `tesla.com/all-locations`

### Source

`https://www.tesla.com/all-locations?type=super_chargers` — Tesla's own global site map, served as
raw JSON (~17,500 entries).

### What it contains

Every Supercharger site with its metadata:

```json
{
  "location_id": "falkenbergsupercharger",
  "title": "Falkenberg",
  "city": "Falkenberg",
  "country": "Sweden",
  "latitude": 56.905,
  "longitude": 12.491,
  "stall_count": "24",
  "trt_id": "TRT00…"         // Tesla's internal stable identifier
}
```

Each is modelled as `Supercharger` in `locations.py` (`@dataclass(frozen=True)`).

### Fetching (`locations refresh`)

Uses a `FetchSession` (headless Chromium via Playwright, or Safari automation on macOS) with one
request — the entire all-locations blob comes as a single JSON payload.

Cached to `~/Library/Caches/tesla-pricing/superchargers.json` (via `platformdirs`).

### Searching the directory (`SuperchargerDirectory`)

The CLI command `trip estimate` and `trip update` both call `_load_directory()` which:

1. Reads the cached JSON file
2. Builds an in-memory `SuperchargerDirectory` — a list of `Supercharger` objects
3. When matching an ABRP stop name (e.g. `"Falkenberg, Sweden [Tesla]"`):
   - Strips `[Tesla]` suffix → `"Falkenberg, Sweden"`
   - Strips `Tesla Supercharger` prefix → `"Falkenberg, Sweden"`
   - Splits on `,` → city `"Falkenberg"`, country `"Sweden"`
   - Fuzzy-matches against the directory using `rapidfuzz` token ratio
   - Country-match bonus / cross-country penalty applied
   - Returns the best match with a confidence score, or `None` if below threshold

---

## 3. Per-Station Pricing: `tesla.com/de_de/findus/location/supercharger/<id>`

### Source

`https://www.tesla.com/de_de/findus/location/supercharger/{location_id}` — Tesla's
Next.js-powered station detail page. Pricing tiers are embedded in a
`<script id="__NEXT_DATA__">` JSON blob. The `de_de` locale path segment is required:
without it, Tesla serves a geo-/locale-dependent intermediate page lacking
`formattedData` for some locations (e.g. Swedish superchargers), even though the slug
itself is valid.

### How it's fetched

Two backends, both using **a real browser** (never a raw HTTP client):

#### a) Safari automation (`safari_fetch.py`) — macOS only, preferred

Drives the user's **already-running real Safari** via AppleScript. This is the important fix:
Akamai's WAF was blocking headless Chromium launched from the sandboxed shell (different network
path off the host), while the exact same URL loaded fine in the user's real browser.

- Opens one **dedicated new Safari window** (never touches the user's tabs)
- Navigates to the station URL, polls for page load completion via AppleScript's built-in wait
- Reuses the same window across an entire trip's station fetches
- Imposes a random 1.0–2.5s pause between navigations (human-like pacing)
- Reads the rendered page source via `do JavaScript "document.documentElement.outerHTML"`
- Raises `SafariUnavailable` if `osascript` isn't on PATH or Automation permission denied
  (naming the exact System Settings toggle)

#### b) Headless Chromium (`fetch.py`) — fallback on non-macOS

Uses Playwright to launch a headless Chromium browser:

- Fresh browser context per retry (new fingerprint, no carried-over cookies)
- Retries up to 4 times against Akamai's `Access Denied` / `errors.edgesuite.net` blocks,
  with exponential backoff (1.5s base)
- One `FetchSession` instance reused per trip run

#### Retry logic (`fetch.py`)

```python
_MAX_ATTEMPTS = 4
_RETRY_BASE_DELAY_S = 1.5
_BLOCK_MARKERS = ("Access Denied", "errors.edgesuite.net")
```

On each block, a fresh browser context is spawned (new fingerprint, no cookies). The `Reference #`
in the block page is extracted for debugging. After exhausting retries, `FetchBlocked` is raised.

#### Auto backend selection (`fetch.open_session(backend="auto")`)

1. Try Safari automation (`SafariSession`)
2. If `SafariUnavailable`, fall back to headless Chromium (`FetchSession`)
3. Never tries a raw HTTP fetch

### How it's parsed (`pricing.py`)

The returned HTML is parsed with a regex:

```python
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)
```

Then `json.loads()` is called on the captured blob. The relevant data lives at:

```
__NEXT_DATA__ → props → pageProps → formattedData → chargerPricing
```

Tesla's `chargerPricing` is an array of pricing tiers. Each tier has:

- A label (e.g. `"Charging Fees for Tesla Owner"`, `"Charging Fees for Other EV"`)
- A list of rate windows — each with a time label, and a rendered price string

The rendered price strings are parsed with:

```python
_RATE_RE = re.compile(r"([A-Z]{2,3}|\$|€|£)\s*([0-9]+(?:[.,][0-9]+)?)\s*/\s*(kWh|min)")
```

This captures currency token, numeric amount, and unit (kWh or min). Currency tokens include
ISO codes (SEK, DKK, NOK, CHF) and symbols ($, €, £).

Separate idle-fee text (e.g. `"$0.12/min idle"`) is also extracted per tier.

### Data model (`pricing.py`)

```
StationPricing
├── location_id: str
├── tiers: list[PricingTier]
│   ├── label: str
│   ├── windows: list[RateWindow]
│   │   ├── label: str (e.g. "4:00 PM - 8:00 PM")
│   │   ├── currency: str
│   │   ├── amount: float
│   │   └── unit: str ("kWh" or "min")
│   └── idle_fee: str | None
├── owner_rate_for_time(time_str: str | None) → RateWindow | None
│   — Selects the applicable Tesla-owner rate based on arrival time
└── to_json() / from_json() — serialization roundtrip
```

The `owner_rate_for_time` method:

1. Picks the first tier whose label suggests Tesla-owner pricing (contains `"Tesla Owner"`)
2. If a time is provided, finds the rate window whose label's time range covers that time
   (handles midnight-wrapping windows)
3. Falls back to the first (default) window if no time match or no owner tier found

### Caching (`pricing.py` / `paths.py`)

```
~/Library/Caches/tesla-pricing/pricing-cache/
├── {location_id}.json           — successful parse result
└── {location_id}.failure.json   — marker for failed fetch/parse
```

- **Success cache**: parsed `StationPricing` serialized to JSON. Reused across commands within
  the default 14-day max age (`MAX_AGE_DAYS_DEFAULT`).
- **Failure cache**: a `PricingFailure` marker with error message and `confirmed_404: bool`.
  - Confirmed-404 failures get a 1-hour TTL (`_TRANSIENT_FAILURE_MAX_AGE_DAYS`) — Tesla's 404s
    are often transient origin blips that resolve minutes later.
  - Other failures keep the caller's full `max_age_days`.
- `--force` flag bypasses the success cache entirely.
- On successful re-fetch after a failure, `clear_cached_failure()` removes the marker.

### Resolving stale location IDs (`resolve.py`)

Sometimes Tesla's bulk directory keeps a pre-launch placeholder slug long after the station opened
under a permanent numeric URL. Resolution tiers, tried in order:

1. **`probe_trt_id`**: Tesla's internal `trt_id` (from the directory) is often the real findus ID.
   Tries `trt_id` bare, then `{trt_id}0` through `{trt_id}9` by fetching each URL and checking for
   `__NEXT_DATA__` with pricing data + matching city name.
2. **`search_ddg`**: DuckDuckGo search for the station name + Tesla findus, scrapes the first
   result that matches. Requires the `ddgs` optional dependency.
3. **Persistent overrides**: discovered mappings saved to `resolved-location-ids.json` in the
   cache directory, so a known-broken ID is resolved immediately on re-runs.

---

## 4. Exchange Rates (`currency.py`)

### Source

`https://api.frankfurter.dev/v1/latest` — free, open exchange-rate API (frozen v1 endpoint).

- A `curl`-style User-Agent is required (Frankfurter sits behind Cloudflare which blocks
  `Python-urllib/x.y`)
- Called with `?base=EUR&symbols=SEK,DKK,NOK,...` — fetches rates for only the currencies actually
  needed (discovered by scanning every station's pricing tiers)

### Caching

```
~/Library/Caches/tesla-pricing/exchange-rates-EUR.json
```

- Cached per UTC day (`_today()` checks the cache file's date)
- One request per base currency covers the whole trip (rates gathered before conversion)

### Conversion (`convert_station_pricing`, `convert_estimates`)

- Every `RateWindow.amount` is divided by the rate: `amount / rate`
- Currency token is replaced with the target currency code
- `--currency` flag on every pricing/trip command (default `EUR`)

---

## 5. Cost Estimation (`estimate.py`)

### Per-stop cost

```python
amount = stop.energy_kwh * pricing.owner_rate_for_time(arrival_time).amount
```

Where `stop.energy_kwh = (soc_departure - soc_arrival) / 100 × battery_kwh`

Returns a `CostEstimate` per stop:

- `stop: Stop`, `station: Supercharger`, `pricing: StationPricing`
- `rate: float` (the per-kWh price), `amount: float` (computed cost)
- `currency: str`, `note: str` (e.g. `"45.00 kWh × 3.90 SEK/kWh"`)

Non-matching stops get `amount = None` and a note like `"No matching Tesla supercharger"`.

### Excel write-back (`update_excel`)

The `trip update` command writes computed prices **directly into the ABRP sheet**:

- **Kosten column**: filtered for `[Tesla]` stops only — writes the computed amount with an Excel
  currency number format (e.g. `#,##0.00" SEK"`), so it's a real numeric cell Excel can sum
- **Ladekarte column**: cleared for Tesla stops (Supercharger isn't billed through a charge card)
- **Rate column**: a new column `Tesla Preis/kWh` added next to the existing columns, showing the
  per-kWh rate that produced each price (also currency-formatted)
- **Totals row**: the trailing `∑ Kosten` cell is updated to include the new Tesla amounts
- **Total sheet**: a separate `Total` sheet is created/rebuilt with:
  - Laden (charging cost, formula-linked to the trip sheet's totals row)
  - Storebaelt / Øresund bridge tolls (configurable defaults: 56€ / 50€)
  - Fähre (ferry cost, default 0€)
  - Übernachtungen (overnight cost, default 0€)
  - Grand total (`SUM`)
  - Battery capacity minus degradation (default 60 kWh, 3% degradation)
- **Non-Tesla stops** are never touched

### Legacy header migration

The tool used to write the rate column under a different header (`Tesla Preis/kWh (ist)`).
When encountering a file with the old header, it renames/reuses that column in place rather than
adding a duplicate. If both old and new headers somehow exist (corrupted file from a mid-version
run), the old column is blanked.

---

## 6. Storage Layout

All runtime data lives in the OS user cache directory, never in the repo or the PyInstaller bundle.

```
~/Library/Caches/tesla-pricing/          (macOS)
├── superchargers.json                    — all-locations dump (~26 MB)
├── exchange-rates-EUR.json               — daily FX rates
├── resolved-location-ids.json            — stale → working ID mappings
└── pricing-cache/
    ├── falkenbergsupercharger.json       — parsed station pricing
    ├── falkenbergsupercharger.failure.json
    ├── kamensupercharger.json
    └── ...
```

Managed by `paths.py`:

- `data_dir()` — creates cache dir via `platformdirs.user_cache_dir("tesla-pricing")`
- `locations_file()`, `pricing_cache_file(id)`, `pricing_failure_file(id)`,
  `exchange_rate_cache_file(base)`, `resolved_ids_file()`

Serialization is done with `@dataclass(frozen=True)` + manual `to_json()`/`from_json()` methods
on `StationPricing` — no ORM, no SQLite.

---

## 7. CLI Command Flow

```
tesla-pricing locations refresh          — fetch superchargers.json
tesla-pricing locations path             — print cache path
tesla-pricing pricing fetch <city>       — one station, print JSON
tesla-pricing pricing path               — print pricing cache path
tesla-pricing trip estimate <xlsx>       — dry-run: print costs per stop
tesla-pricing trip update <xlsx>         — write costs into the .xlsx
```

### `trip estimate` internal flow (the core workflow)

1. Parse ABRP `.xlsx` → `Trip` with `[Tesla]` stops
2. Load `SuperchargerDirectory` from cached `superchargers.json`
3. For each `[Tesla]` stop:
   a. Fuzzy-match against directory → `Supercharger` with `location_id`
   b. Check pricing cache (JSON file, respects `--max-age` and `--force`)
   c. If cache miss: try `resolve_location` (probe trt_id → DuckDuckGo)
   d. If resolved (or already known): fetch via real browser → parse `__NEXT_DATA__` → cache result
   e. If fetch/parse fails: cache failure marker, skip stop
4. (Optionally) gather all currencies, fetch exchange rates, convert pricing
5. Compute `CostEstimate` per stop
6. Print or write results

---

## 8. Error Handling Summary

| Failure mode | Handling | TTL |
|---|---|---|
| Akamai block (Access Denied) | Retry up to 4× with fresh browser context | — |
| Safari unavailable (non-macOS / no permission) | Fall back to headless Chromium | — |
| Chromium not installed | Clear `FetchUnavailable` error naming the missing step | — |
| Tesla 404 on station page | Cached as `PricingFailure(confirmed_404=True)` | 1 hour (transient) |
| No `__NEXT_DATA__` / missing pricing block | `PricingParseError` (not a 404) | Full `max_age_days` |
| Station not in directory | Fuzzy match returns `None` → skip with note | — |
| Exchange rate missing | `ExchangeRateError` | — |
| Stale location ID | `resolve_location` probes alternatives, caches override | Persistent |
