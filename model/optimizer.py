"""
Flow Rate Optimizer
====================

Minimizes total pipeline operating cost:
  Cost(v) = UFP_financial(v) + Pumping_cost(v)

Where:
  - UFP_financial = f(velocity) -- higher velocity -> less time for
    heat exchange -> smaller dT -> smaller thermal UFP
  - Pumping_cost = f(velocity) -- higher velocity -> higher friction
    loss -> more pumping energy

The optimal velocity balances these opposing effects.

References:
  - Colebrook, C.F. (1939). "Turbulent flow in pipes."
  - Crane Co. TP-410: "Flow of Fluids Through Valves, Fittings, and Pipe."
  - Moody, L.F. (1944). "Friction factors for pipe flow."

Units:
  - Velocity:     m/s
  - Pressure:     Pa
  - Power:        W
  - Energy:       kWh
  - Cost:         INR (Indian Rupees)
"""

import os
import sys
from typing import List, Optional

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import (
    ELECTRICITY_RATE_INR_PER_KWH,
    PIPE_SEGMENTS,
    PRODUCTS,
    PUMP_EFFICIENCY,
    VELOCITY_MAX_MS,
    VELOCITY_MIN_MS,
)
from geo.centerline import CenterlinePoint
from model.friction import (
    compute_pumping_cost,
)


