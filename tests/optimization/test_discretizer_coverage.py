"""Zusätzliche Tests für Diskretisierungsfunktionen - Missing Coverage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tripplanner.optimization.discretizer import (
    bucket_to_soc,
    bucket_to_time,
    create_state_node,
    get_all_soc_buckets,
    get_all_time_buckets_for_duration,
    soc_to_bucket,
    time_to_bucket,
)


class TestDiscretizerEdgeCases:
    """Tests für Randfälle und fehlende Branches."""

    # --- soc_to_bucket ---

    def test_soc_to_bucket_negative_raises(self) -> None:
        """Test: Negative SoC wirft ValueError."""
        with pytest.raises(ValueError, match="SoC muss im Bereich"):
            soc_to_bucket(-0.1)
        with pytest.raises(ValueError, match="SoC muss im Bereich"):
            soc_to_bucket(-100.0)

    def test_soc_to_bucket_above_100_raises(self) -> None:
        """Test: SoC > 100 wirft ValueError."""
        with pytest.raises(ValueError, match="SoC muss im Bereich"):
            soc_to_bucket(100.1)
        with pytest.raises(ValueError, match="SoC muss im Bereich"):
            soc_to_bucket(150.0)

    def test_soc_to_bucket_invalid_step_zero_raises(self) -> None:
        """Test: soc_step_pct <= 0 wirft ValueError."""
        with pytest.raises(ValueError, match="soc_step_pct muss positiv"):
            soc_to_bucket(50.0, soc_step_pct=0.0)
        with pytest.raises(ValueError, match="soc_step_pct muss positiv"):
            soc_to_bucket(50.0, soc_step_pct=-1.0)

    def test_soc_to_bucket_exact_boundary_values(self) -> None:
        """Test: Exakte Grenzwerte (0, 100) funktionieren."""
        assert soc_to_bucket(0.0) == 0
        assert soc_to_bucket(100.0) == 100

    def test_soc_to_bucket_rounding_behavior_at_half_steps(self) -> None:
        """Test: Rundungsverhalten bei .5 Werten (Python round = banker's rounding)."""
        # Bei 1% Schritt: round(50.5) = 50 (gerade), round(51.5) = 52 (gerade)
        assert soc_to_bucket(50.5) == 50  # round(50.5) = 50
        assert soc_to_bucket(51.5) == 52  # round(51.5) = 52
        assert soc_to_bucket(0.5) == 0  # round(0.5) = 0
        assert soc_to_bucket(99.5) == 100  # round(99.5) = 100
        assert soc_to_bucket(1.5) == 2  # round(1.5) = 2
        assert soc_to_bucket(2.5) == 2  # round(2.5) = 2 (gerade)

    def test_soc_to_bucket_custom_step_rounding(self) -> None:
        """Test: Rundung mit verschiedenen Schrittweiten."""
        # 0.5% Schritte
        assert soc_to_bucket(50.25, soc_step_pct=0.5) == 100  # round(100.5) = 100
        assert soc_to_bucket(50.0, soc_step_pct=0.5) == 100  # round(100.0) = 100
        assert soc_to_bucket(49.75, soc_step_pct=0.5) == 100  # round(99.5) = 100

        # 5% Schritte
        assert soc_to_bucket(7.5, soc_step_pct=5.0) == 2  # round(1.5) = 2
        assert soc_to_bucket(2.5, soc_step_pct=5.0) == 0  # round(0.5) = 0

    # --- bucket_to_soc ---

    def test_bucket_to_soc_negative_raises(self) -> None:
        """Test: Negativer Bucket-Index wirft ValueError."""
        with pytest.raises(ValueError, match="Bucket-Index muss nicht-negativ"):
            bucket_to_soc(-1)
        with pytest.raises(ValueError, match="Bucket-Index muss nicht-negativ"):
            bucket_to_soc(-100)

    def test_bucket_to_soc_invalid_step_zero_raises(self) -> None:
        """Test: soc_step_pct <= 0 wirft ValueError."""
        with pytest.raises(ValueError, match="soc_step_pct muss positiv"):
            bucket_to_soc(50, soc_step_pct=0.0)
        with pytest.raises(ValueError, match="soc_step_pct muss positiv"):
            bucket_to_soc(50, soc_step_pct=-1.0)

    def test_bucket_to_soc_exact_values(self) -> None:
        """Test: Exakte Bucket-Werte."""
        assert bucket_to_soc(0) == 0.0
        assert bucket_to_soc(100) == 100.0

    def test_bucket_to_soc_custom_step(self) -> None:
        """Test: Bucket zu SoC mit verschiedenen Schritten."""
        assert bucket_to_soc(100, soc_step_pct=0.5) == 50.0
        assert bucket_to_soc(20, soc_step_pct=5.0) == 100.0

    # --- time_to_bucket ---

    def test_time_to_bucket_invalid_step_zero_raises(self) -> None:
        """Test: time_step_min <= 0 wirft ValueError."""
        base = datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)
        with pytest.raises(ValueError, match="time_step_min muss positiv"):
            time_to_bucket(base, base, time_step_min=0)
        with pytest.raises(ValueError, match="time_step_min muss positiv"):
            time_to_bucket(base, base, time_step_min=-5)

    def test_time_to_bucket_negative_delta(self) -> None:
        """Test: Zeitpunkt vor base_time ergibt negativen Bucket."""
        base = datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)
        earlier = base - timedelta(minutes=15)

        # Negative Zeitdifferenz -> negativer Bucket
        result = time_to_bucket(earlier, base)
        assert result == -1

    def test_time_to_bucket_negative_delta_larger(self) -> None:
        """Test: Größere negative Zeitdifferenz."""
        base = datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)
        earlier = base - timedelta(minutes=30)
        result = time_to_bucket(earlier, base)
        assert result == -2

    def test_time_to_bucket_rounding_behavior_at_half_step(self) -> None:
        """Test: Rundung bei halber Schrittweite (banker's rounding).

        Hinweis: Implementation nutzt int(delta.total_seconds() / 60) was
        auf ganze Minuten abrundet. 22.5 min -> 22 min -> 22/15 = 1.46 -> round = 1
        """
        base = datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)

        # 15 min Schritt:
        # 7.5 min -> int=7 -> 7/15=0.46 -> round(0.46) = 0 -> Bucket 0
        t1 = base + timedelta(minutes=7, seconds=30)
        assert time_to_bucket(t1, base) == 0

        # 22.5 min -> int=22 -> 22/15=1.46 -> round(1.46) = 1 -> Bucket 1
        t2 = base + timedelta(minutes=22, seconds=30)
        assert time_to_bucket(t2, base) == 1

        # 37.5 min -> int=37 -> 37/15=2.46 -> round(2.46) = 2 -> Bucket 2
        t3 = base + timedelta(minutes=37, seconds=30)
        assert time_to_bucket(t3, base) == 2

        # 52.5 min -> int=52 -> 52/15=3.46 -> round(3.46) = 3 -> Bucket 3
        t4 = base + timedelta(minutes=52, seconds=30)
        assert time_to_bucket(t4, base) == 3

    def test_time_to_bucket_exact_boundaries(self) -> None:
        """Test: Exakte Zeit-Grenzen."""
        base = datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)

        assert time_to_bucket(base, base) == 0
        assert time_to_bucket(base + timedelta(minutes=15), base) == 1
        assert time_to_bucket(base + timedelta(minutes=30), base) == 2
        assert time_to_bucket(base + timedelta(minutes=45), base) == 3
        assert time_to_bucket(base + timedelta(hours=1), base) == 4

    def test_time_to_bucket_custom_step(self) -> None:
        """Test: Zeit-Bucket mit anderer Schrittweite."""
        base = datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)

        # 10 min Schritte
        # 5 min -> 0.5 -> round(0.5) = 0
        assert time_to_bucket(base + timedelta(minutes=5), base, time_step_min=10) == 0
        # 10 min -> 1.0 -> round(1.0) = 1
        assert time_to_bucket(base + timedelta(minutes=10), base, time_step_min=10) == 1
        # 15 min -> 1.5 -> round(1.5) = 2
        assert time_to_bucket(base + timedelta(minutes=15), base, time_step_min=10) == 2

    # --- bucket_to_time ---

    def test_bucket_to_time_negative_raises(self) -> None:
        """Test: Negativer Bucket wirft ValueError."""
        base = datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)
        with pytest.raises(ValueError, match="Bucket-Index muss nicht-negativ"):
            bucket_to_time(-1, base)
        with pytest.raises(ValueError, match="Bucket-Index muss nicht-negativ"):
            bucket_to_time(-100, base)

    def test_bucket_to_time_invalid_step_zero_raises(self) -> None:
        """Test: time_step_min <= 0 wirft ValueError."""
        base = datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)
        with pytest.raises(ValueError, match="time_step_min muss positiv"):
            bucket_to_time(1, base, time_step_min=0)
        with pytest.raises(ValueError, match="time_step_min muss positiv"):
            bucket_to_time(1, base, time_step_min=-5)

    def test_bucket_to_time_zero_bucket(self) -> None:
        """Test: Bucket 0 gibt base_time zurück."""
        base = datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)
        assert bucket_to_time(0, base) == base

    def test_bucket_to_time_custom_step(self) -> None:
        """Test: bucket_to_time mit anderer Schrittweite."""
        base = datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)
        assert bucket_to_time(1, base, time_step_min=10) == base + timedelta(minutes=10)
        assert bucket_to_time(2, base, time_step_min=5) == base + timedelta(minutes=10)

    def test_bucket_to_time_large_bucket(self) -> None:
        """Test: Großer Bucket-Index."""
        base = datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)
        result = bucket_to_time(1000, base)
        expected = base + timedelta(minutes=1000 * 15)
        assert result == expected

    # --- create_state_node ---

    def test_create_state_node_default_step(self) -> None:
        """Test: create_state_node mit Default-Schrittweiten."""
        segment_index = 5
        soc_pct = 75.0
        zeitpunkt = datetime(2026, 1, 1, 8, 30, 0, tzinfo=UTC)

        result = create_state_node(segment_index, soc_pct, zeitpunkt)

        assert isinstance(result, tuple)
        assert len(result) == 3
        seg_idx, soc_bucket, time_bucket = result
        assert seg_idx == segment_index
        assert soc_bucket == soc_to_bucket(soc_pct)
        # create_state_node nutzt zeitpunkt als base_time für time_bucket
        assert time_bucket == time_to_bucket(zeitpunkt, zeitpunkt)

    def test_create_state_node_custom_soc_step(self) -> None:
        """Test: create_state_node mit benutzerdefinierter SoC-Schrittweite."""
        result = create_state_node(
            segment_index=3,
            soc_pct=50.0,
            zeitpunkt=datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC),
            soc_step_pct=5.0,
        )
        assert result[1] == soc_to_bucket(50.0, soc_step_pct=5.0)  # = 10

    def test_create_state_node_roundtrip_consistency(self) -> None:
        """Test: Roundtrip-Konsistenz für verschiedene Schritte."""
        base = datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)

        for soc_step in [0.5, 1.0, 2.0, 5.0, 10.0]:
            for soc in [0.0, 12.5, 25.0, 50.0, 75.0, 87.5, 100.0]:
                node = create_state_node(1, soc, base, soc_step_pct=soc_step)
                _, soc_bucket, _ = node
                reconstructed_soc = bucket_to_soc(soc_bucket, soc_step_pct=soc_step)
                # Rekonstruierter SoC sollte dem Bucket-Mittelpunkt entsprechen
                # Die Differenz zum Original ist max soc_step/2
                assert abs(reconstructed_soc - soc) <= soc_step / 2.0 + 1e-9


