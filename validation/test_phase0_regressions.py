"""
Phase 0 Regression Pins — FROZEN
=================================

One test per bug fixed in Phase 0. Each pins the *specific* defect with the
*specific* evidence that exposed it, so it cannot silently return.

These tests encode what was actually wrong, which in three of four cases is NOT
what the implementation plan predicted:

  Plan Task 0.1  blamed t2m being read instead of stl3.
                 ACTUAL: `t2m` appears nowhere in the codebase. The dashboard's
                 sample generator invented temperatures from a bare sinusoid,
                 T_receipt = 20 + 20*sin(2pi(m-4)/12) - uniform(2,8), with zero
                 coupling to soil. It returned -4.2 C in January.

  Plan Task 0.2  blamed a litres/KL factor-1000 error or monthly summation.
                 ACTUAL: units were correct throughout. The same density
                 measurement was resolved to two different rho_60 (767.92 vs
                 756.29 kg/m3) by iterating it separately at each meter's
                 temperature. That manufactured -0.209 KL of UFP on a batch
                 constructed to conserve mass exactly.

  Plan Task 0.3  guessed the June outlier "may disappear" once 0.1/0.2 landed.
                 ACTUAL: it was a hard crash, not an outlier. JUNE.zip was
                 downloaded with a different CDS bounding box (lon 72.0-77.5 vs
                 the canonical 75.5-78.0), so the Mathura waypoint at 77.70 E
                 fell outside June's domain -> NaN -> ValueError inside the BDF
                 integrator. It took out 5 of the 9 validation tests.

  Plan Task 0.4  was the real root cause of 0.3 and should have run FIRST.

Run: pytest kota_bijwasan_ufp/validation/test_phase0_regressions.py -v
"""

import math
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import PRODUCTS
from geo.centerline import generate_centerline
from model.heat_transfer import get_receipt_temperature, solve_from_csv_profile
from model.soil_profile import (
    available_months,
    clamp_moisture,
    require_finite,
    soil_temperature_at,
)
from model.ufp import compute_ufp_batch, compute_ufp_from_model, resolve_rho_60
from model.vcf import compute_alpha_60

SOIL_CSV = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "data",
    "kota_bijwasan_soil_profile.csv",
)

QUANTITY_COLS = ["T_soil_C", "moisture_m3m3", "k_soil_WmK"]


@pytest.fixture(scope="module")
def soil():
    return pd.read_csv(SOIL_CSV)


# ═══════════════════════════════════════════════════════════════════
# BUG 0.3 / 0.4 — the June NaN that crashed the ODE solver
# ═══════════════════════════════════════════════════════════════════


class TestJuneNaNRegression:
    def test_soil_profile_has_no_nan_anywhere(self, soil):
        """The lookup table must never contain a NaN. It once did."""
        bad = soil[soil[QUANTITY_COLS].isna().any(axis=1)]
        assert bad.empty, (
            f"{len(bad)} NaN rows in the soil profile: "
            f"{list(bad[['waypoint_name', 'month']].itertuples(index=False, name=None))}"
        )

    def test_mathura_june_is_finite(self, soil):
        """The exact cell that crashed the solver: km 250, month 6.

        Was: T_soil_C = NaN, moisture = 0.5 (the silent clamp), k_soil = 1.5013.
        """
        row = soil[(soil["waypoint_km"] == 250.0) & (soil["month"] == 6)]
        assert len(row) == 1
        r = row.iloc[0]

        assert math.isfinite(r["T_soil_C"]), "Mathura/June T_soil is NaN again"

        # It must agree with its immediate neighbours along the route — a
        # nearest-valid fill that landed on nonsense would show up here.
        neighbours = soil[(soil["month"] == 6) & (soil["waypoint_km"].isin([210.0, 330.0]))][
            "T_soil_C"
        ]
        assert neighbours.min() - 2.0 <= r["T_soil_C"] <= neighbours.max() + 2.0, (
            f"Mathura June T_soil={r['T_soil_C']} is inconsistent with its "
            f"neighbours {list(neighbours)}"
        )

        # And moisture must NOT be pinned at the clamp ceiling — 0.50 was the
        # artifact of min(0.50, nan) returning 0.50.
        assert r["moisture_m3m3"] < 0.50, (
            "Mathura June moisture is exactly at the clamp ceiling (0.50), the "
            "signature of a NaN silently passing through min(0.50, nan)"
        )

    def test_solver_runs_for_every_available_month(self):
        """Every month with source data must integrate without blowing up."""
        for month in available_months():
            profile = solve_from_csv_profile(
                product_name="petrol",
                T_dispatch_C=35.0,
                flow_rate_m3hr=150.0,
                density_kgm3=750.0,
                month=month,
            )
            T_receipt = get_receipt_temperature(profile)
            assert math.isfinite(T_receipt), f"month {month}: T_receipt is NaN"
            assert profile["T_product_C"].notna().all(), (
                f"month {month}: NaN inside the temperature profile"
            )

    def test_nan_clamp_hole_is_closed(self):
        """min(0.50, nan) returns 0.50 in Python. That hole must stay shut."""
        # Demonstrate the original defect still exists in raw Python...
        assert max(0.02, min(0.50, float("nan"))) == 0.50

        # ...and that our guarded clamp refuses it.
        with pytest.raises(ValueError, match="Non-finite"):
            clamp_moisture(float("nan"), where="regression test")

        with pytest.raises(ValueError, match="Non-finite"):
            require_finite(float("nan"), "soil temperature", "regression test")

    def test_missing_month_is_rejected_not_substituted(self):
        """August has no source ZIP. It must raise, not silently serve July."""
        assert 8 not in available_months(), "August now has data — update this pin"

        with pytest.raises(ValueError, match="No ERA5 soil data for month 8"):
            soil_temperature_at(km=0.0, month=8)


