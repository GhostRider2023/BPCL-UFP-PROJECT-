"""
Coupled Thermal-Hydraulic Kernel
=================================

Marches the product's physical state along the pipeline by integrating the
energy and momentum equations **simultaneously**, as a single ODE system in

    y = [T, P]          T in degrees C, P in Pa (gauge)

against chainage x.

Why they must be solved together
--------------------------------
The previous architecture solved heat transfer first and hydraulics second. That
ordering is not physically possible, because the two are coupled three ways:

    T -> mu(T) -> Re -> f -> dP/dx      cooler oil is thicker and rubs harder
    dP/dx -> viscous dissipation -> T   friction heats the oil it is retarding
    T -> rho(T) -> u = m_dot/(rho A)    and rho also sets the elevation head

Ignoring the middle path is the serious one. Every joule the pumps push in ends
up as heat in the fluid:

    dT_visc = dP_friction / (rho * Cp)  ~=  0.58 C per 10 bar for diesel

Over a 360 km line running 50-150 bar of friction loss, that is +3 to +9 C of
self-heating — against a soil-cooling signal of only 10-13 C. A model that omits
it attributes all of the temperature change to the ground, and any U-value
calibrated against a measured receipt temperature silently absorbs the missing
friction into a fictitious soil conductivity.


The governing system
--------------------

ENERGY (per unit length):

    m_dot * Cp * dT/dx  =  -U(x) * pi * D_o(x) * (T - T_env(x))     [environment coupling]
                           +  m_dot * f * u^2 / (2 * D_i)           [viscous dissipation]

    The dissipation term is the rate of mechanical energy destruction,
    Q * (-dP/dx)_friction, rewritten with Q = m_dot/rho and the Darcy-Weisbach
    gradient. Dividing through by m_dot*Cp:

    dT/dx = -U * pi * D_o * (T - T_env) / (m_dot * Cp)  +  f * u^2 / (2 * D_i * Cp)

    T_env is the environment temperature the line exchanges heat with:

        T_env = (T_air_current + T_soil) / 2

    T_air_current is the CURRENT observed air temperature at each station from
    the OpenWeather Current Weather API (data/openweather.py) — a measurement,
    never a forecast. T_soil is the existing ERA5-Land layer-4 climatology,
    unchanged. When no air temperature is available the soil temperature alone
    is used and the run is flagged `air_temperature_used = False`; an air
    temperature is never invented to fill the gap.

    Note what is NOT in the energy equation: elevation. Potential energy trades
    reversibly with pressure; it does not heat the fluid. Only the irreversible
    friction loss does.

MOMENTUM:

    dP/dx = -f(Re(T)) * rho(T) * u^2 / (2 * D_i)     [Darcy-Weisbach friction]
            -  rho(T) * g * dz/dx                    [elevation / static head]

CONTINUITY:

    m_dot = rho(T,P) * u * A  =  constant     =>   u(x) = m_dot / (rho(x) * A(x))

VOLUME (the objective's headline demonstration):

    The batch's MASS is fixed. Therefore

        V_gross(x) = mass / rho(T(x), P(x))  =  V_std / (CTL(T) * CPL(P))
        V_std(x)   = V_gross(x) * CTL(T) * CPL(P)  =  constant

    Gross volume breathes with temperature; standard volume does not move. If
    the VCF chain is correct, V_std(x) is invariant to machine precision, and
    that invariance is the strongest self-check the simulator has.

What is preserved from the validated model
------------------------------------------
The soil-coupling term and the U-value formulation (radial conduction through
steel + soil, Cengel eq. 3-38) are unchanged and still validated. This module
ADDS terms to a correct equation; it does not replace it. `analytic_reference()`
below reproduces the original closed-form solution exactly, and the test suite
pins the marching solver against it in the constant-soil, no-friction limit.

Sources
-------
  Cengel, "Heat Transfer: A Practical Approach", Ch. 3 (buried cylinder).
  Colebrook (1939); Swamee & Jain (1976); Moody (1944).
  API MPMS Ch. 11.1 (CTL) and Ch. 11.2.1 (CPL).
  Bird, Stewart & Lightfoot, "Transport Phenomena" — viscous dissipation.

Units: SI internally. x [m], T [C], P [Pa gauge], rho [kg/m3], mu [Pa.s].
"""

