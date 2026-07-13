"""
Simulator Validation — conservation laws and the standard-volume invariant
==========================================================================

No ground truth exists until BPCL supply measured data, so the simulator is
validated against the conservation laws it is built from. A model that does not
conserve energy, mass and momentum is wrong regardless of how plausible its
plots look.

  1. STANDARD VOLUME INVARIANCE  — the project objective's own acceptance test:
     "the standard volume at 15 C should remain essentially constant while the
     gross volume changes with temperature."

  2. ENERGY CONSERVATION — the enthalpy rise across the line must equal the heat
     exchanged with the soil plus the work dissipated by friction.

  3. MOMENTUM CLOSURE — the pressure drop must equal friction plus static head.

  4. MASS CONSERVATION — m_dot is constant, so rho*u*A is invariant.

  5. MARCHING vs ANALYTIC — in the constant-soil, frictionless limit the coupled
     solver must reproduce the original validated closed form to <0.1 C. This is
     how the added terms are shown not to have corrupted the validated physics.

Run: pytest kota_bijwasan_ufp/validation/test_simulator.py -v
"""

import math
import os
import sys
import warnings

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import K_STEEL_WMK, PRODUCTS
from geo.route import Route
from model.heat_transfer import compute_U_value
from model.kernel import (
    G,
    HydraulicFeasibilityWarning,
    SimulationInputs,
    analytic_reference,
    simulate,
)
from model.soil_profile import _load_soil_csv, available_months

# Flow rates chosen to be hydraulically feasible on this route (see the
# slack-flow guard in kernel.py — 500 m3/hr of diesel from 70 bar is not).
FEASIBLE_FLOW = {"petrol": 400.0, "diesel": 300.0, "atf": 350.0}
P_DISPATCH_BAR = 70.0


@pytest.fixture(scope="module")
def route():
    return Route.from_csv()


@pytest.fixture(scope="module")
def soil_all():
    return _load_soil_csv()


def soil_for(soil_all, month):
    return (
        soil_all[soil_all["month"] == month]
        .rename(columns={"waypoint_km": "km"})
        .reset_index(drop=True)
    )


def run(route, soil_all, product, T_dispatch, month, flow=None, **kw):
    inp = SimulationInputs(
        product=product,
        T_dispatch_C=T_dispatch,
        V_dispatch_KL=1000.0,
        flow_rate_m3hr=flow if flow is not None else FEASIBLE_FLOW[product],
        month=month,
        P_dispatch_bar=P_DISPATCH_BAR,
        **kw,
    )
    return simulate(inp, route, soil_for(soil_all, month))


# ═══════════════════════════════════════════════════════════════════
# 1. STANDARD VOLUME INVARIANCE — the objective's acceptance test
# ═══════════════════════════════════════════════════════════════════


