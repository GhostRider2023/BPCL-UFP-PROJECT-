"""
Friction & Pressure Drop Model
================================

Comprehensive hydraulic model for the Kota-Bijwasan pipeline:
  1. Reynolds number calculation
  2. Colebrook-White / Swamee-Jain friction factor
  3. Darcy-Weisbach pressure drop
  4. Pressure profile P(x) along the pipeline
  5. Slack flow / cavitation detection
  6. Pumping power and energy cost
  7. CPL (Correction for Pressure on Liquid) analysis

Sources:
  - Colebrook, C.F. (1939). "Turbulent flow in pipes."
  - Moody, L.F. (1944). "Friction factors for pipe flow."
  - Crane Co. TP-410: "Flow of Fluids Through Valves, Fittings, and Pipe."
  - Swamee, P.K. & Jain, A.K. (1976). J. Hydraulics Div., ASCE.
  - API MPMS Chapter 11.2.4 (Correction for Pressure on Liquid)
  - IS:2796 (Petrol RVP), IS:1460 (Diesel flash), IS:1571 (ATF flash)

Units:
  - Velocity:     m/s
  - Pressure:     Pa (internal) / bar (interface)
  - Power:        W / kW
  - Energy:       kWh
  - Cost:         INR (Indian Rupees)
"""

import math
import os
import sys
from typing import Dict, Optional

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import (
    ELECTRICITY_RATE_INR_PER_KWH,
    PIPE_ROUGHNESS_M,
    PIPE_SEGMENTS,
    PRODUCTS,
    PUMP_EFFICIENCY,
)

# ═══════════════════════════════════════════════════════════════
# VAPOR PRESSURE DATA
# Source: IS:2796 (petrol RVP), IS:1460 (diesel), IS:1571 (ATF)
# Reid Vapor Pressure (RVP) at 37.8°C for petrol;
# for diesel/ATF, flash point is used as proxy (vapor pressure
# is negligible at pipeline operating temperatures).
# ═══════════════════════════════════════════════════════════════

# Approximate vapor pressure at operating temperatures [Pa]
# Petrol: RVP max 60 kPa per IS:2796, actual ~35-60 kPa at 37.8°C
# Diesel: flash point >35°C (IS:1460), vapor pressure < 2 kPa at 50°C
# ATF: flash point >38°C (IS:1571), vapor pressure < 3 kPa at 50°C
VAPOR_PRESSURE_PA: Dict[str, float] = {
    "petrol": 55000.0,  # ~0.55 bar — conservative RVP at 37.8°C
    "diesel": 2000.0,  # ~0.02 bar — negligible
    "atf": 3000.0,  # ~0.03 bar — negligible
}


# ═══════════════════════════════════════════════════════════════
# CPL ANALYSIS — Correction for Pressure on Liquid
# ═══════════════════════════════════════════════════════════════
#
# API MPMS Chapter 11.2.4 defines CPL = 1 / (1 - F_p × P)
# where F_p is the compressibility factor [per bar] and P is
# gauge pressure [bar].
#
# For refined petroleum products:
#   F_p ≈ 5e-5 to 8e-5 per bar (typical)
#
# At pipeline operating pressures of 20-70 bar:
#   CPL = 1 / (1 - 7e-5 × 50) = 1 / (1 - 0.0035) = 1.00351
#
# This means CPL contributes ~0.035% volume correction,
# compared to CTL which contributes ~1-3% over the same
# temperature range.
#
# CONCLUSION: CPL is negligible for this application.
# At 50 bar, CPL ≈ 1.0035 (0.35% max), while CTL varies
# by 1-3%. Including CPL would add complexity without
# meaningful accuracy improvement.
#
# CPL is EXCLUDED from V1 with this documented justification.
# ═══════════════════════════════════════════════════════════════

CPL_EXCLUDED_REASON = (
    "CPL (API MPMS 11.2.4) excluded: at typical pipeline pressures "
    "(20-70 bar), CPL contributes ~0.03-0.05% volume correction vs "
    "CTL's ~1-3%. Negligible for product pipeline UFP analysis."
)


def compute_reynolds(
    velocity_ms: float,
    D_inner_m: float,
    density_kgm3: float,
    viscosity_pas: float,
) -> float:
    """Compute Reynolds number.

    Re = (rho * v * D) / mu

    Parameters
    ----------
    velocity_ms : float
        Flow velocity [m/s].
    D_inner_m : float
        Pipe inner diameter [m].
    density_kgm3 : float
        Fluid density [kg/m3].
    viscosity_pas : float
        Dynamic viscosity [Pa.s].

    Returns
    -------
    float
        Reynolds number (dimensionless).
    """
    return density_kgm3 * velocity_ms * D_inner_m / viscosity_pas