from __future__ import annotations

import math
import os
import sys
import warnings
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
from scipy.integrate import solve_ivp
from scipy.interpolate import interp1d

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import (
    FLOW_SPLITS,
    MIN_SUCTION_BAR,
    PUMP_STATIONS,
    K_STEEL_WMK,
    PRODUCTS,
)
from geo.route import Route
from model.friction import (
    VAPOR_PRESSURE_PA,
    compute_friction_factor,
    compute_reynolds,
)
from model.heat_transfer import compute_U_value
from model.properties import fluid_state
from model.ufp import resolve_rho_60

G = 9.80665  # standard gravity [m/s2]
ATMOSPHERIC_PA = 101325.0  # to convert gauge <-> absolute pressure


class HydraulicFeasibilityWarning(UserWarning):
    """The line cannot deliver this flow from this dispatch pressure.

    Raised when the pressure profile falls to the product's vapour pressure:
    the liquid column parts (slack flow / column separation) and the
    single-phase equations stop being valid. This is a real operating limit,
    not a numerical artifact — the honest answer is "you need an intermediate
    pump station or a lower flow rate", not a negative gauge pressure.
    """


@dataclass
class SimulationInputs:
    """Everything the user chooses for one run."""

    product: str
    T_dispatch_C: float
    V_dispatch_KL: float
    flow_rate_m3hr: float
    month: int
    P_dispatch_bar: float = 50.0
    density_kgm3: Optional[float] = None  # observed; defaults to product ref
    density_is_at_base: bool = False
    include_pressure_correction: bool = True  # CPL
    include_viscous_heating: bool = True  # for ablation studies
    include_elevation: bool = True  # for ablation studies


