"""
Generate Fixed Soil Profile CSV
================================

One-time script that extracts soil temperature and thermal conductivity
from the cached ERA5-Land NetCDF at the 8 pipeline waypoints for all
12 months, producing a fixed lookup table:

  kota_bijwasan_soil_profile.csv

This CSV is IMMUTABLE SYSTEM DATA — the user never modifies it.

Columns:
  waypoint_km, waypoint_name, lat, lon, month, T_soil_C,
  moisture_m3m3, k_soil_WmK
"""

import os
import sys

import pandas as pd
import xarray as xr

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import (
    SOIL_MOISTURE_VAR,
    SOIL_TEMPERATURE_VAR,
    get_terrain_zone,
)
from geo.route import Route
from model.soil_profile import clamp_moisture, compute_k_soil, require_finite


def generate_soil_csv(
    netcdf_path: str,
    output_csv: str,
) -> pd.DataFrame:
    """Extract soil profile at waypoints from ERA5 NetCDF.

    Parameters
    ----------
    netcdf_path : str
        Path to the cached ERA5-Land NetCDF file.
    output_csv : str
        Path to write the output CSV.

    Returns
    -------
    pd.DataFrame
        The generated soil profile (96 rows: 8 waypoints x 12 months).
    """
    ds = xr.open_dataset(netcdf_path)
    route = Route.from_csv()
    records = []
    months_present = sorted(set(int(m) for m in ds.time.dt.month.values))
    months_absent = [m for m in range(1, 13) if m not in months_present]

    for month in months_present:
        month_data = ds.sel(time=ds.time.dt.month == month)
        month_avg = month_data.mean(dim="time")

        for wp in route.waypoints:
            local = month_avg.sel(
                latitude=wp.lat,
                longitude=wp.lon,
                method="nearest",
            )
            where = f"{wp.name} (km {wp.chainage_km:.0f}), month {month}"

            # Soil state at the pipe's burial depth. SOIL_TEMPERATURE_VAR is
            # stl4 (100–289 cm) because the pipe sits at 1.2 m — inside layer 4.
            # Guarded: a NaN must never be silently clamped into a
            # plausible-looking value (see model.soil_profile.clamp_moisture).
            T_soil_K = require_finite(
                float(local[SOIL_TEMPERATURE_VAR].values),
                f"soil temperature {SOIL_TEMPERATURE_VAR}",
                where,
            )
            T_soil_C = T_soil_K - 273.15

            moisture = clamp_moisture(float(local[SOIL_MOISTURE_VAR].values), where)

            zone = get_terrain_zone(wp.chainage_km)
            k_soil = compute_k_soil(
                moisture_m3m3=moisture,
                porosity=zone.porosity,
                quartz_fraction=zone.quartz_frac,
                soil_type=zone.soil_type,
            )

            records.append(
                {
                    "waypoint_km": wp.chainage_km,
                    "waypoint_name": wp.name,
                    "lat": wp.lat,
                    "lon": wp.lon,
                    "month": month,
                    "T_soil_C": round(T_soil_C, 2),
                    "moisture_m3m3": round(moisture, 4),
                    "k_soil_WmK": round(k_soil, 4),
                    "soil_layer": SOIL_TEMPERATURE_VAR,
                    "burial_depth_m": wp.burial_depth_m,
                }
            )

    gap_filled = ds.attrs.get("gap_filled_cells")
    ds.close()

    df = pd.DataFrame(records)

    # Never write a NaN into the immutable system lookup table.
    quantity_cols = ["T_soil_C", "moisture_m3m3", "k_soil_WmK"]
    bad = df[df[quantity_cols].isna().any(axis=1)]
    if not bad.empty:
        raise ValueError(
            f"Refusing to write {len(bad)} NaN rows to {output_csv}: "
            f"{list(bad[['waypoint_name', 'month']].itertuples(index=False, name=None))}"
        )

    df.to_csv(output_csv, index=False)
    print(f"Wrote {len(df)} rows to {output_csv}")
    print(f"  Waypoints: {df['waypoint_name'].nunique()}")
    print(f"  Months:    {df['month'].nunique()} -> {months_present}")
    if months_absent:
        print(
            f"  [WARNING] NO SOURCE DATA for month(s) {months_absent}. "
            f"Batches in those months cannot be modelled and must be rejected, "
            f"not silently scored against a neighbouring month."
        )
    if gap_filled:
        print(f"  [NOTE] Underlying ERA5 grid was gap-filled: {gap_filled}")
    print(f"  T_soil range: {df['T_soil_C'].min():.1f} to {df['T_soil_C'].max():.1f} C")
    print(f"  k_soil range: {df['k_soil_WmK'].min():.3f} to {df['k_soil_WmK'].max():.3f} W/(m.K)")
    print(f"  NaN cells:    {int(df[quantity_cols].isna().sum().sum())}")
    return df


if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    nc_path = os.path.join(script_dir, "real_soil_data_2025.nc")
    csv_path = os.path.join(script_dir, "kota_bijwasan_soil_profile.csv")

    if not os.path.exists(nc_path):
        print(f"ERROR: {nc_path} not found")
        sys.exit(1)

    generate_soil_csv(nc_path, csv_path)