def compute_friction_factor(
    Re: float,
    D_inner_m: float,
    roughness_m: float = PIPE_ROUGHNESS_M,
    max_iterations: int = 100,
    tolerance: float = 1e-8,
) -> float:
    """Compute Darcy-Weisbach friction factor.

    Uses Swamee-Jain (explicit) as initial guess, then iterates
    Colebrook-White to convergence.

    Laminar (Re < 2300): f = 64/Re
    Turbulent: Colebrook-White implicit equation.

    Parameters
    ----------
    Re : float
        Reynolds number.
    D_inner_m : float
        Pipe inner diameter [m].
    roughness_m : float
        Absolute pipe roughness [m].
        Default: 0.045 mm for commercial steel (Moody, Crane TP-410).

    Returns
    -------
    float
        Darcy-Weisbach friction factor (dimensionless).

    Source: Colebrook (1939), Swamee & Jain (1976).
    """
    if Re < 2300:
        return 64.0 / max(Re, 1.0)

    rel_rough = roughness_m / D_inner_m

    # Swamee-Jain explicit approximation (initial guess)
    A = rel_rough / 3.7
    B = 5.74 / (Re**0.9)
    f = 0.25 / (math.log10(A + B) ** 2)

    # Iterate Colebrook-White
    for _ in range(max_iterations):
        sqrt_f = math.sqrt(f)
        rhs = -2.0 * math.log10(rel_rough / 3.7 + 2.51 / (Re * sqrt_f))
        f_new = 1.0 / (rhs**2)
        if abs(f_new - f) < tolerance:
            return f_new
        f = f_new

    return f


def compute_pressure_drop(
    velocity_ms: float,
    density_kgm3: float,
    viscosity_pas: float,
    length_m: float,
    D_inner_m: float,
    roughness_m: float = PIPE_ROUGHNESS_M,
) -> float:
    """Compute frictional pressure drop via Darcy-Weisbach [Pa].

    dP = f * (L/D) * (rho * v^2 / 2)

    Parameters
    ----------
    velocity_ms : float
        Flow velocity [m/s].
    density_kgm3 : float
        Fluid density [kg/m3].
    viscosity_pas : float
        Dynamic viscosity [Pa.s].
    length_m : float
        Pipe segment length [m].
    D_inner_m : float
        Pipe inner diameter [m].
    roughness_m : float
        Pipe roughness [m].

    Returns
    -------
    float
        Pressure drop [Pa].

    Source: Darcy-Weisbach equation.
    """
    Re = compute_reynolds(velocity_ms, D_inner_m, density_kgm3, viscosity_pas)
    f = compute_friction_factor(Re, D_inner_m, roughness_m)
    delta_P = f * (length_m / D_inner_m) * (density_kgm3 * velocity_ms**2 / 2.0)
    return delta_P


def compute_pressure_profile(
    velocity_ms: float,
    product_name: str,
    density_kgm3: float,
    P_dispatch_bar: float,
    T_profile_df: Optional[pd.DataFrame] = None,
    T_avg_C: float = 30.0,
) -> pd.DataFrame:
    """Compute pressure profile P(x) along the pipeline.

    Calculates cumulative pressure drop at each waypoint and checks
    for slack flow (P < vapor pressure).

    Parameters
    ----------
    velocity_ms : float
        Reference flow velocity [m/s] (in first pipe segment).
    product_name : str
        Product type ("petrol", "diesel", "atf").
    density_kgm3 : float
        Fluid density [kg/m3].
    P_dispatch_bar : float
        Dispatch pressure at Kota pump station [bar].
    T_profile_df : pd.DataFrame, optional
        Temperature profile from heat transfer solver.
        If provided, uses local T for viscosity.
    T_avg_C : float
        Average temperature [C] for viscosity if T_profile_df not given.

    Returns
    -------
    pd.DataFrame
        Columns: km, P_bar, dP_cumulative_bar, Re, f, velocity_ms,
                 P_vapor_bar, slack_flow_risk
    """
    product = PRODUCTS[product_name.lower()]
    P_vapor = VAPOR_PRESSURE_PA.get(product_name.lower(), 5000.0)
    P_vapor_bar = P_vapor / 1e5

    A_ref = PIPE_SEGMENTS[0].cross_section_area_m2
    records = []

    # Starting point
    records.append(
        {
            "km": PIPE_SEGMENTS[0].km_start,
            "P_bar": P_dispatch_bar,
            "dP_cumulative_bar": 0.0,
            "Re": 0.0,
            "f": 0.0,
            "velocity_ms": velocity_ms,
            "P_vapor_bar": P_vapor_bar,
            "slack_flow_risk": False,
        }
    )

    cumulative_dP = 0.0
    step_km = 10.0  # compute every 10 km

    for seg in PIPE_SEGMENTS:
        seg_length_km = seg.km_end - seg.km_start
        n_steps = max(1, int(seg_length_km / step_km))
        step_length_m = (seg_length_km / n_steps) * 1000.0

        # Velocity in this segment (continuity)
        A_seg = seg.cross_section_area_m2
        v_seg = velocity_ms * A_ref / A_seg

        for i in range(n_steps):
            km_point = seg.km_start + (i + 1) * (seg_length_km / n_steps)

            # Get local temperature for viscosity
            if T_profile_df is not None and len(T_profile_df) > 0:
                idx = (T_profile_df["km"] - km_point).abs().idxmin()
                T_local = T_profile_df.loc[idx, "T_product_C"]
            else:
                T_local = T_avg_C

            viscosity = product.viscosity_at_T(T_local)
            Re = compute_reynolds(v_seg, seg.inner_diameter_m, density_kgm3, viscosity)
            f = compute_friction_factor(Re, seg.inner_diameter_m)

            dP_step = f * (step_length_m / seg.inner_diameter_m) * (density_kgm3 * v_seg**2 / 2.0)
            cumulative_dP += dP_step
            P_local_bar = P_dispatch_bar - cumulative_dP / 1e5

            records.append(
                {
                    "km": round(km_point, 1),
                    "P_bar": round(P_local_bar, 2),
                    "dP_cumulative_bar": round(cumulative_dP / 1e5, 2),
                    "Re": round(Re, 0),
                    "f": round(f, 6),
                    "velocity_ms": round(v_seg, 3),
                    "P_vapor_bar": P_vapor_bar,
                    "slack_flow_risk": P_local_bar < P_vapor_bar,
                }
            )

    return pd.DataFrame(records)


