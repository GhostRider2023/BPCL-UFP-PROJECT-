"""
Synthetic ERA5 Data Generator
==============================

Creates realistic synthetic ERA5-Land soil temperature and moisture
data for the Kota–Bijwasan pipeline bounding box. Used when:
  1. No CDS API key is available
  2. During development and testing
  3. As a fallback if real ERA5 download fails

The synthetic data reproduces:
  - Seasonal temperature cycle with latitude-dependent amplitude
  - Terrain-dependent moisture patterns (dry Chambal/Rajasthan,
    wet Yamuna/Haryana)
  - Diurnal temperature variation (subdued at burial depth)
  - Realistic value ranges matching ERA5-Land observations

Units:
  - Soil temperature: Kelvin [K] (ERA5 convention)
  - Volumetric soil water: m³/m³ (dimensionless fraction)
"""

import os
import sys
from datetime import datetime
from typing import Optional

import numpy as np
import xarray as xr

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import ERA5_BBOX, ERA5_DAYS, ERA5_MONTHS, ERA5_TIMES, ERA5_YEARS


def _seasonal_temperature_K(
    lat: np.ndarray,
    month: int,
    hour: int,
) -> np.ndarray:
    """Compute synthetic soil temperature at 28–100 cm depth [K].

    Parameters
    ----------
    lat : np.ndarray
        Latitude grid [degrees North].
    month : int
        Month (1–12).
    hour : int
        Hour of day (0, 6, 12, 18 UTC → IST = UTC + 5:30).

    Returns
    -------
    np.ndarray
        Soil temperature [K], same shape as lat.

    Notes
    -----
    Model:
      T_soil = T_mean(lat) + A(lat) × sin(2π(month - 4)/12)
             + A_diurnal × sin(2π(hour - 6)/24)

    T_mean decreases with latitude (~1°C per degree north in this range).
    Amplitude is larger in southern (Rajasthan desert) portions.
    Diurnal variation at burial depth is very small (~0.5°C).
    """
    # Mean annual soil temperature [°C]
    # Southern (lat ~25) ≈ 28°C, Northern (lat ~29) ≈ 22°C
    T_mean_C = 28.0 - 1.5 * (lat - 25.0)

    # Seasonal amplitude [°C]
    # Larger in southern desert, smaller in northern irrigated
    A_seasonal = 10.0 - 0.5 * (lat - 25.0)

    # Seasonal sinusoid: peak in June (month=6), trough in December
    phase_month = 2.0 * np.pi * (month - 4.0) / 12.0
    T_seasonal = A_seasonal * np.sin(phase_month)

    # Diurnal variation at burial depth (attenuated)
    A_diurnal = 0.5  # °C
    phase_hour = 2.0 * np.pi * (hour - 6.0) / 24.0
    T_diurnal = A_diurnal * np.sin(phase_hour)

    T_C = T_mean_C + T_seasonal + T_diurnal
    return T_C + 273.15  # Convert to Kelvin


def _soil_moisture(
    lat: np.ndarray,
    lon: np.ndarray,
    month: int,
) -> np.ndarray:
    """Compute synthetic volumetric soil water content [m³/m³].

    Parameters
    ----------
    lat : np.ndarray
        Latitude grid [degrees North].
    lon : np.ndarray
        Longitude grid [degrees East].
    month : int
        Month (1–12).

    Returns
    -------
    np.ndarray
        Volumetric soil water content [m³/m³].

    Notes
    -----
    Terrain zones along the route:
      - Southern desert (lat < 26.5°N): dry, θ ≈ 0.08–0.15
      - Central alluvial (26.5–27.5°N): moderate, θ ≈ 0.15–0.25
      - Northern irrigated (lat > 27.5°N): moist, θ ≈ 0.20–0.35

    Monsoon (July–September) increases moisture everywhere.
    """
    # Base moisture by latitude band
    theta_base = np.where(
        lat < 26.5,
        0.10,  # Chambal/Rajasthan — dry
        np.where(
            lat < 27.5,
            0.18,  # Yamuna alluvial — moderate
            0.25,  # Haryana irrigated — moist
        ),
    )

    # Monsoon boost: peaks in August (month 8)
    monsoon_phase = np.exp(-0.5 * ((month - 8.0) / 1.5) ** 2)
    monsoon_boost = np.where(
        lat < 26.5,
        0.08 * monsoon_phase,  # Less monsoon in Rajasthan
        np.where(
            lat < 27.5,
            0.12 * monsoon_phase,
            0.10 * monsoon_phase,
        ),
    )

    # Winter drying
    if month in [11, 12, 1, 2]:
        winter_dry = 0.03
    else:
        winter_dry = 0.0

    theta = theta_base + monsoon_boost - winter_dry

    # Clamp to physical range
    return np.clip(theta, 0.02, 0.50)


