"""Tests für `tripplanner.battery.models.ChargingCurve` - insbesondere die
Hot-Path-Auswertung der HERMITE-Interpolation (`ladeleistung_bei_soc`/
`ladeleistung_bei_soc_batch`).

Regressionstest: `ladeleistung_bei_soc` rief für HERMITE-Kurven bei JEDER
Einzelauswertung `scipy.interpolate.PchipInterpolator.__call__` auf. Dessen
Numpy-Array-Erzeugung/-Validierung pro Skalar-Aufruf machte bei den 10^5-10^6
Punktauswertungen eines `optimize_charging_plan`-Laufs (numerische
Integration/Bisektion über die Ladekurve, siehe `optimization.optimizer.
_mittlere_ladeleistung_kw`/`_soc_nach_fester_ladezeit`) den dominanten Anteil
der Gesamtlaufzeit aus (Nutzer-Report: ~58s für eine Route mit vielen
Ladestationen). Die Fixes:

1. Die PCHIP-Koeffizienten werden EINMALIG (in `model_post_init`) aus scipy
   extrahiert und danach per reinem Python-Horner-Schema ausgewertet
   (`_evaluate_fast_path`) - ohne Numpy-Overhead pro Aufruf.
2. Alle Hot-Path-Werte liegen in EINEM `PrivateAttr` (`ChargingCurve._fast`,
   ein reines `__slots__`-Objekt statt mehrerer separater Pydantic-
   `PrivateAttr`s), da jeder `PrivateAttr`-Zugriff auf einem Pydantic-
   `BaseModel` durch dessen generischen `__getattr__`-Fallback läuft.
3. `ladeleistung_bei_soc_batch` holt dieses eine `PrivateAttr` NUR EINMAL für
   mehrere SoC-Werte (statt einmal pro Wert), für Aufrufer, die die Kurve
   über mehrere Punkte auswerten (numerische Integration).
"""

from __future__ import annotations

import time

import pytest
from scipy.interpolate import PchipInterpolator

from tripplanner.battery.models import ChargingCurve, InterpolationMethod, LadekurveReferenz


@pytest.fixture
def hermite_curve() -> ChargingCurve:
    """Produktions-Referenzkurve (Model 3 SR, 25 Stützpunkte, HERMITE)."""
    return LadekurveReferenz.model_3_sr()


class TestLadeleistungHermiteMatchesScipy:
    """`ladeleistung_bei_soc`/`_batch` MÜSSEN exakt (bis auf Gleitkomma-
    Rauschen durch Neuassoziation) dieselben Werte wie eine direkte
    `scipy.interpolate.PchipInterpolator`-Auswertung derselben Stützpunkte
    liefern - der Horner-Fastpath ersetzt scipy nur in der AUSWERTUNG, nicht
    in der MATHEMATIK (identische Koeffizienten, siehe `model_post_init`).
    """

    def _scipy_reference(self, curve: ChargingCurve, soc_pct: float) -> float:
        soc = [p.soc_pct for p in curve.points]
        power = [p.charging_power_kw for p in curve.points]
        pchip = PchipInterpolator(soc, power, extrapolate=False)
        soc_clamped = min(max(soc_pct, 0.0), 100.0)
        return max(float(pchip(soc_clamped)), 0.0)

    @pytest.mark.parametrize(
        "soc_pct",
        [0.0, 0.1, 3.5, 4.0, 8.7, 10.0, 22.3, 49.9, 50.0, 73.1, 99.9, 100.0, -5.0, 110.0],
    )
    def test_scalar_matches_scipy_reference(
        self, hermite_curve: ChargingCurve, soc_pct: float
    ) -> None:
        got = hermite_curve.ladeleistung_bei_soc(soc_pct)
        want = self._scipy_reference(hermite_curve, soc_pct)
        assert got == pytest.approx(want, abs=1e-9)

    def test_batch_matches_scalar_and_scipy_reference(self, hermite_curve: ChargingCurve) -> None:
        soc_values = [0.0, 4.0, 4.5, 9.0, 17.5, 33.0, 61.0, 88.0, 100.0]
        batch = hermite_curve.ladeleistung_bei_soc_batch(soc_values)

        assert batch == [
            pytest.approx(hermite_curve.ladeleistung_bei_soc(soc), abs=1e-12) for soc in soc_values
        ]
        assert batch == [
            pytest.approx(self._scipy_reference(hermite_curve, soc), abs=1e-9) for soc in soc_values
        ]

    def test_batch_empty_returns_empty(self, hermite_curve: ChargingCurve) -> None:
        assert hermite_curve.ladeleistung_bei_soc_batch([]) == []

    def test_linear_curve_batch_matches_scalar(self) -> None:
        """`ladeleistung_bei_soc_batch` muss auch für LINEAR-Kurven (kein
        PCHIP-Pfad) mit der Einzelauswertung übereinstimmen."""
        curve = LadekurveReferenz.model_3_lr_v3()
        assert curve.interpolation is InterpolationMethod.LINEAR

        soc_values = [-10.0, 0.0, 10.0, 35.0, 50.0, 65.0, 85.0, 95.0, 100.0, 120.0]
        batch = curve.ladeleistung_bei_soc_batch(soc_values)
        assert batch == [curve.ladeleistung_bei_soc(soc) for soc in soc_values]


