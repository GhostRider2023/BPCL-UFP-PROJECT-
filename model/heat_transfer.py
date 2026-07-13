"""
Heat Transfer ODE Solver
=========================

Solves the steady-state heat transfer along the buried Kota–Bijwasan
pipeline to predict product temperature at every point.

Governing equation:
  dT/dx = −(U(x) × π × D(x)) / (ṁ × Cp) × (T − T_soil(x))

Where:
  T       = product temperature [°C]
  x       = distance from Kota [m]
  U(x)    = overall heat transfer coefficient [W/(m²·K)]
  D(x)    = local pipe outer diameter [m]
  ṁ       = mass flow rate [kg/s]
  Cp      = specific heat capacity [J/(kg·K)]
  T_soil  = local soil temperature [°C]

Overall U from radial conduction:
  1/(U×D_o) = ln(D_o/D_i)/(2×k_steel) + ln(2z/D_o)/(2×k_soil(x))

  z = burial depth [m] (center of pipe to surface)

Source: Cengel, Y.A. "Heat Transfer: A Practical Approach", Ch. 3
        (steady radial conduction through cylindrical layers).

Units:
  - Distance:    km (input) → m (internal)
  - Temperature: °C
  - U-value:     W/(m²·K)
  - Mass flow:   kg/s
"""

import math
import os
import sys
from typing import List, Optional

import numpy as np
import pandas as pd
from scipy.integrate import solve_ivp
from scipy.interpolate import interp1d

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import (
    BURIAL_DEPTH_M,
    K_STEEL_WMK,
    PRODUCTS,
)
from geo.centerline import CenterlinePoint


def compute_U_value(
    D_outer_m: float,
    D_inner_m: float,
    k_soil_WmK: float,
    burial_depth_m: float = BURIAL_DEPTH_M,
    k_steel_WmK: float = K_STEEL_WMK,
) -> float:
    """Compute overall heat transfer coefficient U [W/(m²·K)].

    For a buried pipe, the thermal resistance from pipe center to
    the undisturbed soil consists of:
      1. Conduction through steel wall
      2. Conduction through surrounding soil to far field

    Parameters
    ----------
    D_outer_m : float
        Pipe outer diameter [m].
    D_inner_m : float
        Pipe inner diameter [m].
    k_soil_WmK : float
        Soil thermal conductivity [W/(m·K)].
    burial_depth_m : float
        Depth from surface to pipe center [m].
    k_steel_WmK : float
        Steel thermal conductivity [W/(m·K)].

    Returns
    -------
    float
        Overall U-value [W/(m²·K)] based on outer diameter.

    Notes
    -----
    1/(U×D_o) = ln(D_o/D_i) / (2×k_steel) + ln(2z/D_o) / (2×k_soil)

    U is referenced to outer surface area.
    Source: Cengel, "Heat Transfer", eq. 3-38 (buried cylinder).
    """
    r_outer = D_outer_m / 2.0
    r_inner = D_inner_m / 2.0

    # Resistance through steel wall [m·K/W]
    R_steel = math.log(r_outer / r_inner) / (2.0 * math.pi * k_steel_WmK)

    # Resistance through soil (buried cylinder approximation) [m·K/W]
    # Using ln(2z/r_o) / (2π k_soil) for z >> r_o
    z = burial_depth_m
    if z <= r_outer:
        z = r_outer * 1.5  # safety: ensure z > r_o

    R_soil = math.log(2.0 * z / r_outer) / (2.0 * math.pi * k_soil_WmK)

    # Total resistance per unit length [m·K/W]
    R_total = R_steel + R_soil

    # U based on outer diameter: U = 1 / (π × D_o × R_total)
    # But our ODE uses U × π × D_o, so we need 1/R_total
    U = 1.0 / (math.pi * D_outer_m * R_total)

    return U


