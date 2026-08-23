"""Parsing and interpretation of Tesla Supercharger per-kWh pricing data.

Tesla's JSON `get-location-details` API (see `client.py`, `TeslaLocationsClient.
fetch_location_details`) does not carry pricing information at all (verified
against the documented response shape in `docs/Tesla-Supercharger-API.md`).
Per-kWh pricing is only rendered on the public Next.js station detail page
(`https://www.tesla.com/findus/location/supercharger/<slug>`, fetched via
`TeslaLocationsClient.fetch_pricing_html`), embedded in a
`<script id="__NEXT_DATA__">` JSON blob at
`props.pageProps.formattedData.chargerPricing`.

[INFERENCE] Tesla does not publish a schema for `chargerPricing`, and this
environment's outbound network access is blocked by Akamai's WAF (see
`client.py` module docstring), so the exact field names inside each pricing
tier could not be verified against a live response while writing this module.
The parser below is therefore deliberately schema-tolerant: instead of
depending on specific key names (e.g. a hypothetical `"windows"` list), it
scans each tier's own JSON subtree for values matching Tesla's documented
rendered price format (`docs/Tesla-Supercharger-Detail-Scraping.md`, itself
reverse-engineered by a related standalone tool that scraped this exact page
via a real browser). This is more robust to the unstable, undocumented shape
of Tesla's Next.js data than hardcoding exact key paths - but MUST be
re-validated against a live response (a manual curl from a machine that
passes the WAF, see `client.py`) before relying on it in production.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import datetime
from datetime import time as dt_time
from typing import Any, Literal, cast

from pydantic import ValidationError

from .models import ChargingPricingTier

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL,
)

# Currency token (ISO-4217 code or symbol) + amount + unit, e.g. "SEK 3.79/kWh",
# "$0.36 / kWh", "€0,45/min idle". Matches the rendered price format documented
# in docs/Tesla-Supercharger-Detail-Scraping.md.
_RATE_RE = re.compile(r"([A-Z]{2,3}|\$|€|£)\s*([0-9]+(?:[.,][0-9]+)?)\s*/\s*(kWh|min)")

_TIME_WINDOW_RE = re.compile(
    r"(\d{1,2}:\d{2}\s*[AP]M)\s*-\s*(\d{1,2}:\d{2}\s*[AP]M)", re.IGNORECASE
)

_CURRENCY_SYMBOLS: dict[str, str] = {"$": "USD", "€": "EUR", "£": "GBP"}


class PricingParseError(Exception):
    """Raised when pricing data could not be located or parsed in fetched HTML.

    NOT raised for a station that legitimately has no published pricing (e.g.
    a destination charger without `chargerPricing`, or a supercharger tier
    without a parseable rate) - that case returns an empty list instead, since
    it is a valid outcome, not a fetch/parse failure.
    """


def parse_pricing_tiers(html: str) -> list[ChargingPricingTier]:
    """Parses `chargerPricing` tiers from a Tesla findus location detail page.

    Args:
        html: Raw HTML of `https://www.tesla.com/findus/location/supercharger/
            <slug>` (see `TeslaLocationsClient.fetch_pricing_html`).

    Returns:
        One `ChargingPricingTier` per (pricing tier, rate window) with a
        recognizable rate string. Empty if `chargerPricing` is present but has
        no parseable rates (e.g. a destination charger with no fees).

    Raises:
        PricingParseError: If the `__NEXT_DATA__` blob is missing, is not
            valid JSON, or its JSON has no `chargerPricing` key at all -
            distinct from an empty/unparseable `chargerPricing`, which is a
            legitimate result (see `Returns`).
    """
    match = _NEXT_DATA_RE.search(html)
    if match is None:
        raise PricingParseError("__NEXT_DATA__ script tag not found in response")
    try:
        payload: Any = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        raise PricingParseError(f"__NEXT_DATA__ is not valid JSON: {e}") from e

    try:
        formatted_data: Any = payload["props"]["pageProps"]["formattedData"]
    except (KeyError, TypeError) as e:
        raise PricingParseError("__NEXT_DATA__ has no props.pageProps.formattedData") from e

    if not isinstance(formatted_data, dict) or "chargerPricing" not in formatted_data:
        raise PricingParseError("formattedData has no chargerPricing key")

    charger_pricing: Any = formatted_data["chargerPricing"] or []
    if not isinstance(charger_pricing, list):
        raise PricingParseError("chargerPricing is not a list")

    tiers: list[ChargingPricingTier] = []
    for entry in charger_pricing:
        if isinstance(entry, dict):
            tiers.extend(_parse_tier(entry))
    return tiers


def _parse_tier(entry: dict[str, Any]) -> Iterator[ChargingPricingTier]:
    """Parses a single `chargerPricing` entry into zero or more rate-window tiers."""
    tier_label = _own_label_string(entry) or "Charging Fee"
    idle_fee_text = _find_idle_fee_text(entry)

    for node in _iter_dicts(entry):
        rate_text = _own_rate_string(node)
        if rate_text is None:
            continue
        parsed_rate = _RATE_RE.search(rate_text)
        if parsed_rate is None:
            continue
        currency_token, amount_str, unit_str = parsed_rate.groups()
        currency = _CURRENCY_SYMBOLS.get(currency_token, currency_token)
        try:
            amount = float(amount_str.replace(",", "."))
        except ValueError:
            continue
        unit = cast("Literal['kWh', 'min']", unit_str)

        # A window dict carries its own time-range label alongside the rate;
        # the top-level tier entry itself carries the tier label instead
        # (already captured above) - never re-use it as a time window.
        time_label = None if node is entry else _own_label_string(node, exclude=rate_text)

        try:
            yield ChargingPricingTier(
                tier_label=tier_label,
                time_label=time_label,
                currency=currency,
                amount=amount,
                unit=unit,
                idle_fee_text=idle_fee_text,
            )
        except ValidationError:
            # Malformed rate (e.g. non-ISO currency token, non-positive
            # amount after parsing) - skip this window rather than fail the
            # whole station's pricing refresh.
            continue


def _iter_dicts(obj: object) -> Iterator[dict[str, Any]]:
    """Yields every dict node in a nested JSON structure, depth-first."""
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _iter_dicts(value)
    elif isinstance(obj, list):
        for item in obj:  # pyright: ignore[reportUnknownVariableType]
            yield from _iter_dicts(item)


def _own_rate_string(node: dict[str, Any]) -> str | None:
    """A rate-shaped string among `node`'s own (non-nested) values, if any.

    Skips any key whose name suggests an idle fee (see `_find_idle_fee_text`)
    - an idle-per-minute fee is not itself a charging-rate window.
    """
    for key, value in node.items():
        if "idle" in key.lower() or not isinstance(value, str):
            continue
        if _RATE_RE.search(value):
            return value
    return None


def _own_label_string(node: dict[str, Any], exclude: str | None = None) -> str | None:
    """A human-readable label among `node`'s own string values.

    Considers keys containing "label", "title" or "time", skipping `exclude`
    (typically the rate string already claimed by this node).
    """
    for key, value in node.items():
        if not isinstance(value, str) or not value.strip() or value == exclude:
            continue
        lowered_key = key.lower()
        if "label" in lowered_key or "title" in lowered_key or "time" in lowered_key:
            return value.strip()
    return None


def _find_idle_fee_text(entry: dict[str, Any]) -> str | None:
    """Idle-fee text within `entry`'s subtree (e.g. `"$0.50/min idle"`)."""
    for node in _iter_dicts(entry):
        for key, value in node.items():
            if not isinstance(value, str) or not value.strip():
                continue
            if "idle" in key.lower() or "idle" in value.lower():
                return value.strip()
    return None