class TestStandardVolumeInvariance:
    """V_std constant along x; V_gross varies with temperature."""

    def test_v_std_is_invariant_everywhere(self, route, soil_all):
        """Across every product, month and dispatch temperature."""
        worst = 0.0
        worst_case = None

        for product in ("petrol", "diesel", "atf"):
            for month in available_months():
                for T in (20.0, 35.0, 45.0):
                    df = run(route, soil_all, product, T, month)

                    v0 = df["V_std_KL"].iloc[0]
                    drift = (df["V_std_KL"] - v0).abs().max() / v0 * 100.0

                    if drift > worst:
                        worst, worst_case = drift, (product, month, T)

                    assert drift < 0.01, (
                        f"{product} month={month} T={T}: V_std drifted "
                        f"{drift:.2e} % along the line — the VCF chain is not "
                        f"internally consistent."
                    )

        print(
            f"\n  Worst V_std drift over "
            f"{3 * len(available_months()) * 3} runs: {worst:.2e} % "
            f"({worst_case})"
        )

    def test_v_gross_actually_changes(self, route, soil_all):
        """The counterpart: gross volume MUST breathe, or nothing is happening.

        A V_std invariance test passes trivially if the simulator simply never
        changes any volume. Prove the thermal effect is real and visible.
        """
        # January: soil ~23 C at 1.2 m, so a 45 C batch cools hard.
        df = run(route, soil_all, "petrol", 45.0, 1)

        v_in = df["V_gross_KL"].iloc[0]
        v_out = df["V_gross_KL"].iloc[-1]
        swing_pct = abs(v_out - v_in) / v_in * 100.0

        print(f"\n  petrol 45 C -> {df['T_C'].iloc[-1]:.1f} C in January")
        print(f"  V_gross: {v_in:.2f} -> {v_out:.2f} KL  ({swing_pct:.2f} %)")
        print(f"  V_std  : {df['V_std_KL'].iloc[0]:.4f} -> {df['V_std_KL'].iloc[-1]:.4f} KL")

        assert swing_pct > 1.0, (
            f"gross volume only moved {swing_pct:.3f} % — the simulator is not "
            f"showing thermal contraction at all"
        )

    def test_cooling_contracts_warming_expands(self, route, soil_all):
        """Sign check, in both directions, driven by the soil."""
        # Dispatch well ABOVE soil -> product cools -> gross volume shrinks.
        hot = run(route, soil_all, "petrol", 45.0, 1)
        assert hot["T_C"].iloc[-1] < 45.0
        assert hot["V_gross_KL"].iloc[-1] < hot["V_gross_KL"].iloc[0], (
            "product cooled but its gross volume did not contract"
        )

        # Dispatch well BELOW soil -> product warms -> gross volume grows.
        cold = run(route, soil_all, "petrol", 15.0, 6)
        assert cold["T_C"].iloc[-1] > 15.0
        assert cold["V_gross_KL"].iloc[-1] > cold["V_gross_KL"].iloc[0], (
            "product warmed but its gross volume did not expand"
        )

        print(
            f"\n  Jan, dispatched 45 C: T -> {hot['T_C'].iloc[-1]:.1f} C, "
            f"V_gross {hot['V_gross_KL'].iloc[0]:.1f} -> "
            f"{hot['V_gross_KL'].iloc[-1]:.1f} KL  (contracts)"
        )
        print(
            f"  Jun, dispatched 15 C: T -> {cold['T_C'].iloc[-1]:.1f} C, "
            f"V_gross {cold['V_gross_KL'].iloc[0]:.1f} -> "
            f"{cold['V_gross_KL'].iloc[-1]:.1f} KL  (expands)"
        )


# ═══════════════════════════════════════════════════════════════════
# 2. ENERGY CONSERVATION
# ═══════════════════════════════════════════════════════════════════


