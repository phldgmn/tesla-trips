# Tesla Supercharger via Locations-API

## Introduction
Tesla provides a public API to list all their locations. This can be used to filter out superchargers and get their details.

## API Endpoint

### get-locations

cURL-equivalent:
```bash
curl 'https://www.tesla.com/api/findus/get-locations?country=DE&view=map' \
  -H 'accept: application/json, text/plain, */*' \
  -H 'accept-language: de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7' \
  -H 'priority: u=1, i' \
  -H 'referer: https://www.tesla.com/de_de/findus?bounds=61.019610081973084%2C-61.13933008750001%2C6.759859256346627%2C-151.9303457125' \
  -H 'sec-ch-ua: "Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"' \
  -H 'sec-ch-ua-mobile: ?0' \
  -H 'sec-ch-ua-platform: "macOS"' \
  -H 'sec-fetch-dest: empty' \
  -H 'sec-fetch-mode: cors' \
  -H 'sec-fetch-site: same-origin' \
  -H 'user-agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36' \
  -b ''
```
*IMPORTANT:* During testing, we found out that no cookies are apparently required but that all these headers are necessary to get a valid response.

Response (example):
```json
{
  "data": {
    "data": [
      {
        "latitude": 26.878642,
        "longitude": -80.056113,
        "location_type": [
          "destination_charger"
        ],
        "location_url_slug": "dc13470",
        "title": "locations",
        "uuid": "1187174",
        "inCN": false,
        "inHkMoTw": false
      },
      {
        "latitude": 31.3631969,
        "longitude": 121.072243,
        "location_type": [
          "supercharger",
          "service",
          "sales"
        ],
        "location_url_slug": "kunshanchengdong",
        "title": "locations",
        "uuid": "1187176",
        "inCN": true,
        "inHkMoTw": false
      }
    ]
  }
}
```

### get-location-details

Returns the full detail record for a **single** location, identified by the slug
returned from `get-locations`.

cURL-equivalent:

```bash
curl 'https://www.tesla.com/api/findus/get-location-details?locationSlug=rhudensupercharger&functionTypes=party&locale=de_DE&isInHkMoTw=false' \
  -H 'accept: application/json, text/plain, */*' \
  -H 'accept-language: de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7' \
  -H 'priority: u=1, i' \
  -H 'referer: https://www.tesla.com/de_de/findus?bounds=52.49149886461671%2C11.323272090345412%2C51.12457982101295%2C8.486052852064162&search=Cologne&location=rhudensupercharger&functionType=party' \
  -H 'sec-ch-ua: "Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"' \
  -H 'sec-ch-ua-mobile: ?0' \
  -H 'sec-ch-ua-platform: "macOS"' \
  -H 'sec-fetch-dest: empty' \
  -H 'sec-fetch-mode: cors' \
  -H 'sec-fetch-site: same-origin' \
  -H 'user-agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36' \
  -H 'x-txid: 7514d856-0594-496e-8ad1-7f686892a0aa' \
  -b ''
```

#### Query parameters

| Parameter | Required | Example | Notes |
|---|---|---|---|
| `locationSlug` | yes | `rhudensupercharger`, `WolfsburgGermanysupercharger`, `30138` | The `location_url_slug` from `get-locations`, passed **verbatim**. Slugs are opaque: some are descriptive, some are pure numeric IDs, and casing is not normalised (`WolfsburgGermanysupercharger`). Never re-derive a slug from the title. |
| `functionTypes` | yes | `party` | Comma-separated list; mirrors the `functionType` parameter of the `findus` UI URL. Only `party` has been observed in practice — the exact semantics of other values are unverified. |
| `locale` | yes | `de_DE` | Underscore form of the `/de_de/` path segment used by the UI. Drives the language of all human-readable strings (address labels, opening-hour and amenity descriptions). Keep it constant across a crawl so cached records stay comparable. |
| `isInHkMoTw` | yes | `false` | Mirrors the `inHkMoTw` boolean from the corresponding `get-locations` entry. Pass through the value from the list response rather than hardcoding it. |

#### Headers

Identical to `get-locations`.

*IMPORTANT:* As with `get-locations`, no cookies are required, but the full set
of browser-like headers is. Requests originating from datacenter IP ranges are
rejected by the Akamai edge with `403 Access Denied` regardless of headers, so
crawls must run from a residential-style egress.

Response (example):

