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

from config import FLOW_SPLITS, K_STEEL_WMK, PRODUCTS, FlowSplit
from geo.route import Route, Waypoint
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
#
# Set for the documented MMBPL geometry: an 18" mainline (Bina->Piyala) with an
# 8" tail (Piyala->Bijwasan). The binding constraint is the TAIL, not the
# mainline — its bore is 4.5x smaller in area, so the same volumetric flow runs
# 4.5x faster there. At these rates the tail sits at 2.1-2.6 m/s while the
# mainline idles at 0.5-0.6 m/s.
#
# Verified as the worst case across every month in the soil table at 40 C and
# 70 bar dispatch, both with and without booster stations: min P stays above
# +20 bar in every combination.
FEASIBLE_FLOW = {"petrol": 300.0, "diesel": 250.0, "atf": 275.0}
P_DISPATCH_BAR = 70.0


@pytest.fixture(scope="module")
def route():
    return Route.from_csv()


@pytest.fixture(scope="module")
def route_fine():
    """A 0.25 km centreline, for checks that integrate along the route.

    The energy-balance check below closes the budget with a trapezoid sum over
    the reported per-point heat fluxes. That quadrature is second-order in the
    grid spacing, and on the NPS 8 bore the thermal relaxation length is only
    ~7 km, so the temperature transient in the first few kilometres is sharp
    enough that a 1 km grid contributes more error than the model does:

        grid     rel. closure error
        1.00 km       1.3e-3
        0.50 km       3.4e-4
        0.25 km       8.4e-5
        0.10 km       7.1e-6

    Clean h^2 convergence — the imbalance is the test's own integration, not
    the solver, which runs at rtol=1e-8. Refining the grid keeps the tolerance
    tight instead of loosening it to absorb a known numerical artefact.
    """
    return Route.from_csv(resolution_km=0.25)


@pytest.fixture(scope="module")
def route_uniform():
    """The same route, forced to a single 18" bore end to end.

    Several checks below verify a PHYSICAL LAW by re-integrating a gradient the
    kernel reported — energy closure, momentum closure, the u^2.8 dissipation
    scaling. All three assume the gradient is continuous.

    The real route is not: at Piyala the bore steps 18" -> 8", area falls 4.5x,
    and velocity and friction gradient jump with it. A trapezoid across that
    step averages the two sides and mis-attributes ~1 bar, and because the
    integrand is DISCONTINUOUS the error converges only linearly — refining the
    grid does not rescue it the way it does for a merely sharp transient.

    So the laws are verified on a uniform bore, where the quadrature is valid,
    and the real two-diameter route is used for everything that is about this
    pipeline rather than about physics: feasibility, the tail choke, the pump
    stations, and standard-volume invariance.
    """
    base = Route.from_csv()
    uniform = [
        Waypoint(
            waypoint_id=w.waypoint_id,
            name=w.name,
            lat=w.lat,
            lon=w.lon,
            chainage_km=w.chainage_km,
            elevation_m=w.elevation_m,
            od_inch=18.0,
            wall_thickness_mm=14.27,
            roughness_mm=w.roughness_mm,
            burial_depth_m=w.burial_depth_m,
            station_type=w.station_type,
            source="TEST_UNIFORM_18IN",
        )
        for w in base.waypoints
    ]
    return Route(uniform)


@pytest.fixture(scope="module")
def soil_all():
    return _load_soil_csv()


def soil_for(soil_all, month):
    return (
        soil_all[soil_all["month"] == month]
        .rename(columns={"waypoint_km": "km"})
        .reset_index(drop=True)
    )


