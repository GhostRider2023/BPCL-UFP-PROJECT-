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

import math
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

# A waypoint further than this from the ERA5 cell that answered for it is
# outside the download's coverage, and its soil state is an extrapolation
# rather than a reading. ERA5-Land is a 0.1 deg grid (~11 km), so anything
# beyond ~25 km means we fell off the edge of the box, not merely onto a
# neighbouring cell.
ERA5_NEAREST_WARN_KM = 25.0

SOIL_SOURCE_OBSERVED = "ERA5"
SOIL_SOURCE_OUT_OF_BOX = "EXTRAPOLATED_OUTSIDE_ERA5_BBOX"


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance [km]. Only used to police grid snapping."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


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

            # HOW FAR AWAY IS THE CELL WE ACTUALLY GOT?
            #
            # xarray's method="nearest" never fails: ask for a point outside the
            # download's bounding box and it silently hands back the closest
            # edge cell, however far that is. Bina sits ~95 km outside the
            # cached box, and without this guard its soil temperature would
            # arrive looking exactly like a measurement.
            #
            # So the distance is computed, recorded in the CSV, and anything
            # beyond ERA5_NEAREST_WARN_KM is labelled as out-of-coverage rather
            # than passed off as observed.
            got_lat = float(local["latitude"].values)
            got_lon = float(local["longitude"].values)
            offset_km = _haversine_km(wp.lat, wp.lon, got_lat, got_lon)
            in_coverage = offset_km <= ERA5_NEAREST_WARN_KM
            soil_source = (
                SOIL_SOURCE_OBSERVED
                if in_coverage
                else f"{SOIL_SOURCE_OUT_OF_BOX} ({offset_km:.0f} km)"
            )

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
                    "soil_source": soil_source,
                    "era5_cell_offset_km": round(offset_km, 1),
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

    outside = df[df["soil_source"] != SOIL_SOURCE_OBSERVED]
    if not outside.empty:
        names = sorted(outside["waypoint_name"].unique())
        print(
            f"  [WARNING] {len(outside)} rows are OUTSIDE the ERA5 download "
            f"box and were extrapolated from the nearest edge cell: {names}. "
            f"Their soil temperature is NOT a reading. Re-download ERA5 with "
            f"ERA5_BBOX widened to cover them (config.ERA5_BBOX is already "
            f"set for this; the cached NetCDF is not)."
        )
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