class TestGetAllSocBuckets:
    """Tests für get_all_soc_buckets."""

    def test_get_all_soc_buckets_default(self) -> None:
        """Test: Default 1% Schritte -> 101 Buckets (0-100)."""
        buckets = get_all_soc_buckets()
        assert buckets == list(range(101))
        assert len(buckets) == 101

    def test_get_all_soc_buckets_custom_steps(self) -> None:
        """Test: Verschiedene Schrittweiten."""
        # 5% -> 100/5 = 20, round(20) + 1 = 21 -> 0-20
        buckets_5 = get_all_soc_buckets(5.0)
        assert buckets_5 == list(range(21))
        assert len(buckets_5) == 21

        # 20% -> 100/20 = 5, round(5) + 1 = 6 -> 0-5
        buckets_20 = get_all_soc_buckets(20.0)
        assert buckets_20 == list(range(6))
        assert len(buckets_20) == 6

        # 25% -> 100/25 = 4, round(4) + 1 = 5 -> 0-4
        buckets_25 = get_all_soc_buckets(25.0)
        assert buckets_25 == list(range(5))
        assert len(buckets_25) == 5

    def test_get_all_soc_buckets_fractional_step(self) -> None:
        """Test: Bruchteile-Schrittweite (z.B. 0.5%)."""
        # 100/0.5 = 200, round(200) + 1 = 201 -> 0-200
        buckets = get_all_soc_buckets(0.5)
        assert len(buckets) == 201
        assert buckets[-1] == 200

    def test_get_all_soc_buckets_rounding_behavior(self) -> None:
        """Test: Rundungsverhalten bei nicht-teilbaren Schritten."""
        # 100/3 = 33.33, round = 33, +1 = 34
        buckets = get_all_soc_buckets(3.0)
        assert len(buckets) == 34  # 0 bis 33