class TestEnergyConservation:
    """Enthalpy rise = soil heat exchange + viscous dissipation."""

    def test_energy_balance_closes(self, route, soil_all):
        for product in ("petrol", "diesel", "atf"):
            for month in (1, 6, 12):
                df = run(route, soil_all, product, 40.0, month)

                m_dot = df.attrs["m_dot_kgs"]
                cp = PRODUCTS[product].cp_jkgk
                x = df["km"].values * 1000.0

                # LHS: enthalpy change of the stream [W]
                dH = m_dot * cp * (df["T_C"].iloc[-1] - df["T_C"].iloc[0])

                # RHS term 1: heat exchanged with the soil [W]
                q_soil = -(
                    df["U_Wm2K"].values
                    * math.pi
                    * (16.0 * 0.0254)  # D_outer, uniform on this route
                    * (df["T_C"].values - df["T_soil_C"].values)
                )
                Q_soil = np.trapezoid(q_soil, x)

                # RHS term 2: viscous dissipation [W]
                D_i = route.d_inner(0.0)
                q_visc = (
                    m_dot
                    * df["friction_factor"].values
                    * df["velocity_ms"].values ** 2
                    / (2.0 * D_i)
                )
                Q_visc = np.trapezoid(q_visc, x)

                rhs = Q_soil + Q_visc
                denom = max(abs(dH), abs(Q_soil), 1.0)
                rel_err = abs(dH - rhs) / denom

                print(
                    f"\n  {product} month={month}: "
                    f"dH={dH / 1e3:9.2f} kW | soil={Q_soil / 1e3:9.2f} kW | "
                    f"visc={Q_visc / 1e3:7.2f} kW | err={rel_err:.2e}"
                )

                assert rel_err < 1e-3, (
                    f"{product} month={month}: energy not conserved. "
                    f"dH={dH:.1f} W, soil+visc={rhs:.1f} W, "
                    f"relative error {rel_err:.2e}"
                )

    def test_viscous_dissipation_is_not_negligible(self, route, soil_all):
        """Guard the physics we added: prove friction heating matters.

        If someone later 'simplifies' the energy equation by dropping the
        dissipation term, this must fail loudly.
        """
        for product in ("petrol", "diesel", "atf"):
            with_heat = run(route, soil_all, product, 40.0, 1)
            without = run(route, soil_all, product, 40.0, 1, include_viscous_heating=False)

            delta = with_heat["T_C"].iloc[-1] - without["T_C"].iloc[-1]
            u = with_heat["velocity_ms"].iloc[0]
            print(
                f"\n  {product:7s} u={u:.2f} m/s -> viscous heating "
                f"raises T_receipt by {delta:+.3f} C"
            )

            assert delta > 0.25, (
                f"{product}: viscous heating changed T_receipt by only "
                f"{delta:.3f} C. Either the dissipation term has been dropped "
                f"or the friction model is broken."
            )

    def test_viscous_heating_obeys_its_scaling_law(self, route, soil_all):
        """Pin the FORM of the dissipation term, not merely its size.

        Derivation
        ----------
        The dissipation SOURCE term in the energy equation is

            S = f * u^2 / (2 * D_i * Cp)          [C per metre]      ~ u^2

        but the temperature RISE observed at the receipt end is not the integral
        of S — it is the equilibrium reached against soil cooling. Far
        downstream the balance is

            0 = -(T - T_soil) / L*  +  S     =>   dT_eq = S * L*

        and the thermal relaxation length itself grows with flow,

            L* = m_dot * Cp / (U * pi * D_o)   ~  u

        so the receipt-end temperature rise scales as

            dT  ~  S * L*  ~  u^2 * u  =  u^3

        Finally the friction factor drifts weakly with Reynolds number
        (Blasius-like, f ~ Re^-0.2 ~ u^-0.2), pulling the exponent down to

            dT  ~  u^(3 - 0.2)  =  u^2.8

        A model whose exponent is near 2 would mean L* is NOT growing with flow;
        a model near 3 would mean the friction factor is not responding to Re.
        Both would be bugs. This test is therefore a sharp check on the coupling
        between the energy equation, the relaxation length, and the friction
        model all at once.
        """
        flows = [200.0, 300.0, 400.0]
        deltas, us = [], []

        for Q in flows:
            a = run(route, soil_all, "petrol", 40.0, 1, flow=Q)
            b = run(route, soil_all, "petrol", 40.0, 1, flow=Q, include_viscous_heating=False)
            deltas.append(a["T_C"].iloc[-1] - b["T_C"].iloc[-1])
            us.append(a["velocity_ms"].iloc[0])

        # Fit the exponent:  log(dT) = n*log(u) + c
        n = float(np.polyfit(np.log(us), np.log(deltas), 1)[0])

        print("\n  Q [m3/hr]   u [m/s]   dT_visc [C]")
        for Q, u, d in zip(flows, us, deltas):
            print(f"  {Q:9.0f} {u:9.3f} {d:12.4f}")
        print(f"  fitted exponent n = {n:.2f}   (theory: dT ~ u^2.8)")

        assert 2.5 < n < 3.1, (
            f"viscous heating scales as u^{n:.2f}; theory requires ~u^2.8 "
            f"(source ~u^2, relaxation length ~u, friction factor ~u^-0.2). "
            f"An exponent near 2 means L* is not growing with flow; near 3 "
            f"means the friction factor is not responding to Reynolds number."
        )