def compute_pumping_cost(
    velocity_ms: float,
    product_name: str,
    density_kgm3: float,
    T_avg_C: float = 30.0,
    pump_efficiency: float = PUMP_EFFICIENCY,
    electricity_rate: float = ELECTRICITY_RATE_INR_PER_KWH,
) -> dict:
    """Compute pumping energy cost for the full pipeline.

    Parameters
    ----------
    velocity_ms : float
        Flow velocity [m/s].
    product_name : str
        Product type.
    density_kgm3 : float
        Product density [kg/m3].
    T_avg_C : float
        Average product temperature [C] (for viscosity).
    pump_efficiency : float
        Pump overall efficiency (0-1). Default: 0.75.
        Source: Typical centrifugal pump for petroleum service.
    electricity_rate : float
        Electricity cost [INR/kWh].
        Source: Approximate industrial tariff, Rajasthan/Haryana 2024.

    Returns
    -------
    dict
        Keys: delta_P_Pa, delta_P_bar, P_pump_kW, transit_time_hr,
              energy_kWh, pumping_cost_inr, flow_rate_m3hr, velocity_ms
    """
    product = PRODUCTS[product_name.lower()]
    viscosity = product.viscosity_at_T(T_avg_C)

    total_delta_P = 0.0
    total_length_m = 0.0

    A_ref = PIPE_SEGMENTS[0].cross_section_area_m2

    for seg in PIPE_SEGMENTS:
        seg_length_m = (seg.km_end - seg.km_start) * 1000.0
        A_seg = seg.cross_section_area_m2
        v_seg = velocity_ms * A_ref / A_seg  # continuity

        dP = compute_pressure_drop(
            velocity_ms=v_seg,
            density_kgm3=density_kgm3,
            viscosity_pas=viscosity,
            length_m=seg_length_m,
            D_inner_m=seg.inner_diameter_m,
        )
        total_delta_P += dP
        total_length_m += seg_length_m

    Q_m3s = velocity_ms * A_ref
    P_pump_W = total_delta_P * Q_m3s / pump_efficiency

    transit_time_s = total_length_m / velocity_ms
    transit_time_hr = transit_time_s / 3600.0

    energy_kWh = P_pump_W * transit_time_s / 3.6e6
    pumping_cost = energy_kWh * electricity_rate

    return {
        "velocity_ms": velocity_ms,
        "flow_rate_m3hr": round(Q_m3s * 3600.0, 2),
        "delta_P_Pa": round(total_delta_P, 0),
        "delta_P_bar": round(total_delta_P / 1e5, 2),
        "P_pump_kW": round(P_pump_W / 1000.0, 2),
        "transit_time_hr": round(transit_time_hr, 2),
        "energy_kWh": round(energy_kWh, 2),
        "pumping_cost_inr": round(pumping_cost, 2),
    }


# ─── Quick test ──────────────────────────────────────────────
if __name__ == "__main__":
    print("Friction Module — Quick Test")
    print("=" * 60)

    print("\nPumping cost at various velocities (diesel, rho=840):")
    print("-" * 60)
    for v in [0.5, 1.0, 1.5, 2.0, 2.5]:
        r = compute_pumping_cost(v, "diesel", 840.0)
        print(
            f"  v={v:.1f} m/s: Q={r['flow_rate_m3hr']:7.1f} m3/hr, "
            f"dP={r['delta_P_bar']:6.1f} bar, "
            f"P={r['P_pump_kW']:8.1f} kW, "
            f"Cost=INR {r['pumping_cost_inr']:,.0f}"
        )

    print("\nPressure profile (petrol, v=1.5 m/s, P_dispatch=50 bar):")
    print("-" * 60)
    pf = compute_pressure_profile(1.5, "petrol", 750.0, 50.0)
    for _, row in pf.iterrows():
        flag = " ** SLACK FLOW **" if row["slack_flow_risk"] else ""
        print(f"  km {row['km']:6.1f}: P={row['P_bar']:6.2f} bar{flag}")

    print(f"\nCPL Status: {CPL_EXCLUDED_REASON}")