def run(
    route,
    soil_all,
    product,
    T_dispatch,
    month,
    flow=None,
    pump_stations=None,
    flow_splits=None,
    **kw,
):
    """One simulation.

    `pump_stations=[]` models the single-pump case; `flow_splits=[]` sends the
    whole batch end to end with no mid-route delivery.
    """
    inp = SimulationInputs(
        product=product,
        T_dispatch_C=T_dispatch,
        V_dispatch_KL=1000.0,
        flow_rate_m3hr=flow if flow is not None else FEASIBLE_FLOW[product],
        month=month,
        P_dispatch_bar=P_DISPATCH_BAR,
        **kw,
    )
    return simulate(
        inp,
        route,
        soil_for(soil_all, month),
        pump_stations=pump_stations,
        flow_splits=flow_splits,
    )


# ═══════════════════════════════════════════════════════════════════
# 1. STANDARD VOLUME INVARIANCE — the objective's acceptance test
# ═══════════════════════════════════════════════════════════════════


class TestStandardVolumeInvariance:
    """V_std constant along x; V_gross varies with temperature."""

    def test_v_std_is_invariant_everywhere(self, route, soil_all):
        """Across every product, month and dispatch temperature.

        The project's headline acceptance test, in its strong form: the batch
        travels Kota -> Bijwasan intact, so the standard volume must be the SAME
        NUMBER at every chainage, not merely conserved as a balance. Grouping by
        `mass_fraction_remaining` keeps the check correct if a delivery is ever
        configured, but with the shipped config there is exactly one group.
        """
        worst = 0.0
        worst_case = None

        for product in ("petrol", "diesel", "atf"):
            for month in available_months():
                for T in (20.0, 35.0, 45.0):
                    df = run(route, soil_all, product, T, month)

                    # Group by how much mass is still in the line: constant
                    # within a segment, stepping down at each delivery.
                    for _frac, seg in df.groupby("mass_fraction_remaining"):
                        v0 = seg["V_std_KL"].iloc[0]
                        drift = (seg["V_std_KL"] - v0).abs().max() / v0 * 100.0

                        if drift > worst:
                            worst, worst_case = drift, (product, month, T)

                        assert drift < 0.01, (
                            f"{product} month={month} T={T}: V_std drifted "
                            f"{drift:.2e} % within a segment — the VCF chain is "
                            f"not internally consistent."
                        )

        print(
            f"\n  Worst within-segment V_std drift over "
            f"{3 * len(available_months()) * 3} runs: {worst:.2e} % "
            f"({worst_case})"
        )

    def test_v_std_is_invariant_end_to_end_without_a_delivery(self, route, soil_all):
        """With no split, the original invariance must still hold exactly."""
        df = run(route, soil_all, "petrol", 40.0, 1, flow_splits=[])
        v0 = df["V_std_KL"].iloc[0]
        drift = (df["V_std_KL"] - v0).abs().max() / v0 * 100.0
        assert drift < 0.01, f"V_std drifted {drift:.2e} % with no delivery"

    def test_v_gross_actually_changes(self, route, soil_all):
        """The counterpart: gross volume MUST breathe, or nothing is happening.

        A V_std invariance test passes trivially if the simulator simply never
        changes any volume. Prove the thermal effect is real and visible.
        """
        # January: soil ~23 C at 1.2 m, so a 45 C batch cools hard.
        # No delivery, so gross volume changes only because the product does.
        df = run(route, soil_all, "petrol", 45.0, 1, flow_splits=[])

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
        # flow_splits=[] so gross volume tracks the product alone: a delivery
        # would drop it 78 % at Piyala regardless of which way T moved.
        # Dispatch well ABOVE soil -> product cools -> gross volume shrinks.
        hot = run(route, soil_all, "petrol", 45.0, 1, flow_splits=[])
        assert hot["T_C"].iloc[-1] < 45.0
        assert hot["V_gross_KL"].iloc[-1] < hot["V_gross_KL"].iloc[0], (
            "product cooled but its gross volume did not contract"
        )

        # Dispatch well BELOW soil -> product warms -> gross volume grows.
        cold = run(route, soil_all, "petrol", 15.0, 6, flow_splits=[])
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

    def test_energy_balance_closes(self, route_uniform, soil_all):
        # Uniform bore + 0.25 km grid: see the route_uniform and route_fine
        # fixtures for why each is needed.
        route = Route(route_uniform.waypoints, resolution_km=0.25)
        # flow_splits=[]: with a delivery the enthalpy budget must also account
        # for the enthalpy leaving with the delivered product, which is a
        # different (and separately tested) identity. This test is about the
        # soil + dissipation terms on a single continuous stream.
        for product in ("petrol", "diesel", "atf"):
            for month in (1, 6, 12):
                df = run(route, soil_all, product, 40.0, month, flow_splits=[])

                m_dot = df.attrs["m_dot_kgs"]
                cp = PRODUCTS[product].cp_jkgk
                x = df["km"].values * 1000.0

                # LHS: enthalpy change of the stream [W]
                dH = m_dot * cp * (df["T_C"].iloc[-1] - df["T_C"].iloc[0])

                # RHS term 1: heat exchanged with the environment [W].
                # The driving difference is against T_env = (T_air + T_soil)/2,
                # which collapses to T_soil when no air temperature was supplied
                # — as here, since this fixture never calls OpenWeather.
                q_soil = -(
                    df["U_Wm2K"].values
                    * math.pi
                    * df["D_outer_m"].values  # local bore: 18" main, 8" tail
                    * (df["T_C"].values - df["T_env_C"].values)
                )
                Q_soil = np.trapezoid(q_soil, x)

                # RHS term 2: viscous dissipation [W]
                q_visc = (
                    m_dot
                    * df["friction_factor"].values
                    * df["velocity_ms"].values ** 2
                    / (2.0 * df["D_inner_m"].values)
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

        Why this measures POWER, not the receipt temperature
        ----------------------------------------------------
        This used to assert that friction raised T_receipt by >0.25 C. That
        worked on the 16-inch route but is the wrong invariant, because it
        depends on the thermal relaxation length rather than on the dissipation
        term itself:

            L* = m_dot*Cp / (U*pi*D_o)      16" ~ tens of km,  NPS 8 ~ 7 km

        Once the line is many relaxation lengths long, the product reaches
        equilibrium with the environment and dissipation stops ACCUMULATING
        into the receipt temperature — it is balanced locally by heat loss to
        the ground and survives only as a small steady offset above T_env. On
        NPS 8 that offset is ~0.06-0.09 C, and shrinking further with diameter,
        so a receipt-temperature threshold would have to be re-tuned after
        every geometry change and would eventually be indistinguishable from
        noise.

        The dissipated POWER does not have that problem. It is 11-14 % of the
        heat exchanged with the ground here — thoroughly non-negligible, and
        geometry-independent as a statement. Dropping the term sends it to
        exactly zero, which this catches with an enormous margin.
        """
        for product in ("petrol", "diesel", "atf"):
            df = run(route, soil_all, product, 40.0, 1)
            without = run(route, soil_all, product, 40.0, 1, include_viscous_heating=False)

            x = df["km"].values * 1000.0

            Q_visc = np.trapezoid(
                df["m_dot_kgs"].values
                * df["friction_factor"].values
                * df["velocity_ms"].values ** 2
                / (2.0 * df["D_inner_m"].values),
                x,
            )
            Q_env = abs(
                np.trapezoid(
                    -(
                        df["U_Wm2K"].values
                        * math.pi
                        * df["D_outer_m"].values
                        * (df["T_C"].values - df["T_env_C"].values)
                    ),
                    x,
                )
            )
            share = Q_visc / Q_env
            delta = df["T_C"].iloc[-1] - without["T_C"].iloc[-1]

            print(
                f"\n  {product:7s} u={df['velocity_ms'].iloc[0]:.2f} m/s -> "
                f"Q_visc={Q_visc / 1e3:6.1f} kW ({share:.1%} of the "
                f"{Q_env / 1e3:6.1f} kW ground exchange), "
                f"T_receipt {delta:+.3f} C"
            )

            assert share > 0.05, (
                f"{product}: viscous dissipation is only {share:.2%} of the "
                f"energy budget ({Q_visc:.1f} W). Either the dissipation term "
                f"has been dropped or the friction model is broken."
            )
            assert delta > 0.0, (
                f"{product}: enabling viscous heating did not raise the "
                f"receipt temperature at all ({delta:+.4f} C) — the term is "
                f"not reaching the energy equation."
            )

    def test_viscous_heating_obeys_its_scaling_law(self, route_uniform, soil_all):
        route = route_uniform
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
            a = run(
                route, soil_all, "petrol", 40.0, 1, flow=Q,
                pump_stations=[], flow_splits=[],
            )
            b = run(
                route, soil_all, "petrol", 40.0, 1, flow=Q,
                include_viscous_heating=False, pump_stations=[], flow_splits=[],
            )
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

    def test_pressure_drop_decomposes(self, route_uniform, soil_all):
        route = route_uniform
        # Run WITHOUT booster stations: a pump is a step change in P, so with
        # them the profile is piecewise-continuous and "dP end-to-end = friction
        # + head" is simply not the right identity. Pump bookkeeping is checked
        # separately in TestPumpStations below; this test is about the momentum
        # equation the solver integrates between stations.
        df = run(route, soil_all, "petrol", 35.0, 6, pump_stations=[])

        x = df["km"].values * 1000.0

        # Friction gradient, recomputed from the reported state.
        dP_fric = -(
            df["friction_factor"].values
            * df["rho_kgm3"].values
            * df["velocity_ms"].values ** 2
            / (2.0 * df["D_inner_m"].values)
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
        with_elev = run(route, soil_all, "petrol", 35.0, 6, pump_stations=[])
        without = run(
            route, soil_all, "petrol", 35.0, 6, include_elevation=False, pump_stations=[]
        )

        delta = abs(with_elev["P_bar"].iloc[-1] - without["P_bar"].iloc[-1])
        print(f"\n  elevation shifts P_receipt by {delta:.2f} bar")

        assert delta > 1.0, (
            f"elevation moved the receipt pressure by only {delta:.3f} bar. "
            f"The old plan proposed stubbing dz to zero; 56 m of fall is "
            f"~4 bar and must not be discarded."
        )


# ═══════════════════════════════════════════════════════════════════
# 3b. INTERMEDIATE PUMP STATIONS
# ═══════════════════════════════════════════════════════════════════


class TestPumpStations:
    """Booster stations re-pressurise the line; the bookkeeping must close.

    A 360 km line is not pumped from one end. These pin that the boost is
    applied where it should be, that it is a step and not a smear, and that
    momentum still closes once the boosts are accounted for.
    """

    def test_boost_is_applied_at_each_station(self, route, soil_all):
        from config import PUMP_STATIONS  # noqa: PLC0415

        df = run(route, soil_all, "petrol", 40.0, 1)
        log = df.attrs["pump_stations"]

        assert len(log) == len(PUMP_STATIONS)
        for entry, station in zip(log, PUMP_STATIONS):
            assert entry["km"] == station.km

            # A booster raises pressure or passes it through — never lowers it.
            # After a long downhill run the product can arrive above the
            # station's setting, and the honest result is an idle station, not
            # a negative "boost" that discards head the terrain provided.
            assert entry["boost_bar"] >= 0.0, (
                f"{entry['name']} REDUCED pressure by "
                f"{-entry['boost_bar']:.2f} bar — a pump cannot throttle"
            )
            assert entry["P_discharge_bar"] >= entry["P_suction_bar"] - 1e-9
            if not entry["idle"]:
                assert entry["P_discharge_bar"] == pytest.approx(station.discharge_bar)

        assert any(e["boost_bar"] > 1.0 for e in log), (
            "no station did any real work — the boosters are not being applied"
        )

    def test_stations_have_adequate_suction(self, route, soil_all):
        """A booster that arrives at vacuum would cavitate, not pump."""
        df = run(route, soil_all, "petrol", 40.0, 1)
        assert df.attrs["cavitating_stations"] == [], (
            f"stations below minimum suction: {df.attrs['cavitating_stations']}"
        )

    def test_pressure_steps_up_at_a_station_and_falls_between(self, route, soil_all):
        """The profile is piecewise-continuous: sawtooth, not monotone."""
        df = run(route, soil_all, "petrol", 40.0, 1).set_index("km")
        km0 = df.attrs["pump_stations"][0]["km"]

        before = df.loc[km0 - 5.0, "P_bar"]
        after = df.loc[km0 + 5.0, "P_bar"]
        assert after > before, (
            f"pressure did not rise across the station at km {km0:.0f} "
            f"({before:.1f} -> {after:.1f} bar)"
        )

    def test_boosters_raise_the_receipt_pressure(self, route, soil_all):
        with_pumps = run(route, soil_all, "petrol", 40.0, 1)
        without = run(route, soil_all, "petrol", 40.0, 1, pump_stations=[])

        gain = with_pumps["P_bar"].iloc[-1] - without["P_bar"].iloc[-1]
        print(f"\n  booster stations add {gain:.1f} bar at Bijwasan")
        assert gain > 10.0, f"boosters only added {gain:.2f} bar end to end"

    def test_momentum_closes_once_boosts_are_accounted_for(self, route_uniform, soil_all):
        """End-to-end dP = friction + head + the sum of the pump boosts.

        On a uniform bore, so that the only discontinuities in the pressure
        profile are the pump boosts this test is about.
        """
        df = run(route_uniform, soil_all, "petrol", 35.0, 6)
        x = df["km"].values * 1000.0

        dP_fric = -(
            df["friction_factor"].values
            * df["rho_kgm3"].values
            * df["velocity_ms"].values ** 2
            / (2.0 * df["D_inner_m"].values)
        )
        dzdx = np.gradient(df["elevation_m"].values, x)
        dP_elev = -df["rho_kgm3"].values * G * dzdx

        boosts = sum(s["boost_bar"] for s in df.attrs["pump_stations"]) * 1e5
        predicted = np.trapezoid(dP_fric, x) + np.trapezoid(dP_elev, x) + boosts
        actual = (df["P_bar"].iloc[-1] - df["P_bar"].iloc[0]) * 1e5

        rel_err = abs(actual - predicted) / max(abs(actual), 1e5)
        print(
            f"\n  dP actual {actual / 1e5:8.2f} bar | friction+head+boosts "
            f"{predicted / 1e5:8.2f} bar | err {rel_err:.2e}"
        )
        assert rel_err < 5e-3, (
            f"momentum does not close with pump boosts included: "
            f"actual {actual:.0f} Pa vs predicted {predicted:.0f} Pa"
        )


# ═══════════════════════════════════════════════════════════════════
# 3c. FLOW SPLIT AT PIYALA
# ═══════════════════════════════════════════════════════════════════


PIYALA_SPLIT = [FlowSplit("Piyala delivery (hypothetical)", 599.0, 0.78)]


class TestFlowSplit:
    """The mid-route delivery MECHANISM.

    The shipped model has no delivery — the batch runs Kota -> Bijwasan intact,
    because this is a custody-transfer simulator and a mid-route off-take would
    sit inside the very dispatch-vs-receipt comparison it exists to make. See
    `test_the_shipped_configuration_has_no_delivery` below, which pins that.

    The mechanism is kept and tested anyway: the real MMBPL line does have a
    delivery terminal at Piyala, and modelling the wider system will need it.
    Every test here therefore passes its split EXPLICITLY rather than relying
    on the default configuration.
    """

    def test_the_shipped_configuration_has_no_delivery(self, route, soil_all):
        """Custody integrity: nothing may be taken off between the terminals.

        If someone adds a FlowSplit to config to "fix" a low throughput, this
        fails — and it should, because it would silently turn delivered product
        into apparent unaccounted-for product.
        """
        assert FLOW_SPLITS == [], (
            f"config.FLOW_SPLITS must be empty for the Kota->Bijwasan custody "
            f"model; found {FLOW_SPLITS}"
        )

        df = run(route, soil_all, "petrol", 40.0, 1)
        assert df.attrs["n_flow_splits"] == 0
        assert df.attrs["mass_fraction_delivered"] == pytest.approx(0.0)
        assert (df["mass_fraction_remaining"] == 1.0).all()
        assert df["V_std_KL"].iloc[-1] == pytest.approx(df["V_std_KL"].iloc[0], rel=1e-9)

    def test_a_split_lets_both_bores_run_at_design_velocity(self, route, soil_all):
        """Why the mechanism exists: 3 m/s in BOTH segments, simultaneously."""
        Q = 3.0 * route.area(0.0) * 3600.0  # 3 m/s in the 18" mainline
        df = run(route, soil_all, "petrol", 40.0, 1, flow=Q, flow_splits=PIYALA_SPLIT)

        u_main = df[df["km"] < 599.0]["velocity_ms"]
        u_tail = df[df["km"] >= 599.0]["velocity_ms"]

        print(
            f"\n  {Q:.0f} m3/hr -> mainline {u_main.mean():.2f} m/s, "
            f"tail {u_tail.mean():.2f} m/s"
        )
        assert u_main.mean() == pytest.approx(3.0, abs=0.05)
        assert u_tail.mean() == pytest.approx(3.0, abs=0.15), (
            f"tail runs at {u_tail.mean():.2f} m/s — the split is not sized to "
            f"match the bores"
        )

    def test_without_a_split_the_tail_limits_the_line(self, route, soil_all):
        """The shipped case: one flow through both bores, tail runs 4.47x faster.

        This is not a defect. It is the 8-inch final approach genuinely
        constraining throughput, and the reason the default flow rate is sized
        to the tail rather than to the mainline.
        """
        Q = 3.0 * route.area(0.0) * 3600.0
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", HydraulicFeasibilityWarning)
            df = run(route, soil_all, "petrol", 40.0, 1, flow=Q, flow_splits=[])

        u_tail = df["velocity_ms"].iloc[-1]
        print(f"\n  no split: tail runs at {u_tail:.1f} m/s")
        assert u_tail > 12.0, (
            "without a delivery the tail should be forced to an absurd velocity"
        )

    def test_mass_flow_steps_down_by_exactly_the_delivered_fraction(
        self, route, soil_all
    ):
        df = run(
            route, soil_all, "petrol", 40.0, 1, flow_splits=PIYALA_SPLIT
        ).set_index("km")
        before = df.loc[589.0, "m_dot_kgs"]
        after = df.loc[610.0, "m_dot_kgs"]
        assert after / before == pytest.approx(0.22, abs=1e-6)

    def test_standard_volume_balance_closes(self, route, soil_all):
        """V_std(dispatch) = delivered + received, exactly.

        This REPLACES end-to-end invariance as the conservation statement once
        product is legitimately taken off mid-route.
        """
        for product in ("petrol", "diesel", "atf"):
            df = run(route, soil_all, product, 40.0, 1, flow_splits=PIYALA_SPLIT)
            a = df.attrs

            total = a["V_std_delivered_KL"] + a["V_std_receipt_KL"]
            print(
                f"\n  {product}: dispatched {a['V_std_KL']:.3f} = delivered "
                f"{a['V_std_delivered_KL']:.3f} + received "
                f"{a['V_std_receipt_KL']:.3f} KL"
            )
            assert total == pytest.approx(a["V_std_KL"], abs=1e-6)
            assert abs(a["V_std_balance_error_KL"]) < 1e-6

    def test_receipt_volume_is_not_mistaken_for_a_loss(self, route, soil_all):
        """22 % arriving is a DELIVERY, not 78 % unaccounted-for product."""
        df = run(route, soil_all, "petrol", 40.0, 1, flow_splits=PIYALA_SPLIT)
        a = df.attrs

        assert a["mass_fraction_delivered"] == pytest.approx(0.78, abs=1e-9)
        assert a["V_std_receipt_KL"] / a["V_std_KL"] == pytest.approx(0.22, abs=1e-6)
        # The split must be reported, so a reader cannot miss why.
        assert a["n_flow_splits"] == 1
        assert a["flow_splits"][0]["km"] == 599.0

    def test_explicitly_disabling_a_split_matches_the_default(self, route, soil_all):
        df = run(route, soil_all, "petrol", 40.0, 1, flow_splits=[])
        a = df.attrs
        assert a["n_flow_splits"] == 0
        assert a["mass_fraction_delivered"] == pytest.approx(0.0)
        assert a["V_std_receipt_KL"] == pytest.approx(a["V_std_KL"], abs=1e-6)
        assert (df["mass_fraction_remaining"] == 1.0).all()


# ═══════════════════════════════════════════════════════════════════
# 4. MASS CONSERVATION
# ═══════════════════════════════════════════════════════════════════


class TestMassConservation:
    def test_mass_flow_matches_continuity_everywhere(self, route, soil_all):
        """rho * u * A must equal the LOCAL mass flow, whatever T and P do.

        Neither area nor mass flow is constant now: the bore steps at Piyala
        and so does m_dot, because 78 % of the batch is delivered there. Both
        are taken from the kernel's own per-point columns, so this still tests
        continuity rather than the geometry or the delivery.
        """
        for product in ("petrol", "diesel", "atf"):
            df = run(route, soil_all, product, 40.0, 1)

            m_dot_x = df["rho_kgm3"].values * df["velocity_ms"].values * df["area_m2"].values
            m_dot_local = df["m_dot_kgs"].values

            # Normalise POINT BY POINT: each point's error against its own
            # mass flow, so a thin segment is not charged with a thick one's
            # rounding quantum.
            drift = float((np.abs(m_dot_x - m_dot_local) / m_dot_local).max())

            print(
                f"\n  {product}: m_dot {m_dot_local.max():.4f} -> "
                f"{m_dot_local.min():.4f} kg/s, max continuity error {drift:.2e}"
            )

            # Tolerance is set by the output precision (rho and velocity are
            # reported to 8 dp), not by the physics — which conserves mass
            # exactly by construction.
            assert drift < 1e-7, f"{product}: continuity violated by {drift:.2e}"

            # Nothing is delivered en route, so the whole batch is still in the
            # line at every chainage.
            steps = sorted(set(np.round(df["mass_fraction_remaining"].values, 8)))
            assert steps == [pytest.approx(1.0)], (
                f"mass left the line between the terminals: fractions {steps}"
            )

    def test_mass_flow_is_constant_end_to_end(self, route, soil_all):
        """The strong invariant: one m_dot from Kota to Bijwasan."""
        df = run(route, soil_all, "petrol", 40.0, 1)
        m_dot_x = df["rho_kgm3"].values * df["velocity_ms"].values * df["area_m2"].values
        m_dot_0 = df.attrs["m_dot_kgs"]
        drift = np.abs(m_dot_x - m_dot_0).max() / m_dot_0
        assert drift < 1e-7, f"mass flow drifted by {drift:.2e} with no delivery"


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
        """A negative gauge pressure must never be returned silently.

        With no mid-route delivery the 8-inch tail carries the whole flow, so
        it is the tail that fails first: 800 m3/hr is only 1.54 m/s in the
        18-inch mainline but 6.9 m/s in the tail, far past any design velocity.
        """
        inp = SimulationInputs(
            product="diesel",
            T_dispatch_C=40.0,
            V_dispatch_KL=1000.0,
            flow_rate_m3hr=800.0,
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