def generate_synthetic_era5(
    output_path: Optional[str] = None,
) -> xr.Dataset:
    """Generate a synthetic ERA5-Land dataset.

    Creates an xarray Dataset with the same structure and variable
    names as a real ERA5-Land NetCDF file, but with physics-based
    synthetic values.

    Parameters
    ----------
    output_path : str, optional
        If provided, save the dataset to this NetCDF path.

    Returns
    -------
    xr.Dataset
        Synthetic dataset with variables:
          - stl2: soil temperature level 2 (7–28 cm) [K]
          - stl3: soil temperature level 3 (28–100 cm) [K]
          - swvl3: volumetric soil water layer 3 (28–100 cm) [m³/m³]
    """
    # ERA5-Land grid resolution: 0.1° × 0.1°
    north, west, south, east = ERA5_BBOX
    lat_vals = np.arange(north, south - 0.05, -0.1)
    lon_vals = np.arange(west, east + 0.05, 0.1)

    # Build time axis
    times = []
    for year in ERA5_YEARS:
        for month in ERA5_MONTHS:
            for day in ERA5_DAYS:
                for time_str in ERA5_TIMES:
                    hour = int(time_str.split(":")[0])
                    try:
                        dt = datetime(year, month, day, hour)
                        times.append(dt)
                    except ValueError:
                        # Skip invalid dates (e.g., Feb 29 in non-leap years)
                        pass

    times = sorted(set(times))
    n_times = len(times)
    n_lat = len(lat_vals)
    n_lon = len(lon_vals)

    # Pre-allocate arrays
    stl2 = np.zeros((n_times, n_lat, n_lon), dtype=np.float32)
    stl3 = np.zeros((n_times, n_lat, n_lon), dtype=np.float32)
    swvl3 = np.zeros((n_times, n_lat, n_lon), dtype=np.float32)

    # 2D lat/lon grids
    lat_grid, lon_grid = np.meshgrid(lat_vals, lon_vals, indexing="ij")

    for t_idx, dt in enumerate(times):
        month = dt.month
        hour = dt.hour

        # Soil temperature at 28–100 cm
        T3 = _seasonal_temperature_K(lat_grid, month, hour)

        # Soil temperature at 7–28 cm (slightly more responsive)
        T2 = T3 + 0.5 * np.sin(2.0 * np.pi * (hour - 6.0) / 24.0)

        # Add small random noise (±0.3 K) for realism
        rng = np.random.RandomState(t_idx)
        T3 += rng.normal(0, 0.3, T3.shape).astype(np.float32)
        T2 += rng.normal(0, 0.4, T2.shape).astype(np.float32)

        stl2[t_idx] = T2
        stl3[t_idx] = T3

        # Soil moisture
        swvl3[t_idx] = _soil_moisture(lat_grid, lon_grid, month)
        # Add small noise
        swvl3[t_idx] += rng.normal(0, 0.01, swvl3[t_idx].shape).astype(np.float32)
        swvl3[t_idx] = np.clip(swvl3[t_idx], 0.02, 0.50)

    # Build xarray Dataset
    ds = xr.Dataset(
        {
            "stl2": (
                ["time", "latitude", "longitude"],
                stl2,
                {
                    "long_name": "Soil temperature level 2",
                    "units": "K",
                    "depth": "7-28 cm",
                },
            ),
            "stl3": (
                ["time", "latitude", "longitude"],
                stl3,
                {
                    "long_name": "Soil temperature level 3",
                    "units": "K",
                    "depth": "28-100 cm",
                },
            ),
            "swvl3": (
                ["time", "latitude", "longitude"],
                swvl3,
                {
                    "long_name": "Volumetric soil water layer 3",
                    "units": "m3 m-3",
                    "depth": "28-100 cm",
                },
            ),
        },
        coords={
            "time": times,
            "latitude": lat_vals,
            "longitude": lon_vals,
        },
        attrs={
            "source": "Synthetic ERA5-Land data for development",
            "pipeline": "Kota-Bijwasan (BPCL/MMBL)",
            "generated": datetime.now().isoformat(),
        },
    )

    if output_path:
        ds.to_netcdf(output_path)
        print(f"Synthetic ERA5 data saved to: {output_path}")

    return ds


# ─── Quick test when run directly ─────────────────────────────
if __name__ == "__main__":
    ds = generate_synthetic_era5()
    print("\nDataset summary:")
    print(f"  Time steps: {len(ds.time)}")
    print(f"  Lat range:  {float(ds.latitude.min()):.1f} – {float(ds.latitude.max()):.1f}")
    print(f"  Lon range:  {float(ds.longitude.min()):.1f} – {float(ds.longitude.max()):.1f}")
    print(f"\n  stl3 range: {float(ds.stl3.min()):.1f} – {float(ds.stl3.max()):.1f} K")
    print(f"  swvl3 range: {float(ds.swvl3.min()):.3f} – {float(ds.swvl3.max()):.3f} m³/m³")