# ═══════════════════════════════════════════════════════════════════
# 3. MOMENTUM CLOSURE
# ═══════════════════════════════════════════════════════════════════


class TestMomentumClosure:
    """dP = friction + static head, and nothing else."""

    def test_pressure_drop_decomposes(self, route, soil_all):
        df = run(route, soil_all, "petrol", 35.0, 6)

        x = df["km"].values * 1000.0
        D_i = route.d_inner(0.0)

        # Friction gradient, recomputed from the reported state.
        dP_fric = -(
            df["friction_factor"].values
            * df["rho_kgm3"].values
            * df["velocity_ms"].values ** 2
            / (2.0 * D_i)
        )
        P_fric = np.trapezoid(dP_fric, x)

        # Static head: -rho*g*dz, integrated along the route.
        dzdx = np.gradient(df["elevation_m"].values, x)
        dP_elev = -df["rho_kgm3"].values * G * dzdx
        P_elev = np.trapezoid(dP_elev, x)

        actual = (df["P_bar"].iloc[-1] - df["P_bar"].iloc[0]) * 1e5
        predicted = P_fric + P_elev

        rel_err = abs(actual - predicted) / abs(actual)

        print(f"\n  dP total     = {actual / 1e5:8.3f} bar")
        print(f"  dP friction  = {P_fric / 1e5:8.3f} bar")
        print(f"  dP elevation = {P_elev / 1e5:8.3f} bar  (Kota 271 m -> Bijwasan 215 m)")
        print(f"  closure error= {rel_err:.2e}")

        assert rel_err < 5e-3, (
            f"momentum not closing: actual {actual:.0f} Pa vs "
            f"friction+head {predicted:.0f} Pa (rel err {rel_err:.2e})"
        )

    def test_elevation_term_is_not_negligible(self, route, soil_all):
        """Guard against anyone re-stubbing elevation to zero."""
        with_elev = run(route, soil_all, "petrol", 35.0, 6)
        without = run(route, soil_all, "petrol", 35.0, 6, include_elevation=False)

        delta = abs(with_elev["P_bar"].iloc[-1] - without["P_bar"].iloc[-1])
        print(f"\n  elevation shifts P_receipt by {delta:.2f} bar")

        assert delta > 1.0, (
            f"elevation moved the receipt pressure by only {delta:.3f} bar. "
            f"The old plan proposed stubbing dz to zero; 56 m of fall is "
            f"~4 bar and must not be discarded."
        )


# ═══════════════════════════════════════════════════════════════════
# 4. MASS CONSERVATION
# ═══════════════════════════════════════════════════════════════════


class TestMassConservation:
    def test_mass_flow_is_constant(self, route, soil_all):
        """rho * u * A must not drift, whatever T and P do."""
        for product in ("petrol", "diesel", "atf"):
            df = run(route, soil_all, product, 40.0, 1)

            A = route.area(0.0)
            m_dot_x = df["rho_kgm3"].values * df["velocity_ms"].values * A
            m_dot_0 = df.attrs["m_dot_kgs"]

            drift = np.abs(m_dot_x - m_dot_0).max() / m_dot_0

            print(f"\n  {product}: m_dot = {m_dot_0:.4f} kg/s, max drift {drift:.2e}")

            # Tolerance is set by the output precision (rho and velocity are
            # reported to 8 dp), not by the physics — which conserves mass
            # exactly by construction.
            assert drift < 1e-7, f"{product}: mass flow drifted by {drift:.2e} along the line"


# ═══════════════════════════════════════════════════════════════════
# 5. MARCHING SOLVER vs THE VALIDATED ANALYTIC SOLUTION
# ═══════════════════════════════════════════════════════════════════