def solve_temperature_profile(
    centerline_points: List[CenterlinePoint],
    soil_profile_df: pd.DataFrame,
    product_name: str,
    T_dispatch_C: float,
    flow_rate_m3hr: float,
    density_kgm3: float,
) -> pd.DataFrame:
    """Solve the heat transfer ODE along the pipeline.

    Parameters
    ----------
    centerline_points : list of CenterlinePoint
        Pipeline sample points (km, lat, lon, D_outer, D_inner).
    soil_profile_df : pd.DataFrame
        Soil profile for the relevant month.
        Columns: km, T_soil_C, k_soil_WmK.
    product_name : str
        Product type ("petrol", "diesel", "atf").
    T_dispatch_C : float
        Product temperature at Kota (dispatch) [°C].
    flow_rate_m3hr : float
        Volumetric flow rate [m³/hr].
    density_kgm3 : float
        Product density [kg/m³].

    Returns
    -------
    pd.DataFrame
        Columns: km, lat, lon, T_product_C, T_soil_C, U_Wm2K,
                 L_star_km, k_soil_WmK, D_outer_m
    """
    product = PRODUCTS[product_name.lower()]

    # Mass flow rate [kg/s]
    Q_m3s = flow_rate_m3hr / 3600.0  # [m³/s]
    m_dot = density_kgm3 * Q_m3s  # [kg/s]
    Cp = product.cp_jkgk  # [J/(kg·K)]

    # Build interpolation arrays from soil profile
    km_soil = soil_profile_df["km"].values
    T_soil_vals = soil_profile_df["T_soil_C"].values
    k_soil_vals = soil_profile_df["k_soil_WmK"].values

    # Interpolation functions (linear)
    T_soil_interp = interp1d(km_soil, T_soil_vals, kind="linear", fill_value="extrapolate")
    k_soil_interp = interp1d(km_soil, k_soil_vals, kind="linear", fill_value="extrapolate")

    # Build pipe geometry interpolation from centerline points
    km_pipe = np.array([p.km for p in centerline_points])
    D_outer_arr = np.array([p.D_outer_m for p in centerline_points])
    D_inner_arr = np.array([p.D_inner_m for p in centerline_points])

    D_outer_interp = interp1d(km_pipe, D_outer_arr, kind="nearest", fill_value="extrapolate")
    D_inner_interp = interp1d(km_pipe, D_inner_arr, kind="nearest", fill_value="extrapolate")

    # ODE: dT/dx = -(U × π × D_o) / (ṁ × Cp) × (T - T_soil)
    # x is in metres (convert km → m for integration)
    def dTdx(x_m, T):
        """Heat transfer ODE right-hand side."""
        x_km = x_m / 1000.0
        T_val = T[0] if isinstance(T, np.ndarray) and T.ndim > 0 else T

        # Local properties
        T_soil_local = float(T_soil_interp(x_km))
        k_soil_local = float(k_soil_interp(x_km))
        D_o = float(D_outer_interp(x_km))
        D_i = float(D_inner_interp(x_km))

        # Clamp k_soil to avoid numerical issues
        k_soil_local = max(0.1, k_soil_local)

        # Compute local U-value
        U = compute_U_value(D_o, D_i, k_soil_local)

        # dT/dx [°C/m]
        dT = -(U * math.pi * D_o) / (m_dot * Cp) * (T_val - T_soil_local)

        # Must return an array or list for scipy solve_ivp
        return [dT]

    # Integration domain [m]
    x_start = km_pipe[0] * 1000.0
    x_end = km_pipe[-1] * 1000.0

    # Evaluation points [m] — must be strictly increasing for solve_ivp
    x_eval = km_pipe * 1000.0
    # Remove duplicates and ensure strict monotonicity
    x_eval_unique = np.unique(x_eval)
    # Ensure endpoints are included
    if x_eval_unique[0] != x_start:
        x_eval_unique = np.concatenate([[x_start], x_eval_unique])
    if x_eval_unique[-1] != x_end:
        x_eval_unique = np.concatenate([x_eval_unique, [x_end]])
    x_eval_unique = np.sort(x_eval_unique)

    # Solve ODE
    sol = solve_ivp(
        dTdx,
        t_span=(x_start, x_end),
        y0=[T_dispatch_C],
        method="BDF",
        t_eval=x_eval_unique,
    )

    if not sol.success:
        raise RuntimeError(f"ODE solver failed: {sol.message}")

    # Extract results — interpolate from ODE solution grid back to centerline
    T_solution = sol.y[0]  # temperature at x_eval_unique points
    x_solution_km = sol.t / 1000.0  # convert back to km

    # Interpolate to get T at every centerline point
    T_product_interp = interp1d(x_solution_km, T_solution, kind="linear", fill_value="extrapolate")

    # Compute U-values and relaxation lengths at each point
    records = []
    for pt in centerline_points:
        T_prod_local = float(T_product_interp(pt.km))
        T_soil_local = float(T_soil_interp(pt.km))
        k_soil_local = float(k_soil_interp(pt.km))
        k_soil_local = max(0.1, k_soil_local)

        U = compute_U_value(pt.D_outer_m, pt.D_inner_m, k_soil_local)

        # Thermal relaxation length L* = ṁ × Cp / (U × π × D_o) [m → km]
        L_star_m = (m_dot * Cp) / (U * math.pi * pt.D_outer_m)
        L_star_km = L_star_m / 1000.0

        records.append(
            {
                "km": pt.km,
                "lat": pt.lat,
                "lon": pt.lon,
                "T_product_C": round(T_prod_local, 3),
                "T_soil_C": round(T_soil_local, 2),
                "U_Wm2K": round(U, 4),
                "L_star_km": round(L_star_km, 1),
                "k_soil_WmK": round(k_soil_local, 4),
                "D_outer_m": pt.D_outer_m,
                "waypoint_name": pt.waypoint_name,
            }
        )

    return pd.DataFrame(records)