```json
{
  "data": {
    "marketing": {
      "display_driving_directions": true,
      "show_on_bodyshop_support_page": false,
      "show_on_find_us": true,
      "store_sub_region": {
        "display_name": "Germany",
        "locale": null
      },
      "display_name": "Rhüden Supercharger",
      "roadside_assistance_number": " +49 (0) 89 21093303",
      "address_notes": null,
      "gmaps_override": "https://maps.google.com/maps?daddr=51.947203,10.140477",
      "location_url_slug": "rhudensupercharger",
      "gmaps_override_longitude": 10.138943,
      "store_region": {
        "name": "europe"
      },
      "gmaps_override_latitude": 51.947254,
      "service_center_phone": null,
      "service_hours_by_appointment_only": "0",
      "store_hours_by_appointment_only": "0",
      "phone_numbers": null,
      "common_name": "MAXI Autohof Rhüden",
      "site_event": {
        "has_event": null
      }
    },
    "functions": [
      {
        "business_hours": {
          "hours": [
            {
              "close_hour": "22:00",
              "open_hour": "06:00",
              "day": "Monday",
              "holiday": null,
              "is_24_hours": false,
              "is_closed": false
            }
          ]
        },
        "address": {
          "country": "DE",
          "formatted_address": [
            "Am Zainer Berg 2",
            "38723 Seesen"
          ],
          "address_validated": false,
          "city": "Seesen",
          "address_1": "2 Am Zainer Berg",
          "address_2": "",
          "latitude": 51.94739,
          "county": null,
          "state_province": "",
          "locale": "en-US",
          "address_number": "2",
          "address_street": "Am Zainer Berg ",
          "district": null,
          "country_name": null,
          "address_provider_enum": "google",
          "postal_code": "38723",
          "postal_code_suffix": null,
          "longitude": 10.13962
        },
        "address_by_locale": [
          {
            "country": "DE",
            "formatted_address": [
              "Am Zainer Berg 2",
              "38723 Seesen"
            ],
            "address_validated": false,
            "city": "Seesen",
            "address_1": "2 Am Zainer Berg",
            "address_2": "",
            "latitude": 51.94739,
            "county": null,
            "state_province": "",
            "locale": "en-US",
            "address_number": "2",
            "address_street": "Am Zainer Berg ",
            "district": null,
            "country_name": null,
            "address_provider_enum": "google",
            "postal_code": "38723",
            "postal_code_suffix": null,
            "longitude": 10.13962
          }
        ],
        "customer_facing_name": "Rhüden - Self Serve Demo Drive",
        "translations": {
          "customerFacingName": {
            "en-US": "Rhüden - Self Serve Demo Drive"
          }
        },
        "name": "Self_Serve_Demo_Drive",
        "opening_date": "2026-03-02",
        "status": "Open"
      }
    ],
    "tesla_center_collision_function": {
      "company_phone": null
    },
    "key_data": {
      "address": {
        "country": "DE",
        "formatted_address": [
          "Am Zainer Berg 2",
          "38723 Seesen"
        ],
        "address_validated": false,
        "city": "Seesen",
        "address_1": "2 Am Zainer Berg",
        "address_2": "",
        "latitude": 51.94739,
        "county": null,
        "state_province": "",
        "locale": "en-US",
        "address_number": "2",
        "address_street": "Am Zainer Berg ",
        "district": null,
        "country_name": "Germany",
        "address_provider_enum": "google",
        "postal_code": "38723",
        "postal_code_suffix": null,
        "longitude": 10.13962
      },
      "phone": null,
      "local_address": null,
      "use_local_address": false,
      "geo_point": {
        "lon": 10.13962,
        "lat": 51.94739
      },
      "status": {
        "name": "Open"
      },
      "address_by_locale": [
        {
          "country": "DE",
          "city": "Seesen",
          "address_1": "2 Am Zainer Berg",
          "address_2": "",
          "latitude": 51.94739,
          "country_name": null,
          "state_province": "",
          "locale": "en-US",
          "postal_code": "38723",
          "longitude": 10.13962
        }
      ]
    },
    "supercharger_function": {
      "customer_facing_coming_soon_date": "",
      "actual_longitude": "10.140714",
      "access_type": "Public",
      "installed_full_power": "250",
      "vote_winner_quarter": null,
      "show_on_find_us": "1",
      "project_status": "Open",
      "actual_latitude": "51.947252",
      "open_to_non_tesla": true,
      "coming_soon_longitude": "10.140477",
      "site_status": "open",
      "coming_soon_name": null,
      "charging_accessibility": "All Vehicles (Production)",
      "coming_soon_latitude": "51.947203",
      "num_charger_stalls": "16"
    },
    "trtId": 1826
  }
}
```


## Connecting the two endpoints

`get-locations` is the *index*, `get-location-details` is the *record*. The join
key is `location_url_slug` → `locationSlug`; the secondary pass-through is
`inHkMoTw` → `isInHkMoTw`.

```text
get-locations?country=DE&view=map
        │
        │  filter: "supercharger" ∈ location_type
        │          inCN == false            (CN sites use a separate backend)
        ▼
  location_url_slug, inHkMoTw
        │
        │  one request per site
        ▼
get-location-details?locationSlug=<slug>&isInHkMoTw=<inHkMoTw>
                     &functionTypes=party&locale=de_DE
```

### Practical notes for the crawler

* **Cache aggressively.** Supercharger metadata changes on a scale of weeks, not
  minutes. Persist each detail record keyed by slug with a fetch timestamp and
  refresh on a fixed TTL (e.g. 30 days); the routing/charging optimiser should
  never hit the network.
* **Rate-limit and back off.** The detail endpoint needs one request per site
  (~2000 for DE alone). Serialise with a delay and treat `403` as "backed off,
  retry later", not as "site does not exist".
* **`uuid` vs. slug.** The list response carries both `uuid` and
  `location_url_slug`; the detail endpoint keys on the slug. Store both, and use
  `uuid` as the stable internal primary key in case a slug is renamed.