class TestAgainstAnalyticReference:
    """The coupled solver must reduce to the original closed form.

    In the constant-soil, frictionless, flat limit the governing equation
    collapses to  dT/dx = -(T - T_soil)/L*,  whose solution is

        T(x) = T_soil + (T_in - T_soil) * exp(-x / L*)

    This is the validated physics the project started from. Reproducing it
    proves that adding viscous dissipation, elevation and CPL did not corrupt
    the heat-transfer core.
    """

    def test_matches_closed_form_in_the_constant_soil_limit(self, route):
        T_SOIL = 25.0
        K_SOIL = 1.2
        T_IN = 45.0
        FLOW = 400.0
        PRODUCT = "petrol"

        # Constant soil along the whole route.
        soil = pd.DataFrame(
            {
                "km": [p.km for p in route.points],
                "T_soil_C": T_SOIL,
                "k_soil_WmK": K_SOIL,
            }
        )

        # Flat route: temporarily zero the elevation gradient.
        flat = Route.from_csv()
        for w in flat.waypoints:
            object.__setattr__(w, "elevation_m", 200.0)
        flat._wp_elev = np.full_like(flat._wp_elev, 200.0)

        inp = SimulationInputs(
            product=PRODUCT,
            T_dispatch_C=T_IN,
            V_dispatch_KL=1000.0,
            flow_rate_m3hr=FLOW,
            month=1,
            P_dispatch_bar=70.0,
            include_viscous_heating=False,  # switch off the ADDED physics
            include_elevation=False,  # so we recover the original model
            include_pressure_correction=False,
        )
        df = simulate(inp, flat, soil)

        # L* from the solver's own reported U — constant on this uniform route.
        m_dot = df.attrs["m_dot_kgs"]
        cp = PRODUCTS[PRODUCT].cp_jkgk
        D_o = route.d_outer(0.0)
        U = compute_U_value(
            D_o,
            route.d_inner(0.0),
            K_SOIL,
            burial_depth_m=route.burial_depth(0.0),
            k_steel_WmK=K_STEEL_WMK,
        )
        L_star_km = (m_dot * cp) / (U * math.pi * D_o) / 1000.0

        expected = analytic_reference(T_IN, T_SOIL, L_star_km, df["km"].values)
        max_dev = float(np.abs(df["T_C"].values - expected).max())

        print(f"\n  L*            = {L_star_km:.1f} km")
        print(f"  T analytic    = {expected[-1]:.4f} C at receipt")
        print(f"  T marching    = {df['T_C'].iloc[-1]:.4f} C at receipt")
        print(f"  max deviation = {max_dev:.5f} C  (tolerance 0.1 C)")

        assert max_dev < 0.1, (
            f"the coupled marching solver deviates from the validated closed "
            f"form by {max_dev:.4f} C in the constant-soil limit. The added "
            f"terms have corrupted the heat-transfer core."
        )


# ═══════════════════════════════════════════════════════════════════
# 6. HYDRAULIC FEASIBILITY
# ═══════════════════════════════════════════════════════════════════


class TestHydraulicFeasibility:
    def test_slack_flow_is_detected_and_warned(self, route, soil_all):
        """A negative gauge pressure must never be returned silently."""
        inp = SimulationInputs(
            product="diesel",
            T_dispatch_C=40.0,
            V_dispatch_KL=1000.0,
            flow_rate_m3hr=500.0,
            month=1,
            P_dispatch_bar=70.0,
        )
        with pytest.warns(HydraulicFeasibilityWarning, match="SLACK FLOW"):
            df = simulate(inp, route, soil_for(soil_all, 1))

        assert df.attrs["hydraulically_feasible"] is False
        assert df.attrs["slack_flow_onset_km"] is not None
        print(
            f"\n  slack flow onset at km "
            f"{df.attrs['slack_flow_onset_km']:.0f}, "
            f"P_min = {df.attrs['P_min_bar']:.1f} bar"
        )

    def test_feasible_case_raises_nothing(self, route, soil_all):
        with warnings.catch_warnings():
            warnings.simplefilter("error", HydraulicFeasibilityWarning)
            df = run(route, soil_all, "diesel", 40.0, 1)  # 300 m3/hr
        assert df.attrs["hydraulically_feasible"] is True
        assert df["P_bar"].min() > 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-s"])