def simulate(
    inputs: SimulationInputs,
    route: Route,
    soil_profile: pd.DataFrame,
    U_scale: float = 1.0,
    max_step_km: float = 1.0,
    pump_stations: Optional[list] = None,
    flow_splits: Optional[list] = None,
) -> pd.DataFrame:
    """March the product state along the pipeline.

    Parameters
    ----------
    inputs : SimulationInputs
    route : Route
        Geometry from data/route/waypoints.csv.
    soil_profile : pd.DataFrame
        One month's soil profile. Required columns: km, T_soil_C, k_soil_WmK.

        Optionally also `T_env_C` and `T_air_C`, as produced by
        `data.openweather.build_environment_profile()`. When `T_env_C` is
        present it is the boundary temperature in the energy equation:
        T_env = (T_air_current + T_soil)/2, with T_air_current measured at each
        station by OpenWeather. When it is absent the boundary temperature is
        T_soil_C alone — the behaviour this module has always had — and
        `df.attrs["air_temperature_used"]` reports False.

        Note that k_soil is *not* averaged with anything: the conduction path
        out of the pipe is through soil, so the soil's own conductivity still
        sets U.
    U_scale : float
        Multiplier on the computed U-value. Johansen + burial correlations are
        +/- 30% at best; this is the knob a calibration step would fit against a
        measured receipt temperature. Default 1.0 (uncalibrated).
    max_step_km : float
        Cap on integrator step, so soil features are not stepped over.
    pump_stations : list of config.PumpStation, optional
        Intermediate booster stations. Each re-pressurises the product to its
        `discharge_bar` at its chainage. Defaults to `config.PUMP_STATIONS`;
        pass `[]` to model the single-pump case, in which a 360 km line needs
        its entire friction head at Kota.
    flow_splits : list of config.FlowSplit, optional
        Mid-route delivery points. Each removes `delivered_fraction` of the
        arriving mass flow at its chainage, so `m_dot` steps DOWN there and the
        line downstream carries only the remainder. Defaults to
        `config.FLOW_SPLITS`; pass `[]` to run the whole batch end to end.

    Returns
    -------
    pd.DataFrame indexed by chainage, one row per centreline point, carrying the
    complete physical state. This table IS the product of the simulator.
    """
    product = PRODUCTS[inputs.product.lower()]
    pump_stations = PUMP_STATIONS if pump_stations is None else pump_stations
    flow_splits = FLOW_SPLITS if flow_splits is None else flow_splits

    rho_obs = inputs.density_kgm3 if inputs.density_kgm3 is not None else product.density_ref_kgm3

    # ONE rho_60 for the batch, resolved once from the density measurement.
    rho_60 = resolve_rho_60(
        product_name=inputs.product,
        density_kgm3=rho_obs,
        density_measured_at_C=inputs.T_dispatch_C,
        density_is_at_base=inputs.density_is_at_base,
    )

    # ── Soil boundary condition, as a function of x ──────────────────
    soil = soil_profile.sort_values("km")
    T_soil_of = interp1d(soil["km"], soil["T_soil_C"], kind="linear", fill_value="extrapolate")
    k_soil_of = interp1d(soil["km"], soil["k_soil_WmK"], kind="linear", fill_value="extrapolate")

    # ── Environment boundary condition = (T_air_current + T_soil)/2 ──
    # Supplied per station by data.openweather.build_environment_profile().
    # Absent it, the boundary is the soil alone — never a guessed air value.
    has_env = "T_env_C" in soil.columns and soil["T_env_C"].notna().any()
    has_air = "T_air_C" in soil.columns and soil["T_air_C"].notna().any()

    if has_env:
        T_env_of = interp1d(soil["km"], soil["T_env_C"], kind="linear", fill_value="extrapolate")
    else:
        T_env_of = T_soil_of

    # Air temperature is NOT interpolated along the route. It exists only at the
    # terminals that carry a live reading (Bina and Bijwasan — see
    # config.AIR_STATIONS), and is written onto exactly those rows after
    # the integration. Interpolating it would draw a smooth air curve across
    # 360 km of buried pipe where no air measurement exists and none is used.
    air_by_km = (
        {round(float(k), 3): float(t) for k, t in zip(soil["km"], soil["T_air_C"]) if pd.notna(t)}
        if has_air
        else {}
    )
    basis_by_km = (
        {round(float(k), 3): str(b) for k, b in zip(soil["km"], soil["T_env_basis"])}
        if "T_env_basis" in soil.columns
        else {}
    )

    # ── Mass flow is fixed by the dispatch condition and conserved ───
    P_dispatch_Pa = inputs.P_dispatch_bar * 1e5
    s0 = fluid_state(
        rho_60,
        inputs.T_dispatch_C,
        P_dispatch_Pa,
        product,
        include_pressure=inputs.include_pressure_correction,
    )
    rho_dispatch = s0["rho_kgm3"]

    Q_dispatch_m3s = inputs.flow_rate_m3hr / 3600.0
    m_dot_dispatch = rho_dispatch * Q_dispatch_m3s  # [kg/s] leaving Kota

    # ── Flow splits: mass flow is constant only BETWEEN deliveries ───
    # At each delivery node a fraction of the arriving mass is taken off, so
    # m_dot is a step function of chainage rather than a single number.
    splits = sorted(
        (s for s in (flow_splits or []) if s.delivered_fraction > 0.0),
        key=lambda s: s.km,
    )

    def remaining_fraction(km: float) -> float:
        """Fraction of the dispatched mass still in the line at `km`.

        A delivery at `s.km` has taken effect AT that chainage — the tail
        segment starts there, and it starts carrying the reduced flow.
        """
        frac = 1.0
        for s in splits:
            if km >= s.km:
                frac *= 1.0 - s.delivered_fraction
        return frac

    def m_dot_at(km: float) -> float:
        return m_dot_dispatch * remaining_fraction(km)

    # Standard volume of the batch as dispatched. Downstream of a delivery the
    # line carries only `remaining_fraction` of it; the balance is checked in
    # the attrs below rather than asserted as an end-to-end invariance.
    CTL_dispatch = s0["CTL"]
    CPL_dispatch = s0["CPL"]
    V_std_KL = inputs.V_dispatch_KL * CTL_dispatch * CPL_dispatch
    mass_kg = V_std_KL * rho_60  # 1 KL == 1 m3

    def local_geometry(x_m: float):
        km = x_m / 1000.0
        return (
            route.d_inner(km),
            route.d_outer(km),
            route.area(km),
            route.roughness(km),
            route.burial_depth(km),
            route.elevation_gradient(km),
        )

    def derivatives(x_m: float, y):
        T_C, P_Pa = float(y[0]), float(y[1])
        km = x_m / 1000.0

        D_i, D_o, A, eps, burial, dzdx = local_geometry(x_m)

        st = fluid_state(
            rho_60, T_C, P_Pa, product, include_pressure=inputs.include_pressure_correction
        )
        rho, mu, cp = st["rho_kgm3"], st["mu_Pas"], st["cp_JkgK"]

        # Continuity: velocity follows from the mass flow still in the line.
        m_dot = m_dot_at(km)
        u = m_dot / (rho * A)

        Re = compute_reynolds(u, D_i, rho, mu)
        f = compute_friction_factor(Re, D_i, roughness_m=eps)

        # ── MOMENTUM ────────────────────────────────────────────────
        dP_friction = -f * rho * u * u / (2.0 * D_i)  # always < 0
        dP_elevation = -rho * G * dzdx if inputs.include_elevation else 0.0
        dPdx = dP_friction + dP_elevation

        # ── ENERGY ──────────────────────────────────────────────────
        # The driving temperature difference is against the ENVIRONMENT,
        # T_env = (T_air_current + T_soil)/2. k_soil still comes from the soil
        # alone: it describes the conduction path, not the sink temperature.
        T_env = float(T_env_of(km))
        k_soil = max(0.1, float(k_soil_of(km)))
        U = U_scale * compute_U_value(
            D_o, D_i, k_soil, burial_depth_m=burial, k_steel_WmK=K_STEEL_WMK
        )

        env_term = -U * math.pi * D_o * (T_C - T_env) / (m_dot * cp)

        # Viscous dissipation: Q*(-dP/dx)_friction / (m_dot*Cp), which reduces
        # to f*u^2/(2*D_i*Cp). Elevation is NOT here — it is reversible.
        visc_term = f * u * u / (2.0 * D_i * cp) if inputs.include_viscous_heating else 0.0

        return [env_term + visc_term, dPdx]

    x_eval = np.array([p.km for p in route.points]) * 1000.0

    # ── Integrate, breaking at each intermediate pump station ───────
    #
    # A booster station is a step change in pressure, so the pressure profile is
    # piecewise-continuous, not continuous. Integrating straight through would
    # smear the boost across the neighbouring kilometres and understate the
    # suction pressure the station actually sees. Instead the route is split at
    # each station: integrate up to it, record the arrival (suction) pressure,
    # reset P to the station's discharge pressure, and carry T across untouched
    # — a pump adds a little heat, but far less than the friction term already
    # models, and crediting it here would double-count the pump work that
    # reappears downstream as viscous dissipation.
    stations = sorted(
        (s for s in (pump_stations or []) if x_eval[0] / 1000.0 < s.km < x_eval[-1] / 1000.0),
        key=lambda s: s.km,
    )

    # Break at flow splits too. Nothing in the STATE jumps there — the product
    # that carries on has the temperature and pressure it arrived with — but
    # m_dot does, and integrating a stiff system across a step change in a
    # coefficient makes LSODA reduce its step drastically and still smear the
    # transition. Breaking is both faster and exact.
    split_kms = sorted(
        s.km for s in splits if x_eval[0] / 1000.0 < s.km < x_eval[-1] / 1000.0
    )
    boost_at = {s.km: s for s in stations}
    cut_kms = sorted({*boost_at, *split_kms})
    boundaries = [float(x_eval[0])] + [km * 1000.0 for km in cut_kms] + [float(x_eval[-1])]

    T_all = np.empty_like(x_eval)
    P_all = np.empty_like(x_eval)
    y0 = [float(inputs.T_dispatch_C), float(P_dispatch_Pa)]
    station_log = []

    for i in range(len(boundaries) - 1):
        lo, hi = boundaries[i], boundaries[i + 1]
        mask = (x_eval >= lo) & (x_eval <= hi) if i == 0 else (x_eval > lo) & (x_eval <= hi)
        sub_eval = x_eval[mask]

        span_eval = sub_eval if sub_eval.size else np.array([hi])
        sol = solve_ivp(
            derivatives,
            t_span=(lo, hi),
            y0=y0,
            method="LSODA",
            t_eval=span_eval,
            max_step=max_step_km * 1000.0,
            rtol=1e-8,
            atol=[1e-8, 1e-3],
        )
        if not sol.success:
            raise RuntimeError(f"Coupled solver failed on {lo / 1000:.0f}–{hi / 1000:.0f} km: {sol.message}")

        if sub_eval.size:
            T_all[mask] = sol.y[0]
            P_all[mask] = sol.y[1]

        # Carry the state to the next section. T and P are continuous across a
        # flow split — only the mass flow changes, and `m_dot_at` already knows
        # about it — so the only state edit here is a pump boost.
        y0 = [float(sol.y[0][-1]), float(sol.y[1][-1])]
        if i < len(cut_kms):
            cut_km = cut_kms[i]
            st_i = boost_at.get(cut_km)
            if st_i is not None:
                P_suction_bar = y0[1] / 1e5

                # A booster RAISES pressure; it never lowers it. If the product
                # already arrives above the station's discharge setting — which
                # happens after a long downhill run, where rho*g*dz has done the
                # pumping for free — the station simply passes it through. The
                # earlier version assigned the discharge pressure
                # unconditionally and so silently THREW AWAY head at exactly the
                # points where the terrain had provided it.
                P_discharge_bar = max(P_suction_bar, st_i.discharge_bar)

                station_log.append(
                    {
                        "name": st_i.name,
                        "km": st_i.km,
                        "P_suction_bar": round(P_suction_bar, 3),
                        "P_discharge_bar": round(P_discharge_bar, 3),
                        "boost_bar": round(P_discharge_bar - P_suction_bar, 3),
                        "idle": bool(P_discharge_bar <= P_suction_bar + 1e-9),
                        "suction_ok": bool(P_suction_bar >= MIN_SUCTION_BAR),
                    }
                )
                y0[1] = P_discharge_bar * 1e5

    # ── Reconstruct the full state at every evaluation point ────────
    records = []
    for pt, x_m, T_C, P_Pa in zip(route.points, x_eval, T_all, P_all):
        km = x_m / 1000.0
        D_i, D_o, A, eps, burial, dzdx = local_geometry(x_m)

        st = fluid_state(
            rho_60,
            float(T_C),
            float(P_Pa),
            product,
            include_pressure=inputs.include_pressure_correction,
        )
        rho, mu, cp = st["rho_kgm3"], st["mu_Pas"], st["cp_JkgK"]

        frac = remaining_fraction(km)
        m_dot = m_dot_dispatch * frac

        u = m_dot / (rho * A)
        Re = compute_reynolds(u, D_i, rho, mu)
        f = compute_friction_factor(Re, D_i, roughness_m=eps)

        k_soil = max(0.1, float(k_soil_of(km)))
        U = U_scale * compute_U_value(
            D_o, D_i, k_soil, burial_depth_m=burial, k_steel_WmK=K_STEEL_WMK
        )

        # Thermal relaxation length: how far the product travels while its
        # excess temperature over the soil decays by 1/e. Downstream of a
        # delivery this shortens, because less mass is carrying the heat.
        L_star_km = (m_dot * cp) / (U * math.pi * D_o) / 1000.0

        # THE VOLUMES. The mass still in the line follows the deliveries, and
        # gross volume follows density from there.
        mass_here_kg = mass_kg * frac
        V_gross_KL = mass_here_kg / rho
        V_std_KL_x = V_gross_KL * st["CTL"] * st["CPL"]

        # Slack flow / column separation: if the local pressure falls to the
        # product's vapour pressure, the liquid boils, the column parts, and the
        # single-phase model below is no longer valid. Report it — never hand
        # back a negative gauge pressure as though it meant something.
        P_vap_Pa = VAPOR_PRESSURE_PA.get(inputs.product.lower(), 5000.0)
        P_abs_Pa = float(P_Pa) + ATMOSPHERIC_PA
        slack = P_abs_Pa <= P_vap_Pa

        records.append(
            {
                "km": round(km, 3),
                "lat": pt.lat,
                "lon": pt.lon,
                "waypoint_name": pt.waypoint_name,
                "elevation_m": round(pt.elevation_m, 1),
                # Local geometry, carried explicitly. The route is no longer a
                # single diameter (18" mainline, 8" tail from Piyala), so any
                # downstream check that recomputes a gradient MUST use the bore
                # at that chainage rather than the one at km 0.
                "D_outer_m": round(D_o, 6),
                "D_inner_m": round(D_i, 6),
                # Precision note, as for rho and velocity below: the continuity
                # check recomputes m_dot = rho*u*A from these columns. The 8"
                # tail's area is only 0.0323 m2, so 8 dp here is a 1.5e-7
                # relative quantum — enough to fail a 1e-7 tolerance on
                # rounding rather than on physics. 12 dp costs nothing.
                "area_m2": round(A, 12),
                # Mass flow still in the line here. Constant within a segment,
                # stepping down at each delivery — so rho*u*A must be checked
                # against THIS, not against the dispatch value.
                "m_dot_kgs": round(m_dot, 10),
                "mass_fraction_remaining": round(frac, 8),
                # The three temperatures of the SURROUNDINGS, reported side by
                # side so the table can be audited: air is measured, soil is
                # ERA5, environment is their mean and is what the energy
                # equation above actually saw. T_C (below) is the PRODUCT
                # temperature — a solved quantity, not one of these.
                "T_air_C": air_by_km.get(round(km, 3), float("nan")),
                "T_soil_C": round(float(T_soil_of(km)), 3),
                "T_env_C": round(float(T_env_of(km)), 3),
                "T_env_basis": basis_by_km.get(round(km, 3), ""),
                "k_soil_WmK": round(k_soil, 4),
                "U_Wm2K": round(U, 4),
                "L_star_km": round(L_star_km, 1),
                "T_C": round(float(T_C), 4),
                "P_bar": round(float(P_Pa) / 1e5, 4),
                # Precision note: rho and velocity are kept to 8 dp because the
                # conservation checks recompute m_dot = rho*u*A from these columns.
                # At 4 dp, velocity alone injects a ~1e-4 relative error and the
                # mass-conservation test fails on rounding rather than on physics.
                "rho_kgm3": round(rho, 8),
                "mu_cP": round(mu * 1000.0, 6),
                "velocity_ms": round(u, 8),
                "Re": round(Re, 0),
                "friction_factor": round(f, 8),
                "CTL": round(st["CTL"], 8),
                "CPL": round(st["CPL"], 8),
                "V_gross_KL": round(V_gross_KL, 5),
                "V_std_KL": round(V_std_KL_x, 6),
                "P_vapour_bar": round(P_vap_Pa / 1e5, 4),
                "slack_flow": slack,
            }
        )

    df = pd.DataFrame(records)

    # ── Delivery log: what each split actually took off ─────────────
    # Standard volume, so the figures are comparable with the dispatch and
    # receipt numbers and add up exactly.
    split_log = []
    for s in splits:
        upstream = remaining_fraction(s.km - 1e-9)  # before this delivery
        taken = upstream * s.delivered_fraction
        split_log.append(
            {
                "name": s.name,
                "km": s.km,
                "delivered_pct": round(s.delivered_fraction * 100.0, 3),
                "V_std_delivered_KL": round(V_std_KL * taken, 6),
                "mass_delivered_kg": round(mass_kg * taken, 3),
                "m_dot_delivered_kgs": round(m_dot_dispatch * taken, 6),
            }
        )

    # `T_air_C` was written from `air_by_km` above, so every non-NaN value is
    # verbatim what the weather API returned for that station's own
    # coordinates. Nothing is interpolated, and a station with no reading stays
    # NaN and reads as "no data" in the table.

    # Cumulative pressure drop, for reporting.
    df["dP_cumulative_bar"] = round(inputs.P_dispatch_bar, 4) - df["P_bar"]

    # ── Hydraulic feasibility ───────────────────────────────────────
    # A negative gauge pressure is not a result; it is the model telling you the
    # line cannot deliver this flow from this dispatch pressure. Surface it.
    slack_rows = df[df["slack_flow"]]
    feasible = slack_rows.empty
    if not feasible:
        first_km = float(slack_rows["km"].iloc[0])
        warnings.warn(
            f"SLACK FLOW: pressure falls to the vapour pressure of "
            f"{inputs.product} at km {first_km:.0f} and stays below it for "
            f"{len(slack_rows)} of {len(df)} points (P_min = "
            f"{df['P_bar'].min():.1f} bar gauge). The liquid column would part. "
            f"This line cannot deliver {inputs.flow_rate_m3hr:.0f} m3/hr from "
            f"{inputs.P_dispatch_bar:.0f} bar without intermediate pumping. "
            f"Downstream of km {first_km:.0f} the single-phase results are not "
            f"physical.",
            HydraulicFeasibilityWarning,
            stacklevel=2,
        )

    df.attrs.update(
        {
            "hydraulically_feasible": feasible,
            "slack_flow_onset_km": (None if feasible else float(slack_rows["km"].iloc[0])),
            "P_min_bar": float(df["P_bar"].min()),
            "product": inputs.product,
            "rho_60_kgm3": rho_60,
            "m_dot_kgs": m_dot_dispatch,
            "mass_kg": mass_kg,
            "V_std_KL": V_std_KL,
            # ── Delivery balance ────────────────────────────────────
            # With a mid-route delivery the standard volume is no longer the
            # same number at both ends. What IS conserved is the total:
            #     V_std(dispatch) = sum(delivered) + V_std(receipt)
            # `V_std_balance_error_KL` is that identity's residual and should
            # be ~0 to machine precision.
            "flow_splits": split_log,
            "n_flow_splits": len(split_log),
            "mass_fraction_delivered": round(1.0 - remaining_fraction(route.length_km), 8),
            "V_std_delivered_KL": round(sum(s["V_std_delivered_KL"] for s in split_log), 6),
            "V_std_receipt_KL": round(V_std_KL * remaining_fraction(route.length_km), 6),
            "V_std_balance_error_KL": round(
                V_std_KL
                - sum(s["V_std_delivered_KL"] for s in split_log)
                - V_std_KL * remaining_fraction(route.length_km),
                9,
            ),
            "month": inputs.month,
            # Provenance of the energy equation's boundary temperature. False
            # means the run is soil-only: OpenWeather was unavailable and no air
            # temperature was substituted for it.
            "pump_stations": station_log,
            "n_pump_stations": len(station_log),
            "cavitating_stations": [s["name"] for s in station_log if not s["suction_ok"]],
            "air_temperature_used": bool(has_env),
            "n_stations_with_air": (
                int(soil["T_air_C"].notna().sum()) if "T_air_C" in soil.columns else 0
            ),
            "U_scale": U_scale,
            "include_viscous_heating": inputs.include_viscous_heating,
            "include_elevation": inputs.include_elevation,
            "include_pressure_correction": inputs.include_pressure_correction,
        }
    )
    return df