# ═══════════════════════════════════════════════════════════════════
# BUG 0.2 — rho_60 resolved twice, manufacturing UFP from nothing
# ═══════════════════════════════════════════════════════════════════


class TestRho60Regression:
    def test_mass_conserving_batch_has_exactly_zero_net_ufp(self):
        """The batch that exposed the bug: petrol 2000 KL, 35 C -> 22 C.

        Was: UFP_net = -0.208900 KL (-0.0107%) out of thin air.
        Must be: exactly 0.
        """
        r = compute_ufp_from_model(
            product_name="petrol",
            V_dispatch_KL=2000.0,
            T_dispatch_C=35.0,
            density_kgm3=750.0,
            T_receipt_modeled_C=22.0,
        )
        assert abs(r["UFP_net_KL"]) < 1e-9, (
            f"UFP_net = {r['UFP_net_KL']} KL on a mass-conserving batch. "
            f"rho_60 is being resolved inconsistently again."
        )

    def test_rho_60_is_independent_of_meter_temperature(self):
        """One batch, one rho_60 — whatever temperature you look at it from."""
        rho_at_dispatch = resolve_rho_60("petrol", 750.0, density_measured_at_C=35.0)

        # The same physical oil, its density referred from the same measurement,
        # must give the same rho_60 regardless of what the receipt meter reads.
        for T_receipt in (10.0, 22.0, 30.0, 45.0):
            r = compute_ufp_from_model(
                product_name="petrol",
                V_dispatch_KL=2000.0,
                T_dispatch_C=35.0,
                density_kgm3=750.0,
                T_receipt_modeled_C=T_receipt,
            )
            # rho_60_kgm3 is reported rounded to 3 dp; the bug it guards moved
            # rho_60 by 11.6 kg/m3, so a 1e-3 tolerance is four orders of
            # magnitude tighter than the defect.
            assert abs(r["rho_60_kgm3"] - rho_at_dispatch) < 1e-3, (
                f"rho_60 drifted to {r['rho_60_kgm3']} when the receipt meter "
                f"read {T_receipt} C; it must stay {rho_at_dispatch:.3f}"
            )

    def test_zero_net_ufp_across_the_operating_envelope(self):
        """No loss-free batch, anywhere in the envelope, may show net UFP."""
        rng = np.random.RandomState(7)
        for _ in range(50):
            product = str(rng.choice(["petrol", "diesel", "atf"]))
            V = float(rng.uniform(500, 5000))
            T_d = float(rng.uniform(5, 50))
            T_r = float(rng.uniform(5, 50))
            rho = PRODUCTS[product].density_ref_kgm3 + float(rng.uniform(-15, 15))

            r = compute_ufp_from_model(
                product_name=product,
                V_dispatch_KL=V,
                T_dispatch_C=T_d,
                density_kgm3=rho,
                T_receipt_modeled_C=T_r,
            )
            assert abs(r["UFP_net_KL"]) < 1e-6 * V, (
                f"{product} V={V:.0f} {T_d:.1f}->{T_r:.1f} C: UFP_net = {r['UFP_net_KL']}"
            )


# ═══════════════════════════════════════════════════════════════════
# BUG 0.2 — units. The reported "553-1927 KL" were litres.
# ═══════════════════════════════════════════════════════════════════