class TestGetAllTimeBucketsForDuration:
    """Tests für get_all_time_buckets_for_duration."""

    def test_get_all_time_buckets_default_step(self) -> None:
        """Test: Default 15 min Schritte."""
        # 1 Stunde = 3600s
        buckets = get_all_time_buckets_for_duration(3600.0)
        # 60/15 = 4, round(4) + 1 = 5 -> 0-4
        assert buckets == list(range(5))
        assert len(buckets) == 5

    def test_get_all_time_buckets_custom_step(self) -> None:
        """Test: Benutzerdefinierte Zeit-Schrittweite."""
        # 30 min = 1800s, 10 min Schritte
        buckets = get_all_time_buckets_for_duration(1800.0, time_step_min=10)
        # 30/10 = 3, round(3) + 1 = 4 -> 0-3
        assert buckets == list(range(4))
        assert len(buckets) == 4

    def test_get_all_time_buckets_short_duration(self) -> None:
        """Test: Kurze Dauer (< 1 Schritt)."""
        # 5 Minuten = 300s, 15 min Default
        buckets = get_all_time_buckets_for_duration(300.0)
        # 5/15 = 0.33, round = 0, +1 = 1 -> nur [0]
        assert buckets == [0]
        assert len(buckets) == 1

    def test_get_all_time_buckets_zero_duration(self) -> None:
        """Test: Dauer 0."""
        buckets = get_all_time_buckets_for_duration(0.0)
        # 0/15 = 0, round(0) + 1 = 1 -> [0]
        assert buckets == [0]

    def test_get_all_time_buckets_fractional_rounding(self) -> None:
        """Test: Rundung bei bruchteiliger Division (banker's rounding)."""
        # 23 min = 1380s, 15 min Schritte -> 23/15 = 1.53, round = 2, +1 = 3
        buckets = get_all_time_buckets_for_duration(1380.0)
        assert buckets == [0, 1, 2]
        assert len(buckets) == 3

        # 22 min = 1320s, 15 min Schritte -> 22/15 = 1.47, round = 1, +1 = 2
        buckets = get_all_time_buckets_for_duration(1320.0)
        assert buckets == [0, 1]
        assert len(buckets) == 2

        # 22.5 min = 1350s, 15 min Schritte -> 22.5/15 = 1.5, round = 2 (gerade), +1 = 3
        buckets = get_all_time_buckets_for_duration(1350.0)
        assert buckets == [0, 1, 2]
        assert len(buckets) == 3

    def test_get_all_time_buckets_large_duration(self) -> None:
        """Test: Große Dauer."""
        # 12 Stunden = 43200s
        buckets = get_all_time_buckets_for_duration(43200.0)
        # 720/15 = 48, +1 = 49
        assert len(buckets) == 49
        assert buckets[-1] == 48


