"""
Soil Thermal Profile — Johansen (1975) Model
==============================================

Extracts ERA5 soil temperature and moisture at each pipeline
centerline point, then computes thermal conductivity using the
full Johansen (1975) correlation.

Johansen Model:
  λ = Ke × (λ_sat − λ_dry) + λ_dry

Where:
  Ke    = Kersten number (function of saturation degree)
  λ_sat = geometric-mean saturated conductivity
  λ_dry = empirical dry conductivity from bulk density

References:
  - Johansen, O. (1975). "Thermal conductivity of soils."
    Ph.D. thesis, Trondheim, Norway. CRREL Draft Translation 637.
  - Lu, S. et al. (2007). "An improved model for predicting soil
    thermal conductivity..." Int. J. Heat Mass Transfer 50:1547–1555.

Units:
  - Temperature:   °C (converted from ERA5 Kelvin)
  - Moisture:      m³/m³ (volumetric, from ERA5)
  - Conductivity:  W/(m·K)
"""

import math
import os
import sys
from typing import List, Optional

import numpy as np
import pandas as pd
import xarray as xr

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import (
    K_OTHER_MINERALS_WMK,
    K_QUARTZ_WMK,
    K_WATER_WMK,
    SOIL_MOISTURE_VAR,
    SOIL_PARTICLE_DENSITY_KGM3,
    SOIL_POROSITY,
    SOIL_QUARTZ_FRACTION,
    SOIL_TEMPERATURE_VAR,
    SOIL_TYPE,
    get_terrain_zone,
)
from geo.centerline import CenterlinePoint


def compute_k_dry(porosity: float, particle_density: float = SOIL_PARTICLE_DENSITY_KGM3) -> float:
    """Dry thermal conductivity [W/(m·K)] from Johansen (1975).

    Parameters
    ----------
    porosity : float
        Soil porosity (dimensionless, 0–1).
    particle_density : float
        Solid particle density [kg/m³]. Default 2650.

    Returns
    -------
    float
        Dry thermal conductivity [W/(m·K)].

    Notes
    -----
    Johansen formula for natural soils:
      λ_dry = (0.135 × ρ_d + 64.7) / (2700 − 0.947 × ρ_d)

    where ρ_d = (1 − n) × ρ_s is dry bulk density [kg/m³].
    Source: Johansen (1975), eq. 12.
    """
    rho_d = (1.0 - porosity) * particle_density  # dry bulk density [kg/m³]
    k_dry = (0.135 * rho_d + 64.7) / (2700.0 - 0.947 * rho_d)
    return k_dry


def compute_k_sat(
    porosity: float,
    quartz_fraction: float = SOIL_QUARTZ_FRACTION,
) -> float:
    """Saturated thermal conductivity [W/(m·K)] from Johansen (1975).

    Parameters
    ----------
    porosity : float
        Soil porosity (dimensionless, 0–1).
    quartz_fraction : float
        Volume fraction of quartz in solid particles (0–1).

    Returns
    -------
    float
        Saturated thermal conductivity [W/(m·K)].

    Notes
    -----
    Geometric mean model:
      λ_sat = λ_s^(1−n) × λ_w^n

    where λ_s = λ_quartz^q × λ_other^(1−q)
    Source: Johansen (1975), eq. 10.
    """
    # Solid particle conductivity (geometric mean)
    k_solid = (K_QUARTZ_WMK**quartz_fraction) * (K_OTHER_MINERALS_WMK ** (1.0 - quartz_fraction))

    # Saturated conductivity (geometric mean of solid and water)
    k_sat = (k_solid ** (1.0 - porosity)) * (K_WATER_WMK**porosity)
    return k_sat