class TestUnitsRegression:
    def test_litres_are_exactly_1000x_kilolitres(self):
        r = compute_ufp_batch(
            product_name="petrol",
            V_dispatch_KL=2000.0,
            T_dispatch_C=35.0,
            density_kgm3=750.0,
            V_receipt_KL=1975.0,
            T_receipt_C=22.0,
        )
        # Both fields are rounded for display (litres to 0.1 L, KL to 1e-6 KL),
        # so compare within the coarser of the two roundings.
        assert abs(r["UFP_litres"] - r["UFP_net_KL"] * 1000.0) < 0.05

    def test_gross_thermal_net_decomposition_is_exact(self):
        """gross == thermal + net / CTL_receipt, to machine precision."""
        r = compute_ufp_batch(
            product_name="diesel",
            V_dispatch_KL=1500.0,
            T_dispatch_C=38.0,
            density_kgm3=840.0,
            V_receipt_KL=1480.0,
            T_receipt_C=24.0,
        )
        lhs = r["UFP_gross_KL"]
        rhs = r["UFP_thermal_KL"] + r["UFP_net_KL"] / r["CTL_receipt"]
        assert abs(lhs - rhs) < 1e-3, f"decomposition broken: gross={lhs}, thermal+net/CTL={rhs}"

    def test_thermal_ufp_matches_closed_form(self):
        """Cross-check the model against first-principles alpha_60 * dT_F * V.

        This is the check that proved the plan's [1, 8] KL acceptance band for
        a 2000 KL petrol batch at dT = 15 C is physically unreachable: the true
        answer is ~35 KL (1.74%).
        """
        V, T_d, T_r, rho = 2000.0, 35.0, 20.0, 750.0  # dT = 15 C
        r = compute_ufp_from_model("petrol", V, T_d, rho, T_r)

        alpha = compute_alpha_60(r["rho_60_kgm3"], PRODUCTS["petrol"])
        dT_F = (T_d - T_r) * 9.0 / 5.0
        closed_form_KL = alpha * dT_F * V

        assert abs(r["UFP_thermal_KL"] - closed_form_KL) / closed_form_KL < 0.05, (
            f"model {r['UFP_thermal_KL']:.2f} KL vs closed form "
            f"{closed_form_KL:.2f} KL — more than 5% apart"
        )
        # Pin the magnitude: it is ~35 KL, NOT the plan's 1-8 KL.
        assert 30.0 < r["UFP_thermal_KL"] < 40.0, (
            f"thermal UFP = {r['UFP_thermal_KL']:.2f} KL; expected ~35 KL "
            f"(1.74% of batch) for petrol at dT = 15 C"
        )


# ═══════════════════════════════════════════════════════════════════
# BUG 0.1 — winter receipt temperatures. Was -4.2 C in January.
# ═══════════════════════════════════════════════════════════════════