class TestDiscretizerRoundtripConsistency:
    """Tests für Roundtrip-Konsistenz über alle Funktionen."""

    def test_soc_roundtrip_various_steps(self) -> None:
        """Test: SoC Roundtrip für verschiedene Schritte."""
        for step in [0.5, 1.0, 2.0, 5.0, 10.0]:
            for soc in [0.0, 10.0, 25.0, 33.3, 50.0, 66.6, 75.0, 90.0, 100.0]:
                bucket = soc_to_bucket(soc, soc_step_pct=step)
                back = bucket_to_soc(bucket, soc_step_pct=step)
                # Fehler <= step/2
                assert abs(back - soc) <= step / 2.0 + 1e-9

    def test_time_roundtrip_various_steps(self) -> None:
        """Test: Zeit Roundtrip für verschiedene Schritte (nur positive Buckets)."""
        base = datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)
        test_times = [
            base,
            base + timedelta(minutes=7),
            base + timedelta(minutes=15),
            base + timedelta(minutes=22),
            base + timedelta(minutes=30),
            base + timedelta(hours=1),
            base + timedelta(hours=2, minutes=30),
        ]
        for step in [5, 10, 15, 30]:
            for t in test_times:
                bucket = time_to_bucket(t, base, time_step_min=step)
                # Nur testen wenn Bucket nicht-negativ ist (bucket_to_time verlangt das)
                if bucket >= 0:
                    back = bucket_to_time(bucket, base, time_step_min=step)
                    # Differenz <= step/2 Minuten
                    diff_min = abs((back - t).total_seconds()) / 60
                    assert diff_min <= step / 2.0 + 1e-9

    def test_time_roundtrip_negative_times_produces_negative_buckets(self) -> None:
        """Test: Zeit vor base_time produziert negative Buckets (kein Roundtrip möglich)."""
        base = datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)
        test_times = [
            base - timedelta(minutes=7),
            base - timedelta(minutes=15),
            base - timedelta(minutes=22),
            base - timedelta(minutes=30),
        ]
        for step in [5, 10, 15, 30]:
            for t in test_times:
                bucket = time_to_bucket(t, base, time_step_min=step)
                # Negative Buckets sind erlaubt bei time_to_bucket
                assert bucket <= 0
                # Aber bucket_to_time akzeptiert nur nicht-negative Buckets
                if bucket < 0:
                    with pytest.raises(ValueError, match="Bucket-Index muss nicht-negativ"):
                        bucket_to_time(bucket, base, time_step_min=step)