def compute_kersten_number(
    saturation: float,
    soil_type: str = SOIL_TYPE,
) -> float:
    """Kersten number Ke from Johansen (1975).

    Parameters
    ----------
    saturation : float
        Degree of saturation S_r = θ/n (0–1).
    soil_type : str
        Either "coarse" or "fine".

    Returns
    -------
    float
        Kersten number Ke (0–1).

    Notes
    -----
    Coarse soils (S_r > 0.05):
      Ke = 0.7 × log10(S_r) + 1.0

    Fine soils (S_r > 0.1):
      Ke = log10(S_r) + 1.0

    Source: Johansen (1975), eq. 8 and 9.
    """
    if saturation <= 0.01:
        return 0.0

    if soil_type == "coarse":
        if saturation < 0.05:
            # Linear interpolation from 0 to Ke(0.05)
            ke_05 = 0.7 * np.log10(0.05) + 1.0
            return max(0.0, ke_05 * (saturation / 0.05))
        ke = 0.7 * np.log10(saturation) + 1.0
    else:  # fine
        if saturation < 0.1:
            ke_10 = np.log10(0.1) + 1.0
            return max(0.0, ke_10 * (saturation / 0.1))
        ke = np.log10(saturation) + 1.0

    return max(0.0, min(1.0, ke))


def compute_k_soil(
    moisture_m3m3: float,
    porosity: float = SOIL_POROSITY,
    quartz_fraction: float = SOIL_QUARTZ_FRACTION,
    soil_type: str = SOIL_TYPE,
) -> float:
    """Compute soil thermal conductivity [W/(m·K)] via Johansen (1975).

    Parameters
    ----------
    moisture_m3m3 : float
        Volumetric soil water content [m³/m³].
    porosity : float
        Soil porosity (dimensionless).
    quartz_fraction : float
        Quartz fraction of solid particles (0–1).
    soil_type : str
        "coarse" or "fine".

    Returns
    -------
    float
        Soil thermal conductivity [W/(m·K)].
    """
    k_dry = compute_k_dry(porosity)
    k_sat = compute_k_sat(porosity, quartz_fraction)

    # Degree of saturation
    saturation = moisture_m3m3 / porosity if porosity > 0 else 0.0
    saturation = min(saturation, 1.0)

    ke = compute_kersten_number(saturation, soil_type)

    k_soil = ke * (k_sat - k_dry) + k_dry
    return k_soil


MOISTURE_MIN_M3M3: float = 0.02
MOISTURE_MAX_M3M3: float = 0.50


def require_finite(value: float, what: str, where: str) -> float:
    """Return `value`, or raise if it is NaN/inf.

    ERA5-Land returns NaN over water and outside a download's bounding box.
    A NaN that reaches the physics layer propagates through interp1d into the
    BDF integrator and dies as "array must not contain infs or NaNs" hundreds
    of lines away from its cause. Catch it at the boundary, where we still know
    which waypoint and which variable produced it.

    The GRIB loader (data/grib_loader.py) already gap-fills its output, so a
    NaN arriving here means the dataset was not produced by that loader, or
    the waypoint lies outside the dataset's domain entirely.
    """
    if not math.isfinite(value):
        raise ValueError(
            f"Non-finite {what} ({value}) at {where}. ERA5 returns NaN over "
            f"water and outside the download bounding box. Rebuild the dataset "
            f"with data.grib_loader.load_dataset_soil(), which gap-fills and "
            f"reports coverage defects, or extend ERA5_BBOX to cover this point."
        )
    return value


def clamp_moisture(value: float, where: str) -> float:
    """Clamp volumetric soil moisture into its physical range.

    Guards the NaN hole in ``max(0.02, min(0.50, x))``: Python's ``min``
    returns 0.50 when x is NaN (every comparison against NaN is False), so a
    missing value silently became the *maximum* plausible moisture — which in
    turn produced a plausible-looking k_soil and hid the defect.
    """
    require_finite(value, "soil moisture", where)
    return max(MOISTURE_MIN_M3M3, min(MOISTURE_MAX_M3M3, value))