class TestReceiptTemperatureRegression:
    def test_receipt_temperature_never_subzero_and_never_below_12C(self):
        """A buried batch cannot arrive at -4 C when the soil is at 18-21 C."""
        for month in available_months():
            T_soil_origin = soil_temperature_at(km=0.0, month=month)
            profile = solve_from_csv_profile(
                product_name="petrol",
                T_dispatch_C=T_soil_origin,
                flow_rate_m3hr=150.0,
                density_kgm3=750.0,
                month=month,
            )
            T_receipt = get_receipt_temperature(profile)
            assert T_receipt >= 12.0, (
                f"month {month}: T_receipt = {T_receipt:.1f} C, below the 12 C "
                f"floor. The old sinusoid generator returned -4.2 C in January."
            )

    def test_product_moves_toward_soil_never_away(self):
        """The batch must relax TOWARD the soil temperature, in both directions.

        The old generator subtracted an unconditional 2-8 C, so it 'cooled' the
        product even in months when the soil is hotter than the dispatch
        temperature. Heat flows one way only.
        """
        for month in available_months():
            T_soil_receipt = soil_temperature_at(km=360.0, month=month)

            for T_dispatch in (T_soil_receipt - 12.0, T_soil_receipt + 12.0):
                profile = solve_from_csv_profile(
                    product_name="diesel",
                    T_dispatch_C=T_dispatch,
                    flow_rate_m3hr=150.0,
                    density_kgm3=840.0,
                    month=month,
                )
                T_receipt = get_receipt_temperature(profile)

                # Must land between dispatch and soil (inclusive), i.e. it moved
                # toward soil and did not overshoot past it.
                lo, hi = sorted((T_dispatch, T_soil_receipt))
                assert lo - 0.5 <= T_receipt <= hi + 0.5, (
                    f"month {month}: dispatched at {T_dispatch:.1f} C into soil "
                    f"at {T_soil_receipt:.1f} C, arrived at {T_receipt:.1f} C — "
                    f"outside [{lo:.1f}, {hi:.1f}]. Heat flowed the wrong way."
                )

                # And it must actually have moved.
                assert abs(T_receipt - T_dispatch) > 0.1, (
                    f"month {month}: no heat exchange at all over 360 km"
                )

    def test_sign_reversal_is_driven_by_the_soil_not_the_calendar(self):
        """The thermal artifact flips sign according to soil, in both directions.

        Asserted RELATIVE to the local soil temperature, deliberately. An
        earlier version of this test hardcoded "June: soil ~32 C", which was
        true only of stl3 (28-100 cm) — the wrong layer. At the pipe's actual
        burial depth of 1.2 m the soil is layer 4 (100-289 cm), which is damped
        and phase-lagged: June soil is 28.1 C, not 32.4 C. A 30 C batch
        therefore COOLS in June, and the old test's premise collapsed.

        Pinning to soil-relative dispatch temperatures makes the assertion a
        statement about heat flow, which is what we actually mean, and immunises
        it against any future change of soil layer or burial depth.
        """
        for month in available_months():
            T_soil = soil_temperature_at(km=360.0, month=month)

            # Dispatched ABOVE the soil -> cools -> contracts -> positive UFP.
            T_hot = T_soil + 10.0
            T_rec_hot = get_receipt_temperature(
                solve_from_csv_profile("petrol", T_hot, 150.0, 750.0, month=month)
            )
            ufp_hot = compute_ufp_from_model("petrol", 2000.0, T_hot, 750.0, T_rec_hot)[
                "UFP_thermal_KL"
            ]

            assert T_rec_hot < T_hot and ufp_hot > 0, (
                f"month {month}: soil {T_soil:.1f} C, dispatched {T_hot:.1f} C, "
                f"arrived {T_rec_hot:.1f} C, thermal UFP {ufp_hot:.2f} KL — "
                f"a batch hotter than the soil must cool and contract"
            )

            # Dispatched BELOW the soil -> warms -> expands -> negative UFP.
            T_cold = T_soil - 10.0
            T_rec_cold = get_receipt_temperature(
                solve_from_csv_profile("petrol", T_cold, 150.0, 750.0, month=month)
            )
            ufp_cold = compute_ufp_from_model("petrol", 2000.0, T_cold, 750.0, T_rec_cold)[
                "UFP_thermal_KL"
            ]

            assert T_rec_cold > T_cold and ufp_cold < 0, (
                f"month {month}: soil {T_soil:.1f} C, dispatched {T_cold:.1f} C, "
                f"arrived {T_rec_cold:.1f} C, thermal UFP {ufp_cold:.2f} KL — "
                f"a batch cooler than the soil must warm and expand"
            )

    def test_deep_soil_is_damped_relative_to_shallow_soil(self):
        """The pipe sits at 1.2 m, so it must see the DAMPED annual wave.

        The annual temperature wave attenuates and phase-lags with depth. Layer 4
        (100-289 cm) must therefore swing less than layer 3 (28-100 cm). If this
        ever inverts, the soil layer selection has regressed.

        Measured at Bijwasan: stl3 swings 14.5 C over the year; stl4 swings
        8.3 C. Reading stl3 at a 1.2 m burial overstated the seasonal signal by
        ~75% and got its phase wrong.
        """
        from config import SOIL_TEMPERATURE_VAR

        assert SOIL_TEMPERATURE_VAR == "stl4", (
            f"soil layer is {SOIL_TEMPERATURE_VAR}; the pipe is buried at 1.2 m, "
            f"which lies in ERA5 layer 4 (100-289 cm)"
        )

        temps = [soil_temperature_at(km=360.0, month=m) for m in available_months()]
        swing = max(temps) - min(temps)

        print(
            f"\n  Bijwasan deep-soil (stl4) annual swing: {swing:.2f} C "
            f"({min(temps):.2f} – {max(temps):.2f})"
        )

        assert 4.0 < swing < 12.0, (
            f"deep-soil annual swing is {swing:.1f} C. Layer 4 should swing "
            f"~8 C at this latitude; ~14 C would mean stl3 (too shallow) has "
            f"crept back in."
        )


# ═══════════════════════════════════════════════════════════════════
# Centerline — duplicate waypoint samples
# ═══════════════════════════════════════════════════════════════════


class TestCenterlineRegression:
    def test_no_duplicate_chainage(self):
        """Every interior waypoint was being emitted twice (367 pts, not 361)."""
        pts = generate_centerline()
        kms = [p.km for p in pts]
        dupes = sorted({k for k in kms if kms.count(k) > 1})
        assert not dupes, (
            f"duplicate chainage at km {dupes} — the j>0 guard in "
            f"generate_centerline() re-emits each segment's start point"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-s"])
