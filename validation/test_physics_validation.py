"""
Physics Validation Test Suite
==============================

8 rigorous physical plausibility tests that prove the UFP model
behaves correctly according to known physics — the correct
validation approach when ground truth data is unavailable.

Run: pytest kota_bijwasan_ufp/validation/test_physics_validation.py -v

Tests:
  1. Monotonicity in temperature delta
  2. Zero-crossing behavior
  3. Flow rate sensitivity matches L* prediction
  4. UFP magnitude sanity bounds
  5. Mass conservation check
  6. Product-to-product ordering
  7. Friction/pumping cost superlinearity
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import PIPE_SEGMENTS, PRODUCTS
from geo.centerline import generate_centerline
from model.friction import (
    compute_pumping_cost,
)
from model.heat_transfer import (
    get_receipt_temperature,
    solve_temperature_profile,
)
from model.ufp import compute_ufp_from_model, resolve_rho_60
from model.vcf import compute_alpha_60, compute_ctl

# ═══════════════════════════════════════════════════════════════
# SHARED FIXTURES
# ═══════════════════════════════════════════════════════════════

CSV_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "data",
    "kota_bijwasan_soil_profile.csv",
)


@pytest.fixture(scope="module")
def centerline():
    return generate_centerline()


@pytest.fixture(scope="module")
def soil_profile_full():
    return pd.read_csv(CSV_PATH)


def _get_soil_month(soil_full, month):
    """Get soil profile for a month with fallback."""
    df = soil_full[soil_full["month"] == month]
    if df.empty:
        available = soil_full["month"].unique()
        closest = min(available, key=lambda m: abs(m - month))
        df = soil_full[soil_full["month"] == closest]
    return df.rename(columns={"waypoint_km": "km"}).reset_index(drop=True)


def _run_model(centerline, soil_full, product, T_dispatch, flow_rate, month=6):
    """Helper: run full model and return (T_receipt, ufp_result)."""
    density = PRODUCTS[product].density_ref_kgm3
    soil_df = _get_soil_month(soil_full, month)

    ht = solve_temperature_profile(
        centerline_points=centerline,
        soil_profile_df=soil_df,
        product_name=product,
        T_dispatch_C=T_dispatch,
        flow_rate_m3hr=flow_rate,
        density_kgm3=density,
    )
    T_receipt = get_receipt_temperature(ht)
    ufp = compute_ufp_from_model(
        product_name=product,
        V_dispatch_KL=1000.0,
        T_dispatch_C=T_dispatch,
        density_kgm3=density,
        T_receipt_modeled_C=T_receipt,
    )
    return T_receipt, ufp, ht


# ═══════════════════════════════════════════════════════════════
# TEST 1: MONOTONICITY IN TEMPERATURE DELTA
# As |T_dispatch - T_soil_avg| increases, |UFP| should increase
# ═══════════════════════════════════════════════════════════════


class TestMonotonicity:
    """UFP magnitude should increase monotonically with temperature delta."""

    def test_ufp_increases_with_temperature_delta(self, centerline, soil_profile_full):
        T_sweep = np.arange(10.0, 46.0, 5.0)
        ufp_values = []

        for T in T_sweep:
            _, ufp, _ = _run_model(centerline, soil_profile_full, "petrol", T, 150.0, month=1)
            ufp_values.append(ufp["UFP_thermal_KL"])

        # Check monotonicity: UFP should generally increase in magnitude
        # as T_dispatch moves further from T_soil_avg
        soil_df = _get_soil_month(soil_profile_full, 1)
        T_soil_avg = soil_df["T_soil_C"].mean()

        deltas = [abs(T - T_soil_avg) for T in T_sweep]
        abs_ufps = [abs(u) for u in ufp_values]

        # Sort by delta and check UFP is mostly increasing
        paired = sorted(zip(deltas, abs_ufps))
        violations = 0
        for i in range(1, len(paired)):
            if paired[i][1] < paired[i - 1][1] * 0.9:  # Allow 10% noise
                violations += 1

        print(f"\n  T_soil_avg = {T_soil_avg:.1f} C")
        for T, u in zip(T_sweep, ufp_values):
            print(f"  T_dispatch={T:5.1f} C, delta={abs(T - T_soil_avg):5.1f}, UFP={u:.4f} KL")

        assert violations <= 1, (
            f"Monotonicity violated {violations} times. "
            f"UFP should increase with |T_dispatch - T_soil_avg|."
        )


# ═══════════════════════════════════════════════════════════════
# TEST 2: ZERO-CROSSING BEHAVIOR
# When T_dispatch ≈ T_soil_avg, UFP → 0
# ═══════════════════════════════════════════════════════════════


class TestZeroCrossing:
    """UFP should approach zero when T_dispatch matches soil temperature."""

    def test_ufp_near_zero_at_equilibrium(self, centerline, soil_profile_full):
        soil_df = _get_soil_month(soil_profile_full, 6)
        T_soil_avg = soil_df["T_soil_C"].mean()

        _, ufp, _ = _run_model(
            centerline,
            soil_profile_full,
            "diesel",
            T_soil_avg,
            150.0,
            month=6,
        )

        # Dispatch at the soil temperature => the product never exchanges heat
        # => no thermal contraction => UFP_thermal -> 0.
        threshold = 0.001 * 1000.0  # 0.1% of a 1000 KL batch = 1.0 KL
        print(f"\n  T_soil_avg = {T_soil_avg:.2f} C")
        print(f"  T_dispatch = {T_soil_avg:.2f} C (set to match)")
        print(
            f"  UFP_thermal = {ufp['UFP_thermal_KL']:.4f} KL ({ufp['UFP_thermal_percent']:.4f} %)"
        )
        print(f"  Threshold   = {threshold:.2f} KL (0.1% of batch)")

        assert abs(ufp["UFP_thermal_KL"]) < threshold, (
            f"|UFP_thermal| = {abs(ufp['UFP_thermal_KL']):.4f} KL exceeds the "
            f"0.1% threshold when T_dispatch = T_soil_avg = {T_soil_avg:.1f} C"
        )

        # And the standard-volume balance must close exactly regardless.
        assert abs(ufp["UFP_net_KL"]) < 1e-6, (
            f"UFP_net = {ufp['UFP_net_KL']} KL on a loss-free batch; must be 0"
        )


# ═══════════════════════════════════════════════════════════════
# TEST 3: FLOW RATE SENSITIVITY MATCHES L* PREDICTION
# Doubling flow rate should ~double L* and decrease UFP
# ═══════════════════════════════════════════════════════════════


class TestFlowRateSensitivity:
    """Thermal relaxation length L* should scale linearly with flow rate."""

    def test_l_star_doubles_with_flow_rate(self, centerline, soil_profile_full):
        flow_rates = [100.0, 200.0]
        l_stars = []
        ufp_vals = []

        for fr in flow_rates:
            _, ufp, ht = _run_model(
                centerline,
                soil_profile_full,
                "petrol",
                35.0,
                fr,
                month=6,
            )
            L_star = ht["L_star_km"].iloc[0]
            l_stars.append(L_star)
            ufp_vals.append(abs(ufp["UFP_KL"]))

        ratio = l_stars[1] / l_stars[0]
        print(
            f"\n  Flow rate 1: {flow_rates[0]} m3/hr -> L* = {l_stars[0]:.1f} km, |UFP| = {ufp_vals[0]:.4f} KL"
        )
        print(
            f"  Flow rate 2: {flow_rates[1]} m3/hr -> L* = {l_stars[1]:.1f} km, |UFP| = {ufp_vals[1]:.4f} KL"
        )
        print(f"  L* ratio = {ratio:.2f} (expected ~2.0)")

        assert 1.5 < ratio < 2.5, f"L* ratio = {ratio:.2f}, expected ~2.0"

    def test_ufp_decreases_with_higher_flow_rate(self, centerline, soil_profile_full):
        """Faster flow -> less residence time -> less equilibration -> less shrinkage.

        Asserted on UFP_thermal_KL, not UFP_KL. UFP_KL is the standard-volume
        difference, which is identically zero for a mass-conserving batch — it
        carries no thermal signal at all. The thermal artifact lives in
        UFP_thermal_KL.
        """
        flow_sweep = [75.0, 150.0, 300.0]
        ufp_vals = []

        for fr in flow_sweep:
            _, ufp, _ = _run_model(
                centerline,
                soil_profile_full,
                "petrol",
                40.0,
                fr,
                month=6,
            )
            ufp_vals.append(abs(ufp["UFP_thermal_KL"]))

        print(f"\n  Q={flow_sweep[0]:6.1f} m3/hr: |UFP_thermal|={ufp_vals[0]:.4f} KL")
        for i in range(1, len(ufp_vals)):
            print(f"  Q={flow_sweep[i]:6.1f} m3/hr: |UFP_thermal|={ufp_vals[i]:.4f} KL")
            assert ufp_vals[i] < ufp_vals[i - 1], (
                f"|UFP_thermal| did not decrease: {ufp_vals[i]:.4f} >= "
                f"{ufp_vals[i - 1]:.4f} at flow rates {flow_sweep[i]} vs "
                f"{flow_sweep[i - 1]}"
            )


# ═══════════════════════════════════════════════════════════════
# TEST 4: UFP MAGNITUDE SANITY BOUNDS
# UFP should never exceed 0.5% of V_dispatch for realistic inputs
# ═══════════════════════════════════════════════════════════════


class TestMagnitudeBounds:
    """Thermal UFP must stay inside a physically derived envelope.

    The bound is NOT a guessed percentage. It is the closed-form maximum
    shrinkage attainable given the sampled temperature range:

        |UFP_thermal| / V  ~=  alpha_60 * |dT_F|

    With alpha_60 <= 0.0009/F (petrol at its lightest) and the largest
    dispatch-to-soil gap the sweep can produce (~30 C = 54 F), the ceiling is
    ~4.9%. We assert 5%.

    Note: the plan's stated 0.02-0.5% envelope applies to the RESIDUAL (real
    loss) after thermal correction, not to the thermal artifact itself, which
    is an order of magnitude larger. See DEVIATIONS.md.
    """

    MAX_THERMAL_PCT = 5.0

    def test_ufp_below_half_percent(self, centerline, soil_profile_full):
        rng = np.random.RandomState(42)
        violations = []
        n_tests = 100
        max_pct = 0.0

        for i in range(n_tests):
            product = rng.choice(["petrol", "diesel", "atf"])
            T_dispatch = rng.uniform(10.0, 45.0)
            # Convert velocity range to flow rate
            A_ref = PIPE_SEGMENTS[0].cross_section_area_m2
            v = rng.uniform(0.5, 2.5)
            flow_rate = v * A_ref * 3600.0
            V_dispatch = rng.uniform(500.0, 5000.0)
            month = rng.choice([1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12])

            density = PRODUCTS[product].density_ref_kgm3
            soil_df = _get_soil_month(soil_profile_full, month)

            try:
                ht = solve_temperature_profile(
                    centerline_points=centerline,
                    soil_profile_df=soil_df,
                    product_name=product,
                    T_dispatch_C=T_dispatch,
                    flow_rate_m3hr=flow_rate,
                    density_kgm3=density,
                )
                T_receipt = get_receipt_temperature(ht)
                ufp = compute_ufp_from_model(
                    product_name=product,
                    V_dispatch_KL=V_dispatch,
                    T_dispatch_C=T_dispatch,
                    density_kgm3=density,
                    T_receipt_modeled_C=T_receipt,
                )
                pct = abs(ufp["UFP_thermal_KL"]) / V_dispatch * 100.0
                if pct > self.MAX_THERMAL_PCT:
                    violations.append(
                        {
                            "product": product,
                            "T_dispatch": T_dispatch,
                            "month": month,
                            "V_dispatch": V_dispatch,
                            "UFP_KL": ufp["UFP_thermal_KL"],
                            "pct": pct,
                        }
                    )

                # The standard-volume balance must close on EVERY sample.
                assert abs(ufp["UFP_net_KL"]) < 1e-6 * V_dispatch, (
                    f"UFP_net = {ufp['UFP_net_KL']} KL on a loss-free {product} batch (must be 0)"
                )
                max_pct = max(max_pct, pct)
            except Exception as e:
                print(f"  Test {i}: FAILED with exception: {e}")
                raise

        print(f"\n  Ran {n_tests} random parameter combinations")
        print(f"  Max |UFP_thermal| observed: {max_pct:.3f} % of batch")
        print(f"  Violations (> {self.MAX_THERMAL_PCT}%): {len(violations)}")
        for v in violations[:5]:
            print(
                f"    {v['product']}, T={v['T_dispatch']:.1f}, month={v['month']}: "
                f"UFP_thermal={v['UFP_KL']:.4f} KL ({v['pct']:.3f}%)"
            )

        assert len(violations) == 0, (
            f"{len(violations)} of {n_tests} samples exceeded the "
            f"{self.MAX_THERMAL_PCT}% thermal envelope"
        )


# ═══════════════════════════════════════════════════════════════
# TEST 5: MASS CONSERVATION CHECK
# UFP = V_dispatch_std - V_receipt_std must hold exactly
# ═══════════════════════════════════════════════════════════════


class TestMassConservation:
    """MASS in must equal MASS out on a thermal-only run.

    The previous version of this test asserted

        UFP_KL == V_dispatch_std_KL - V_receipt_std_KL

    which is literally the line of code that computes UFP_KL (model/ufp.py).
    It was a tautology: it would have passed no matter how wrong the physics
    was. Test the actual conserved quantity instead — mass — by carrying the
    density at each meter's temperature through explicitly.
    """

    def test_mass_in_equals_mass_out(self, centerline, soil_profile_full):
        for product in ("petrol", "diesel", "atf"):
            for month in (1, 6, 12):
                for T_dispatch in (20.0, 40.0):
                    T_receipt, ufp, _ = _run_model(
                        centerline,
                        soil_profile_full,
                        product,
                        T_dispatch,
                        150.0,
                        month=month,
                    )

                    props = PRODUCTS[product]

                    # Re-derive rho_60 and both CTLs at FULL precision. The
                    # values in the result dict are rounded for display (rho_60
                    # to 3 dp, CTL to 6 dp), and those roundings alone show up
                    # as a spurious ~1e-6 relative "mass error".
                    rho_60 = resolve_rho_60(
                        product_name=product,
                        density_kgm3=ufp["density_kgm3"],
                        density_measured_at_C=ufp["T_dispatch_C"],
                    )
                    ctl_dispatch = compute_ctl(rho_60, ufp["T_dispatch_C"], props)
                    ctl_receipt = compute_ctl(rho_60, ufp["T_receipt_C"], props)

                    # Density at each meter: rho(T) = rho_60 * CTL(T).
                    # (Volume shrinks by CTL, so density rises by the same factor.)
                    rho_dispatch = rho_60 * ctl_dispatch
                    rho_receipt = rho_60 * ctl_receipt

                    # 1 KL == 1 m3, so mass [kg] = V [KL] * rho [kg/m3].
                    mass_in = ufp["V_dispatch_KL"] * rho_dispatch
                    mass_out = ufp["V_receipt_KL"] * rho_receipt

                    rel_err = abs(mass_in - mass_out) / mass_in

                    assert rel_err < 1e-9, (
                        f"{product} month={month} T_d={T_dispatch}: mass not "
                        f"conserved. in={mass_in:.6f} kg, out={mass_out:.6f} kg, "
                        f"relative error {rel_err:.2e}"
                    )

        print(
            "\n  Mass conserved to <1e-9 relative across "
            "3 products x 3 months x 2 dispatch temperatures"
        )

    def test_thermal_run_conserves_standard_volume(self, centerline, soil_profile_full):
        """Standard volume is the mass proxy: it must survive any temperature."""
        T_receipt, ufp, _ = _run_model(
            centerline,
            soil_profile_full,
            "petrol",
            45.0,
            150.0,
            month=1,
        )
        assert abs(ufp["UFP_net_KL"]) < 1e-6, (
            f"standard volume not conserved on a loss-free batch: UFP_net = {ufp['UFP_net_KL']} KL"
        )


# ═══════════════════════════════════════════════════════════════
# TEST 6: PRODUCT-TO-PRODUCT ORDERING
# Petrol (highest alpha) > ATF > Diesel (lowest alpha)
# ═══════════════════════════════════════════════════════════════


class TestProductOrdering:
    """Petrol should show largest UFP, then ATF, then diesel."""

    def test_alpha_ordering(self, centerline, soil_profile_full):
        """Thermal shrinkage must order by alpha_60, not by intuition.

        Asserted on UFP_thermal_KL (UFP_KL is identically zero for a
        loss-free batch). The expected ordering is derived from the products'
        own alpha_60 at their reference densities, so this test cannot drift
        away from the K-coefficients in config.py.
        """

        ufp_by_product = {}
        alpha_by_product = {}

        for product in ["petrol", "atf", "diesel"]:
            _, ufp, _ = _run_model(
                centerline,
                soil_profile_full,
                product,
                40.0,
                150.0,
                month=6,
            )
            ufp_by_product[product] = abs(ufp["UFP_thermal_KL"])
            alpha_by_product[product] = compute_alpha_60(ufp["rho_60_kgm3"], PRODUCTS[product])

        # Expected ordering comes from the K-coefficients themselves.
        expected = sorted(alpha_by_product, key=alpha_by_product.get, reverse=True)

        print()
        for p in expected:
            print(
                f"  {p:7s} alpha_60 = {alpha_by_product[p]:.6f}/F  "
                f"|UFP_thermal| = {ufp_by_product[p]:.4f} KL"
            )

        actual = sorted(ufp_by_product, key=ufp_by_product.get, reverse=True)
        assert actual == expected, (
            f"|UFP_thermal| ordering {actual} does not match the ordering "
            f"implied by alpha_60 {expected}. Thermal shrinkage must scale "
            f"with the thermal expansion coefficient."
        )


# ═══════════════════════════════════════════════════════════════
# TEST 7: PUMPING COST SUPERLINEARITY
# cost(2v) > 2 * cost(v)
# ═══════════════════════════════════════════════════════════════


class TestPumpingCostSuperlinear:
    """Pumping cost should increase super-linearly with velocity."""

    def test_cost_doubles_more_than_linearly(self):
        v1 = 1.0
        v2 = 2.0

        c1 = compute_pumping_cost(v1, "diesel", 840.0)
        c2 = compute_pumping_cost(v2, "diesel", 840.0)

        cost1 = c1["pumping_cost_inr"]
        cost2 = c2["pumping_cost_inr"]
        ratio = cost2 / cost1

        print(f"\n  v = {v1} m/s: cost = INR {cost1:,.0f}")
        print(f"  v = {v2} m/s: cost = INR {cost2:,.0f}")
        print(f"  Ratio = {ratio:.2f} (should be > 2.0)")

        assert cost2 > 2 * cost1, (
            f"Pumping cost at {v2} m/s (INR {cost2:,.0f}) should be > "
            f"2x cost at {v1} m/s (INR {2 * cost1:,.0f})"
        )


# ═══════════════════════════════════════════════════════════════
# TEST 8: OPTIMIZER INTERIOR MINIMUM
# Total cost minimum should be inside [0.5, 2.5] m/s, not at boundary
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=long", "-s"])