def extract_soil_profile(
    centerline_points: List[CenterlinePoint],
    era5_dataset: xr.Dataset,
) -> pd.DataFrame:
    """Extract monthly soil profile along the entire pipeline.

    For each centerline point × each month (1–12), extracts:
      - Soil temperature [°C] (from ERA5 stl3, nearest-neighbor)
      - Soil moisture [m³/m³] (from ERA5 swvl3, nearest-neighbor)
      - k_soil [W/(m·K)] (computed via Johansen with local terrain params)

    Parameters
    ----------
    centerline_points : list of CenterlinePoint
        Pipeline sample points from centerline generator.
    era5_dataset : xr.Dataset
        ERA5-Land dataset with variables stl2/stl3/swvl3.

    Returns
    -------
    pd.DataFrame
        Columns: km, lat, lon, month, T_soil_C, moisture_m3m3, k_soil_WmK
    """
    records = []

    for month in range(1, 13):
        # Select data for this month and compute monthly average
        month_data = era5_dataset.sel(time=era5_dataset.time.dt.month == month)

        # Skip if no data for this month (e.g., missing download)
        if len(month_data.time) == 0:
            continue

        month_data = month_data.mean(dim="time")

        for pt in centerline_points:
            # Nearest-neighbor extraction
            local = month_data.sel(
                latitude=pt.lat,
                longitude=pt.lon,
                method="nearest",
            )

            where = f"km {pt.km:.1f} ({pt.lat:.2f}N, {pt.lon:.2f}E), month {month}"

            # Soil temperature at the pipe's burial depth [K → °C].
            # SOIL_TEMPERATURE_VAR is stl4 (100–289 cm): the pipe is buried at
            # 1.2 m, which lies in layer 4. Reading stl3 (28–100 cm) — as the
            # code previously did — carries a 4.5 °C peak-to-peak seasonal bias
            # that flips sign with the season. See config.py.
            T_soil_K = require_finite(
                float(local[SOIL_TEMPERATURE_VAR].values),
                f"soil temperature {SOIL_TEMPERATURE_VAR}",
                where,
            )
            T_soil_C = T_soil_K - 273.15

            # Volumetric soil moisture [m³/m³], from the SAME layer, so that
            # k_soil describes the soil actually surrounding the pipe.
            moisture = clamp_moisture(float(local[SOIL_MOISTURE_VAR].values), where)

            # Get local terrain properties
            zone = get_terrain_zone(pt.km)

            # Compute k_soil with local terrain parameters
            k_soil = compute_k_soil(
                moisture_m3m3=moisture,
                porosity=zone.porosity,
                quartz_fraction=zone.quartz_frac,
                soil_type=zone.soil_type,
            )

            records.append(
                {
                    "km": pt.km,
                    "lat": pt.lat,
                    "lon": pt.lon,
                    "month": month,
                    "T_soil_C": round(T_soil_C, 2),
                    "moisture_m3m3": round(moisture, 4),
                    "k_soil_WmK": round(k_soil, 4),
                }
            )

    df = pd.DataFrame(records)

    # Gap repair belongs in the loader (data/grib_loader.fill_spatial_gaps),
    # where the defect can be attributed to a specific month and bounding box.
    # Doing it here with ffill/bfill hid the June coverage defect for the whole
    # life of the project. If anything is still NaN at this point, that is a
    # bug, not a data quirk — so assert loudly rather than paper over it.
    quantity_cols = ["T_soil_C", "moisture_m3m3", "k_soil_WmK"]
    bad = df[df[quantity_cols].isna().any(axis=1)]
    if not bad.empty:
        raise ValueError(
            f"{len(bad)} soil-profile rows are NaN after extraction. "
            f"Offending (km, month): "
            f"{list(bad[['km', 'month']].itertuples(index=False, name=None))[:10]}. "
            f"Rebuild the ERA5 dataset via data.grib_loader.load_dataset_soil()."
        )

    return df