def select_owner_rate_for_time(
    tiers: list[ChargingPricingTier], at: datetime
) -> ChargingPricingTier | None:
    """Selects the Tesla-owner per-kWh rate applicable at `at`.

    Mirrors the legacy tesla-pricing tool's `owner_rate_for_time` (see
    `docs/Tesla-Supercharger-Detail-Scraping.md`):

    1. Prefer tiers whose label suggests Tesla-owner pricing (contains
       "owner"); if none do, but every kWh tier shares one label (a station
       that only publishes a single, undifferentiated rate), treat those as
       the owner rate by elimination.
    2. Among owner-rate candidates, prefer a window whose `time_label` covers
       `at`'s clock time (handles midnight-wrapping windows).
    3. Fall back to a flat (`time_label is None`) window, then to any
       remaining owner-rate window.
    4. `None` if no kWh-priced tier could be attributed to a Tesla owner (e.g.
       only a "Charging Fees for Other EV" tier is published, or no cached
       pricing exists at all).

    Note: Germany, Denmark and Sweden - the countries this application covers
    - all observe the same civil time zone (CET/CEST), so `at`'s clock time is
    compared directly against `time_label` without per-country time zone
    conversion.
    """
    kwh_tiers = [t for t in tiers if t.unit == "kWh"]
    owner_tiers = [t for t in kwh_tiers if "owner" in t.tier_label.lower()]
    if not owner_tiers:
        distinct_labels = {t.tier_label for t in kwh_tiers}
        if len(distinct_labels) == 1:
            owner_tiers = kwh_tiers
    if not owner_tiers:
        return None

    time_matched = [t for t in owner_tiers if t.time_label and _time_in_window(t.time_label, at)]
    if time_matched:
        return time_matched[0]

    flat = [t for t in owner_tiers if t.time_label is None]
    if flat:
        return flat[0]

    return owner_tiers[0]


_MIDDAY_HOUR = 12


def _parse_clock(label: str) -> dt_time:
    """Parses a `"H:MM AM/PM"` clock string into a `time`."""
    match = re.match(r"\s*(\d{1,2}):(\d{2})\s*([AP]M)\s*$", label, re.IGNORECASE)
    if match is None:
        raise ValueError(f"unrecognized time format: {label!r}")
    hour, minute, meridiem = int(match[1]), int(match[2]), match[3].upper()
    if meridiem == "PM" and hour != _MIDDAY_HOUR:
        hour += _MIDDAY_HOUR
    if meridiem == "AM" and hour == _MIDDAY_HOUR:
        hour = 0
    return dt_time(hour=hour, minute=minute)


def _time_in_window(time_label: str, at: datetime) -> bool:
    """Checks whether `at`'s clock time falls within a rendered time window.

    Handles a `"H:MM AM/PM - H:MM AM/PM"` window, including midnight-wrapping
    windows (e.g. `"10:00 PM - 6:00 AM"`). Returns False if `time_label`
    doesn't match the expected format, rather than raising - malformed labels
    are treated as non-matching, not as a fatal parse error.
    """
    match = _TIME_WINDOW_RE.search(time_label)
    if match is None:
        return False
    try:
        start = _parse_clock(match[1])
        end = _parse_clock(match[2])
    except ValueError:
        return False
    current = at.time().replace(second=0, microsecond=0)
    if start <= end:
        return start <= current < end
    return current >= start or current < end  # wraps past midnight