def get_receipt_temperature(
    result_df: pd.DataFrame,
) -> float:
    """Extract product temperature at receipt (Bijwasan).

    Parameters
    ----------
    result_df : pd.DataFrame
        Output from solve_temperature_profile().

    Returns
    -------
    float
        Product temperature at Bijwasan [C].
    """
    return float(result_df["T_product_C"].iloc[-1])


def solve_from_csv_profile(
    product_name: str,
    T_dispatch_C: float,
    flow_rate_m3hr: float,
    density_kgm3: float,
    month: int,
    csv_path: Optional[str] = None,
) -> pd.DataFrame:
    """Solve heat transfer using the fixed soil profile CSV.

    Convenience wrapper that loads kota_bijwasan_soil_profile.csv
    and generates the centerline internally, so callers don't need
    xarray or ERA5 data.

    Parameters
    ----------
    product_name : str
        Product type ("petrol", "diesel", "atf").
    T_dispatch_C : float
        Dispatch temperature at Kota [C].
    flow_rate_m3hr : float
        Flow rate [m3/hr].
    density_kgm3 : float
        Product density [kg/m3].
    month : int
        Month (1-12, excluding 8/August).
    csv_path : str, optional
        Path to soil profile CSV. Default: auto-detect.

    Returns
    -------
    pd.DataFrame
        Same as solve_temperature_profile output.
    """
    import os as _os

    from geo.centerline import generate_centerline

    if csv_path is None:
        csv_path = _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
            "kota_bijwasan_ufp",
            "data",
            "kota_bijwasan_soil_profile.csv",
        )
        if not _os.path.exists(csv_path):
            # Try relative to this file
            csv_path = _os.path.join(
                _os.path.dirname(_os.path.abspath(__file__)),
                "..",
                "data",
                "kota_bijwasan_soil_profile.csv",
            )

    soil_full = pd.read_csv(csv_path)

    # Filter to requested month; fallback to nearest if missing
    soil_month = soil_full[soil_full["month"] == month]
    if soil_month.empty:
        available = soil_full["month"].unique()
        closest = min(available, key=lambda m: abs(m - month))
        soil_month = soil_full[soil_full["month"] == closest]

    soil_df = soil_month.rename(columns={"waypoint_km": "km"}).reset_index(drop=True)

    centerline = generate_centerline()

    return solve_temperature_profile(
        centerline_points=centerline,
        soil_profile_df=soil_df,
        product_name=product_name,
        T_dispatch_C=T_dispatch_C,
        flow_rate_m3hr=flow_rate_m3hr,
        density_kgm3=density_kgm3,
    )


# ─── Quick test ──────────────────────────────────────────────
if __name__ == "__main__":
    from data.era5_synthetic import generate_synthetic_era5
    from geo.centerline import generate_centerline
    from model.soil_profile import get_monthly_soil_profile

    print("Setting up...")
    pts = generate_centerline()
    ds = generate_synthetic_era5()

    for month in [1, 6]:  # January (winter) and June (summer)
        month_name = [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ][month - 1]
        print(f"\n{'=' * 50}")
        print(f"Month: {month_name}")

        soil_df = get_monthly_soil_profile(pts, ds, month)

        result = solve_temperature_profile(
            centerline_points=pts,
            soil_profile_df=soil_df,
            product_name="diesel",
            T_dispatch_C=35.0,
            flow_rate_m3hr=150.0,
            density_kgm3=840.0,
        )

        T_receipt = get_receipt_temperature(result)
        print(f"  Diesel: T_dispatch = 35.0°C → T_receipt = {T_receipt:.2f}°C")
        print(f"  ΔT = {35.0 - T_receipt:.2f}°C over {pts[-1].km} km")

        # Show waypoints
        wps = result[result["waypoint_name"].notna()]
        print("\n  Waypoint temperatures:")
        for _, row in wps.iterrows():
            print(
                f"    km {row['km']:6.1f} {row['waypoint_name']:25s}: "
                f"T_prod={row['T_product_C']:5.2f}°C, "
                f"T_soil={row['T_soil_C']:5.1f}°C, "
                f"L*={row['L_star_km']:6.1f} km"
            )