class TestLadeleistungBeiSocPerformance:
    """Regressionstest gegen die scipy-Per-Aufruf-Auswertung (siehe Modul-
    Docstring). `ladeleistung_bei_soc` wurde vor dem Fix bei diesem
    Aufrufvolumen (200'000 Aufrufe, angelehnt an das reale Aufrufvolumen
    innerhalb EINER `optimize_charging_plan`-Ausführung) mehrere Sekunden
    ueber scipy's `PchipInterpolator.__call__` benoetigt (gemessen: ~7 us je
    Aufruf, > 1.4s allein für die scipy-Auswertung). Grosszuegige Grenze
    (gemessen nach dem Fix: < 0.2s) haelt robust Puffer fuer langsamere
    CI-Maschinen, waehrend sie eine Rueckkehr zum scipy-Pro-Aufruf-Pfad
    zuverlaessig faengt.
    """

    def test_viele_einzelaufrufe_sind_deutlich_schneller_als_scipy_pro_aufruf(
        self, hermite_curve: ChargingCurve
    ) -> None:
        n = 200_000
        start = time.perf_counter()
        for i in range(n):
            hermite_curve.ladeleistung_bei_soc((i % 1000) / 10.0)
        dauer_s = time.perf_counter() - start

        assert dauer_s < 2.0, (
            f"{n} Aufrufe von ladeleistung_bei_soc brauchten {dauer_s:.2f}s - "
            "deutet auf eine Regression zurueck zu einer Pro-Aufruf-scipy-"
            "Auswertung hin (siehe Moduldokstring)."
        )

    def test_batch_aufruf_ist_nicht_langsamer_als_einzelaufrufe(
        self, hermite_curve: ChargingCurve
    ) -> None:
        """`ladeleistung_bei_soc_batch` amortisiert den `PrivateAttr`-Zugriff
        ueber alle Punkte - MUSS daher fuer dieselbe Punktzahl mindestens so
        schnell wie die Summe der Einzelaufrufe sein."""
        soc_values = [(i % 1000) / 10.0 for i in range(50_000)]

        start = time.perf_counter()
        for soc in soc_values:
            hermite_curve.ladeleistung_bei_soc(soc)
        einzeln_s = time.perf_counter() - start

        start = time.perf_counter()
        hermite_curve.ladeleistung_bei_soc_batch(soc_values)
        batch_s = time.perf_counter() - start

        assert batch_s < einzeln_s
