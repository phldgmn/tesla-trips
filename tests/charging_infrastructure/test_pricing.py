"""Tests für das `pricing`-Modul (Parsing und Tier-Auswahl)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from tripplanner.charging_infrastructure.models import ChargingPricingTier
from tripplanner.charging_infrastructure.pricing import (
    PricingParseError,
    parse_pricing_tiers,
    select_owner_rate_for_time,
)


def _next_data_html(payload: dict[str, object]) -> str:
    """Wraps `payload` in a minimal HTML page with a `__NEXT_DATA__` script tag."""
    return (
        "<html><body>"
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'
        "</body></html>"
    )


def _charger_pricing_html(charger_pricing: list[dict[str, object]]) -> str:
    return _next_data_html(
        {"props": {"pageProps": {"formattedData": {"chargerPricing": charger_pricing}}}}
    )


class TestParsePricingTiers:
    """Tests für `parse_pricing_tiers`."""

    def test_parses_time_windowed_owner_tier(self) -> None:
        """Ein Tier mit mehreren zeitbasierten Rate-Windows wird vollständig geparst."""
        html = _charger_pricing_html(
            [
                {
                    "label": "Charging Fees for Tesla Owner",
                    "windows": [
                        {"label": "4:00 PM - 8:00 PM", "price": "$0.48/kWh"},
                        {"label": "8:00 PM - 4:00 PM", "price": "$0.36/kWh"},
                    ],
                    "idleFee": "$0.50/min idle",
                },
            ]
        )
        tiers = parse_pricing_tiers(html)
        assert len(tiers) == 2
        peak, offpeak = tiers
        assert peak.tier_label == "Charging Fees for Tesla Owner"
        assert peak.time_label == "4:00 PM - 8:00 PM"
        assert peak.currency == "USD"
        assert peak.amount == pytest.approx(0.48)
        assert peak.unit == "kWh"
        assert peak.idle_fee_text == "$0.50/min idle"
        assert offpeak.time_label == "8:00 PM - 4:00 PM"
        assert offpeak.amount == pytest.approx(0.36)

    def test_idle_fee_is_not_duplicated_as_a_separate_tier(self) -> None:
        """`idleFee` liefert nur `idle_fee_text`, keinen eigenen `min`-Tier-Eintrag."""
        html = _charger_pricing_html(
            [
                {
                    "label": "Charging Fees for Tesla Owner",
                    "price": "SEK 3.79/kWh",
                    "idleFee": "SEK 5.00/min idle",
                },
            ]
        )
        tiers = parse_pricing_tiers(html)
        assert len(tiers) == 1
        assert tiers[0].unit == "kWh"
        assert tiers[0].idle_fee_text == "SEK 5.00/min idle"

    def test_parses_flat_rate_without_windows(self) -> None:
        """Ein Tier ohne verschachtelte Windows (einzelner Flatrate-Preis) wird geparst."""
        html = _charger_pricing_html(
            [{"label": "Charging Fees for Tesla Owner", "price": "€0,45/kWh"}]
        )
        tiers = parse_pricing_tiers(html)
        assert len(tiers) == 1
        assert tiers[0].currency == "EUR"
        assert tiers[0].amount == pytest.approx(0.45)
        assert tiers[0].time_label is None

    def test_multiple_tiers_owner_and_other_ev(self) -> None:
        """Owner- und Other-EV-Tier werden beide unabhängig geparst."""
        html = _charger_pricing_html(
            [
                {"label": "Charging Fees for Tesla Owner", "price": "$0.36/kWh"},
                {"label": "Charging Fees for Other EV", "price": "$0.52/kWh"},
            ]
        )
        tiers = parse_pricing_tiers(html)
        assert {t.tier_label for t in tiers} == {
            "Charging Fees for Tesla Owner",
            "Charging Fees for Other EV",
        }

    def test_missing_next_data_raises(self) -> None:
        """Fehlender `__NEXT_DATA__`-Script-Tag löst `PricingParseError` aus."""
        with pytest.raises(PricingParseError, match="__NEXT_DATA__"):
            parse_pricing_tiers("<html><body>no data here</body></html>")

    def test_invalid_json_raises(self) -> None:
        """Ungültiges JSON im `__NEXT_DATA__`-Blob löst `PricingParseError` aus."""
        html = '<script id="__NEXT_DATA__" type="application/json">{not json}</script>'
        with pytest.raises(PricingParseError, match="not valid JSON"):
            parse_pricing_tiers(html)

    def test_missing_formatted_data_raises(self) -> None:
        """Fehlendes `props.pageProps.formattedData` löst `PricingParseError` aus."""
        html = _next_data_html({"props": {"pageProps": {}}})
        with pytest.raises(PricingParseError, match="formattedData"):
            parse_pricing_tiers(html)

    def test_missing_charger_pricing_key_raises(self) -> None:
        """Fehlender `chargerPricing`-Schlüssel löst `PricingParseError` aus."""
        html = _next_data_html({"props": {"pageProps": {"formattedData": {}}}})
        with pytest.raises(PricingParseError, match="chargerPricing"):
            parse_pricing_tiers(html)

    def test_empty_charger_pricing_is_a_legitimate_empty_result(self) -> None:
        """Ein leeres `chargerPricing`-Array (z. B. Destination Charger) ist kein Fehler."""
        assert parse_pricing_tiers(_charger_pricing_html([])) == []

    def test_unparseable_rate_is_skipped_not_fatal(self) -> None:
        """Ein Tier ohne erkennbares Preis-Format wird übersprungen statt zu crashen."""
        html = _charger_pricing_html(
            [
                {"label": "Charging Fees for Tesla Owner", "note": "Contact site for pricing"},
                {"label": "Charging Fees for Other EV", "price": "$0.52/kWh"},
            ]
        )
        tiers = parse_pricing_tiers(html)
        assert len(tiers) == 1
        assert tiers[0].tier_label == "Charging Fees for Other EV"

    def test_charger_pricing_not_a_list_raises(self) -> None:
        """`chargerPricing` als falscher Typ (nicht Liste) löst `PricingParseError` aus."""
        html = _next_data_html(
            {"props": {"pageProps": {"formattedData": {"chargerPricing": "unexpected"}}}}
        )
        with pytest.raises(PricingParseError, match="not a list"):
            parse_pricing_tiers(html)

    def test_non_positive_amount_is_skipped(self) -> None:
        """Ein Tier mit nicht-positivem Betrag (Pydantic-Validierung) wird
        übersprungen, ohne den gesamten Parse-Vorgang scheitern zu lassen.
        """
        html = _charger_pricing_html(
            [
                {"label": "Charging Fees for Tesla Owner", "price": "$0.00/kWh"},
                {"label": "Charging Fees for Other EV", "price": "$0.52/kWh"},
            ]
        )
        tiers = parse_pricing_tiers(html)
        assert len(tiers) == 1
        assert tiers[0].tier_label == "Charging Fees for Other EV"

    def test_non_dict_entries_are_ignored(self) -> None:
        """Nicht-Dict-Einträge in `chargerPricing` werden übersprungen."""
        html = _next_data_html(
            {
                "props": {
                    "pageProps": {
                        "formattedData": {
                            "chargerPricing": [
                                "unexpected string entry",
                                {"label": "Charging Fees for Tesla Owner", "price": "$0.40/kWh"},
                            ]
                        }
                    }
                }
            }
        )
        tiers = parse_pricing_tiers(html)
        assert len(tiers) == 1


class TestSelectOwnerRateForTime:
    """Tests für `select_owner_rate_for_time`."""

    def _tiers(self) -> list[ChargingPricingTier]:
        html = _charger_pricing_html(
            [
                {
                    "label": "Charging Fees for Tesla Owner",
                    "windows": [
                        {"label": "4:00 PM - 8:00 PM", "price": "$0.48/kWh"},
                        {"label": "8:00 PM - 4:00 PM", "price": "$0.36/kWh"},
                    ],
                },
                {"label": "Charging Fees for Other EV", "price": "$0.52/kWh"},
            ]
        )
        return parse_pricing_tiers(html)

    def test_selects_peak_window_at_matching_time(self) -> None:
        """18:00 Uhr faellt in das 16:00-20:00-Fenster (Peak)."""
        rate = select_owner_rate_for_time(self._tiers(), datetime(2026, 1, 1, 18, 0, tzinfo=UTC))
        assert rate is not None
        assert rate.amount == pytest.approx(0.48)

    def test_selects_offpeak_window_wrapping_midnight(self) -> None:
        """03:00 Uhr faellt in das ueber Mitternacht laufende 20:00-16:00-Fenster."""
        rate = select_owner_rate_for_time(self._tiers(), datetime(2026, 1, 1, 3, 0, tzinfo=UTC))
        assert rate is not None
        assert rate.amount == pytest.approx(0.36)

    def test_never_selects_other_ev_tier(self) -> None:
        """Der 'Other EV'-Tier wird nie als Owner-Rate zurueckgegeben."""
        rate = select_owner_rate_for_time(self._tiers(), datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
        assert rate is not None
        assert rate.tier_label == "Charging Fees for Tesla Owner"

    def test_falls_back_to_flat_rate_when_no_time_windows(self) -> None:
        """Ohne zeitbasierte Fenster wird die einzige Flatrate zurueckgegeben."""
        html = _charger_pricing_html(
            [{"label": "Charging Fees for Tesla Owner", "price": "SEK 3.79/kWh"}]
        )
        tiers = parse_pricing_tiers(html)
        rate = select_owner_rate_for_time(tiers, datetime(2026, 1, 1, 9, 0, tzinfo=UTC))
        assert rate is not None
        assert rate.amount == pytest.approx(3.79)

    def test_falls_back_to_single_tier_without_owner_keyword(self) -> None:
        """Ein einzelner, nicht als 'owner' gekennzeichneter Tier gilt per
        Ausschlussverfahren als Owner-Rate.
        """
        html = _charger_pricing_html([{"label": "Charging Fees", "price": "DKK 4.50/kWh"}])
        tiers = parse_pricing_tiers(html)
        rate = select_owner_rate_for_time(tiers, datetime(2026, 1, 1, 9, 0, tzinfo=UTC))
        assert rate is not None
        assert rate.amount == pytest.approx(4.50)

    def test_returns_none_when_only_other_ev_and_another_distinct_tier_exist(self) -> None:
        """Bei mehreren nicht als 'owner' erkennbaren Tiers ist die Zuordnung
        mehrdeutig - kein Rate wird zurueckgegeben.
        """
        html = _charger_pricing_html(
            [
                {"label": "Members", "price": "$0.30/kWh"},
                {"label": "Non-members", "price": "$0.52/kWh"},
            ]
        )
        tiers = parse_pricing_tiers(html)
        assert select_owner_rate_for_time(tiers, datetime(2026, 1, 1, 9, 0, tzinfo=UTC)) is None

    def test_returns_none_for_empty_tiers(self) -> None:
        """Keine Preisdaten ergeben None statt eines Fehlers."""
        assert select_owner_rate_for_time([], datetime(2026, 1, 1, 9, 0, tzinfo=UTC)) is None