_SOIL_CSV_CACHE: Optional[pd.DataFrame] = None


def _load_soil_csv(csv_path: Optional[str] = None) -> pd.DataFrame:
    """Load (and memoise) the precomputed waypoint soil-profile lookup table."""
    global _SOIL_CSV_CACHE
    if _SOIL_CSV_CACHE is not None and csv_path is None:
        return _SOIL_CSV_CACHE

    if csv_path is None:
        csv_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data",
            "kota_bijwasan_soil_profile.csv",
        )
    df = pd.read_csv(csv_path)
    if csv_path is None:
        _SOIL_CSV_CACHE = df
    _SOIL_CSV_CACHE = df
    return df


def available_months(csv_path: Optional[str] = None) -> List[int]:
    """Months for which ERA5 soil data actually exists."""
    return sorted(int(m) for m in _load_soil_csv(csv_path)["month"].unique())


def soil_temperature_at(
    km: float,
    month: int,
    csv_path: Optional[str] = None,
) -> float:
    """Soil temperature [C] at the ERA5 cell nearest the given chainage.

    Used to derive a physics-based temperature whenever a measured one is
    unavailable. NEVER substitute a constant (e.g. "assume 25 C at receipt") —
    a constant discards the seasonal and latitudinal signal that this whole
    model exists to capture, and it silently biases every downstream VCF.

    Raises
    ------
    ValueError
        If the requested month has no ERA5 source data. A missing month must
        be rejected, not quietly served from a neighbouring month.
    """
    df = _load_soil_csv(csv_path)

    months = available_months(csv_path)
    if month not in months:
        raise ValueError(
            f"No ERA5 soil data for month {month}. Available: {months}. "
            f"Batches in month {month} cannot be modelled — the month is "
            f"absent from DATASET_SOIL, and substituting a neighbouring month "
            f"would silently misprice the batch."
        )

    sub = df[df["month"] == month]
    idx = (sub["waypoint_km"] - km).abs().idxmin()
    return float(sub.loc[idx, "T_soil_C"])


def get_monthly_soil_profile(
    centerline_points: List[CenterlinePoint],
    era5_dataset: xr.Dataset,
    month: int,
) -> pd.DataFrame:
    """Get soil profile for a single month.

    Parameters
    ----------
    centerline_points : list of CenterlinePoint
    era5_dataset : xr.Dataset
    month : int
        Month (1–12).

    Returns
    -------
    pd.DataFrame
        Single-month slice with columns: km, lat, lon, T_soil_C,
        moisture_m3m3, k_soil_WmK.
    """
    full = extract_soil_profile(centerline_points, era5_dataset)
    return full[full["month"] == month].reset_index(drop=True)


# ─── Quick test ──────────────────────────────────────────────
if __name__ == "__main__":
    from data.era5_synthetic import generate_synthetic_era5
    from geo.centerline import generate_centerline

    print("Generating centerline...")
    pts = generate_centerline()
    print(f"  {len(pts)} points")

    print("Generating synthetic ERA5 data...")
    ds = generate_synthetic_era5()

    print("Extracting soil profile...")
    df = extract_soil_profile(pts, ds)
    print(f"\n  Total records: {len(df)}")
    print(f"  Months: {df['month'].unique()}")
    print(f"\n  k_soil range: {df['k_soil_WmK'].min():.3f} – {df['k_soil_WmK'].max():.3f} W/(m·K)")
    print(f"  T_soil range: {df['T_soil_C'].min():.1f} – {df['T_soil_C'].max():.1f} °C")
    print(
        f"  θ range:      {df['moisture_m3m3'].min():.3f} – {df['moisture_m3m3'].max():.3f} m³/m³"
    )

    # Show a few rows for January
    print("\n  Sample (January, first 5 points):")
    jan = df[df["month"] == 1].head(5)
    print(jan.to_string(index=False))