# ═══════════════════════════════════════════════════════════════════
# Analytic reference — how we extend a validated module without
# invalidating it
# ═══════════════════════════════════════════════════════════════════


def analytic_reference(
    T_dispatch_C: float,
    T_soil_C: float,
    L_star_km: float,
    km: np.ndarray,
) -> np.ndarray:
    """The original closed-form solution, preserved exactly.

        T(x) = T_soil + (T_in - T_soil) * exp(-x / L*)

    Valid only when T_soil and L* are constant along x and there is no viscous
    heating. The marching solver must reproduce this to <0.1 C in that limit —
    which is how we prove the added terms did not corrupt the validated physics.

    Source: integral of dT/dx = -(T - T_soil)/L* with constant coefficients.
    """
    return T_soil_C + (T_dispatch_C - T_soil_C) * np.exp(-np.asarray(km) / L_star_km)


if __name__ == "__main__":
    from model.soil_profile import _load_soil_csv

    route = Route.from_csv()
    soil = _load_soil_csv()
    soil = soil[soil["month"] == 1].rename(columns={"waypoint_km": "km"})

    inp = SimulationInputs(
        product="petrol",
        T_dispatch_C=40.0,
        V_dispatch_KL=1000.0,
        flow_rate_m3hr=400.0,
        month=1,
        P_dispatch_bar=60.0,
    )
    df = simulate(inp, route, soil)

    print("Petrol, 1000 KL @ 40 C, 400 m3/hr, January")
    print(
        f"  rho_60 = {df.attrs['rho_60_kgm3']:.2f} kg/m3   m_dot = {df.attrs['m_dot_kgs']:.2f} kg/s"
    )
    print()
    wp = df[df["waypoint_name"].notna()]
    cols = ["km", "waypoint_name", "T_C", "P_bar", "rho_kgm3", "mu_cP", "V_gross_KL", "V_std_KL"]
    print(wp[cols].to_string(index=False))
    print()
    print(
        f"  V_std drift over the whole line: "
        f"{(df.V_std_KL.max() - df.V_std_KL.min()) / df.V_std_KL.iloc[0] * 100:.2e} %"
    )