def optimize_flow_rate(
    product_name: str,
    density_kgm3: float,
    V_dispatch_KL: float,
    T_dispatch_C: float,
    month: int,
    centerline_points: List[CenterlinePoint],
    soil_profile_df: pd.DataFrame,
    price_per_litre: Optional[float] = None,
    pump_efficiency: float = PUMP_EFFICIENCY,
    electricity_rate: float = ELECTRICITY_RATE_INR_PER_KWH,
    v_min: float = VELOCITY_MIN_MS,
    v_max: float = VELOCITY_MAX_MS,
    n_eval_points: int = 30,
) -> dict:
    """Optimize flow velocity to minimize total cost.

    Parameters
    ----------
    product_name : str
        Product type.
    density_kgm3 : float
        Product density [kg/m³].
    V_dispatch_KL : float
        Dispatched volume [KL].
    T_dispatch_C : float
        Dispatch temperature [°C].
    month : int
        Month (1–12) for soil temperature selection.
    centerline_points : list of CenterlinePoint
        Pipeline sample points.
    soil_profile_df : pd.DataFrame
        Soil profile for the selected month.
    price_per_litre : float, optional
        Product price [₹/litre].
    pump_efficiency : float
        Pump efficiency (0–1).
    electricity_rate : float
        Electricity rate [₹/kWh].
    v_min, v_max : float
        Velocity bounds [m/s].
    n_eval_points : int
        Number of points for cost curve evaluation.

    Returns
    -------
    dict
        Optimization results with keys:
          - optimal_velocity_ms, optimal_flow_rate_m3hr
          - optimal_total_cost_inr, optimal_ufp_cost_inr,
            optimal_pumping_cost_inr
          - cost_curve: DataFrame with velocity, ufp_cost, pump_cost, total_cost
          - ufp_at_optimal: UFP details at optimal velocity
    """
    from model.heat_transfer import get_receipt_temperature, solve_temperature_profile
    from model.ufp import compute_ufp_from_model

    product = PRODUCTS[product_name.lower()]
    if price_per_litre is None:
        price_per_litre = product.price_per_litre

    A_ref = PIPE_SEGMENTS[0].cross_section_area_m2

    def total_cost(v):
        """Total cost at velocity v [m/s] → ₹."""
        flow_rate_m3hr = v * A_ref * 3600.0

        try:
            # Solve heat transfer at this velocity
            ht_result = solve_temperature_profile(
                centerline_points=centerline_points,
                soil_profile_df=soil_profile_df,
                product_name=product_name,
                T_dispatch_C=T_dispatch_C,
                flow_rate_m3hr=flow_rate_m3hr,
                density_kgm3=density_kgm3,
            )
            T_receipt = get_receipt_temperature(ht_result)

            # UFP cost (minimize physical shrinkage value)
            ufp_result = compute_ufp_from_model(
                product_name=product_name,
                V_dispatch_KL=V_dispatch_KL,
                T_dispatch_C=T_dispatch_C,
                density_kgm3=density_kgm3,
                T_receipt_modeled_C=T_receipt,
                price_per_litre=price_per_litre,
            )
            ufp_cost = abs(ufp_result["Physical_Shrinkage_rupees"])

            # Pumping cost
            pump_result = compute_pumping_cost(
                velocity_ms=v,
                product_name=product_name,
                density_kgm3=density_kgm3,
                T_avg_C=(T_dispatch_C + T_receipt) / 2.0,
                pump_efficiency=pump_efficiency,
                electricity_rate=electricity_rate,
            )
            pump_cost = pump_result["pumping_cost_inr"]

            return ufp_cost + pump_cost

        except Exception:
            return 1e12  # penalty for failed evaluation

    # Optimize.
    #
    # scipy's bounded Brent minimiser never evaluates the bracket endpoints, so
    # when the true minimum lies ON a bound it converges *near* it and returns a
    # strictly worse point (v = 0.506 m/s at INR 1,427,995, where v = 0.500 m/s
    # costs INR 1,417,456). Evaluate the endpoints explicitly and keep whichever
    # candidate is genuinely cheapest.
    result = minimize_scalar(
        total_cost,
        bounds=(v_min, v_max),
        method="bounded",
        options={"xatol": 0.01},
    )

    candidates = [float(result.x), float(v_min), float(v_max)]
    v_opt = min(candidates, key=total_cost)

    # Generate cost curve for plotting
    velocities = np.linspace(v_min, v_max, n_eval_points)
    curve_records = []

    for v in velocities:
        flow_rate = v * A_ref * 3600.0

        try:
            ht_result = solve_temperature_profile(
                centerline_points=centerline_points,
                soil_profile_df=soil_profile_df,
                product_name=product_name,
                T_dispatch_C=T_dispatch_C,
                flow_rate_m3hr=flow_rate,
                density_kgm3=density_kgm3,
            )
            T_receipt = get_receipt_temperature(ht_result)

            ufp_result = compute_ufp_from_model(
                product_name=product_name,
                V_dispatch_KL=V_dispatch_KL,
                T_dispatch_C=T_dispatch_C,
                density_kgm3=density_kgm3,
                T_receipt_modeled_C=T_receipt,
                price_per_litre=price_per_litre,
            )

            pump_result = compute_pumping_cost(
                velocity_ms=v,
                product_name=product_name,
                density_kgm3=density_kgm3,
                T_avg_C=(T_dispatch_C + T_receipt) / 2.0,
            )

            curve_records.append(
                {
                    "velocity_ms": round(v, 3),
                    "flow_rate_m3hr": round(flow_rate, 2),
                    "T_receipt_C": round(T_receipt, 2),
                    "ufp_cost_inr": round(abs(ufp_result["Physical_Shrinkage_rupees"]), 2),
                    "pumping_cost_inr": round(pump_result["pumping_cost_inr"], 2),
                    "total_cost_inr": round(
                        abs(ufp_result["Physical_Shrinkage_rupees"])
                        + pump_result["pumping_cost_inr"],
                        2,
                    ),
                }
            )
        except Exception:
            continue

    cost_curve_df = pd.DataFrame(curve_records)

    # Get detailed results at optimal velocity
    flow_rate_opt = v_opt * A_ref * 3600.0
    ht_opt = solve_temperature_profile(
        centerline_points=centerline_points,
        soil_profile_df=soil_profile_df,
        product_name=product_name,
        T_dispatch_C=T_dispatch_C,
        flow_rate_m3hr=flow_rate_opt,
        density_kgm3=density_kgm3,
    )
    T_receipt_opt = get_receipt_temperature(ht_opt)

    ufp_opt = compute_ufp_from_model(
        product_name=product_name,
        V_dispatch_KL=V_dispatch_KL,
        T_dispatch_C=T_dispatch_C,
        density_kgm3=density_kgm3,
        T_receipt_modeled_C=T_receipt_opt,
        price_per_litre=price_per_litre,
    )

    pump_opt = compute_pumping_cost(
        velocity_ms=v_opt,
        product_name=product_name,
        density_kgm3=density_kgm3,
        T_avg_C=(T_dispatch_C + T_receipt_opt) / 2.0,
    )

    return {
        "optimal_velocity_ms": round(v_opt, 3),
        "optimal_flow_rate_m3hr": round(flow_rate_opt, 2),
        "T_receipt_at_optimal_C": round(T_receipt_opt, 2),
        "optimal_ufp_cost_inr": round(abs(ufp_opt["Physical_Shrinkage_rupees"]), 2),
        "optimal_pumping_cost_inr": round(pump_opt["pumping_cost_inr"], 2),
        "optimal_total_cost_inr": round(
            abs(ufp_opt["Physical_Shrinkage_rupees"]) + pump_opt["pumping_cost_inr"], 2
        ),
        "optimal_ufp_KL": round(ufp_opt["Physical_Shrinkage_KL"], 4),
        "cost_curve": cost_curve_df,
        "ufp_details": ufp_opt,
        "pump_details": pump_opt,
    }


# ─── Quick test ──────────────────────────────────────────────
if __name__ == "__main__":
    print("Pumping cost at various velocities (diesel, ρ=840):")
    print("-" * 60)

    for v in [0.5, 1.0, 1.5, 2.0, 2.5]:
        r = compute_pumping_cost(
            velocity_ms=v,
            product_name="diesel",
            density_kgm3=840.0,
        )
        print(
            f"  v={v:.1f} m/s: Q={r['flow_rate_m3hr']:7.1f} m³/hr, "
            f"ΔP={r['delta_P_bar']:6.1f} bar, "
            f"P={r['P_pump_kW']:8.1f} kW, "
            f"Cost=₹{r['pumping_cost_inr']:,.0f}"
        )
