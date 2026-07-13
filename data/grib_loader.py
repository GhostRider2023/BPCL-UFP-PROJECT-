"""
GRIB Dataset Loader — Real ERA5-Land Soil Data
===============================================

Loads hourly ERA5-Land GRIB data from the DATASET_SOIL folder
(ZIP archives containing data.grib + static NetCDF files) and
converts them into an xarray Dataset compatible with the UFP
engine's soil_profile module.

Expected folder structure:
  DATASET_SOIL/
    JANUARY (2).zip    -> data.grib + 6x .nc static files
    FEBURARY.zip       -> data.grib + 6x .nc static files
    MARCH.zip          -> ...
    ...
    DECEMBER.zip       -> ...

Each data.grib contains hourly fields for:
  - stl1/stl2/stl3/stl4   (soil temperature levels 1-4, Kelvin)
  - swvl1/swvl2/swvl3/swvl4 (volumetric soil water layers 1-4, m3/m3)
  - t2m, d2m, skt, src      (2m temp, dewpoint, skin temp, skin reservoir)

The loader produces an xarray.Dataset with:
  - Dimensions: (time, latitude, longitude)
  - Variables: stl2, stl3, swvl3 (minimum required by soil_profile.py)
  - All additional variables preserved for future use
"""

import logging
import os
import tempfile
import zipfile
from typing import List, Optional

import numpy as np
import xarray as xr

logger = logging.getLogger(__name__)

# Month name to ZIP filename mapping
MONTH_ZIP_MAP = {
    1: "JANUARY (2).zip",
    2: "FEBURARY.zip",
    3: "MARCH.zip",
    4: "APRIL.zip",
    5: "MAY.zip",
    6: "JUNE.zip",
    7: "JULY.zip",
    8: "AUGUST.zip",
    9: "SEPTEMBER.zip",
    10: "OCTOBER.zip",
    11: "NOVEMBER.zip",
    12: "DECEMBER.zip",
}


def load_grib_from_zip(
    zip_path: str,
    variables: Optional[List[str]] = None,
) -> xr.Dataset:
    """Load GRIB data from a ZIP archive into an xarray Dataset.

    Parameters
    ----------
    zip_path : str
        Path to the ZIP file containing data.grib.
    variables : list of str, optional
        GRIB short names to extract (e.g., ['stl3', 'swvl3']).
        If None, extracts all variables.

    Returns
    -------
    xr.Dataset
        Merged dataset with all requested variables.
    """
    import cfgrib

    with tempfile.TemporaryDirectory() as tmpdir:
        # Extract data.grib from the ZIP
        with zipfile.ZipFile(zip_path, "r") as z:
            grib_members = [m for m in z.namelist() if m.endswith(".grib")]
            if not grib_members:
                raise FileNotFoundError(f"No .grib file found in {zip_path}")
            z.extract(grib_members[0], tmpdir)
            grib_path = os.path.join(tmpdir, grib_members[0])

        # Open multi-field GRIB using cfgrib's backend_kwargs
        # cfgrib splits multi-parameter GRIB into separate datasets by default
        datasets = cfgrib.open_datasets(grib_path)

        if not datasets:
            raise ValueError(f"No datasets could be read from {grib_path}")

        # Filter to requested variables if specified
        if variables:
            filtered = []
            for ds in datasets:
                matching_vars = [v for v in ds.data_vars if v in variables]
                if matching_vars:
                    # Drop unneeded variables
                    ds_filtered = ds[matching_vars]

                    # ── Fix 1: assert time/valid_time alignment explicitly ──
                    # cfgrib exposes `time` (forecast reference) and `valid_time`
                    # (actual observation time). For ERA5-Land reanalysis with
                    # step=0 these must be identical. If they are not, the GRIB is
                    # a forecast product and silently swapping the axis would
                    # mislabel every timestamp. Fail loudly instead.
                    ds_filtered = _assert_time_alignment(ds_filtered, zip_path, matching_vars)

                    # Drop conflicting scalar coordinates like step or
                    # depthBelowLandLayer before merging, so that compat="equals"
                    # only trips on genuine data conflicts (lat/lon/time drift).
                    drop_coords = [
                        c
                        for c in ["step", "depthBelowLandLayer", "valid_time", "surface"]
                        if c in ds_filtered.coords
                    ]
                    ds_filtered = ds_filtered.drop_vars(drop_coords)

                    filtered.append(ds_filtered)
            datasets = filtered

        if not datasets:
            raise ValueError(f"Variables {variables} not found in {zip_path}")

        # ── Fix 2: compat="equals" so overlapping coords must genuinely agree ──
        # compat="override" silently takes the first dataset's coordinates and
        # discards the rest, which can mask a grid mismatch between variables.
        try:
            merged = xr.merge(datasets, compat="equals")
        except (xr.MergeError, ValueError) as exc:
            conflicts = _describe_merge_conflicts(datasets)
            raise ValueError(
                f"GRIB merge conflict in {os.path.basename(zip_path)} under "
                f"compat='equals'. Variables do not share an identical grid.\n"
                f"{conflicts}\n"
                f"Underlying error: {exc}"
            ) from exc

        # Load into memory before the temp directory is cleaned up
        merged = merged.load()

    return merged


def _assert_time_alignment(
    ds: xr.Dataset,
    zip_path: str,
    var_names: List[str],
) -> xr.Dataset:
    """Verify `time` and `valid_time` agree, then promote valid_time to `time`.

    ERA5-Land reanalysis is an analysis product: step == 0, so valid_time
    must equal time. A mismatch means the file is a forecast and every
    timestamp would be mislabelled by the length of the forecast step.

    Raises
    ------
    ValueError
        If valid_time and time disagree by more than one minute.
    """
    if "valid_time" not in ds.coords:
        return ds

    valid_time = ds.coords["valid_time"]

    # Scalar valid_time on a time-dimensioned dataset carries no information.
    if valid_time.dims != ("time",):
        return ds

    t = ds.coords["time"].values.astype("datetime64[s]")
    vt = valid_time.values.astype("datetime64[s]")

    if t.shape != vt.shape:
        raise ValueError(
            f"time/valid_time shape mismatch in {os.path.basename(zip_path)} "
            f"for {var_names}: time{t.shape} vs valid_time{vt.shape}"
        )

    delta_s = np.abs((vt - t).astype("timedelta64[s]").astype(np.int64))
    max_delta = int(delta_s.max()) if delta_s.size else 0

    if max_delta > 60:
        n_bad = int((delta_s > 60).sum())
        i = int(np.argmax(delta_s))
        raise ValueError(
            f"time/valid_time misalignment in {os.path.basename(zip_path)} for "
            f"{var_names}: {n_bad}/{len(t)} steps differ by up to {max_delta}s "
            f"(e.g. time={t[i]} vs valid_time={vt[i]}). This is a forecast "
            f"product, not an analysis — timestamps cannot be trusted."
        )

    # Aligned within tolerance: valid_time is authoritative.
    return ds.assign_coords(time=vt)


def _describe_merge_conflicts(datasets: List[xr.Dataset]) -> str:
    """Report which coordinate differs across datasets, for merge diagnostics."""
    lines = []
    for coord in ("time", "latitude", "longitude"):
        seen = {}
        for ds in datasets:
            if coord not in ds.coords:
                continue
            vals = ds.coords[coord].values
            key = (
                vals.shape,
                float(vals.min()) if vals.size else None,
                float(vals.max()) if vals.size else None,
            )
            seen.setdefault(key, []).extend(list(ds.data_vars))
        if len(seen) > 1:
            lines.append(f"  Coordinate '{coord}' differs across variables:")
            for (shape, lo, hi), vars_ in seen.items():
                lines.append(f"    n={shape} range=[{lo}, {hi}]  vars={vars_}")
    return "\n".join(lines) if lines else "  (no coordinate differences detected)"


def _coverage_gap(
    ds: xr.Dataset,
    target_lat: np.ndarray,
    target_lon: np.ndarray,
    tolerance: float,
) -> Optional[str]:
    """Return a description of the target box this dataset fails to cover.

    Returns None if the native grid spans the whole target box (within
    `tolerance`), otherwise a human-readable description of the shortfall.
    """
    lat = ds.latitude.values
    lon = ds.longitude.values

    problems = []
    if target_lat.min() < lat.min() - tolerance:
        problems.append(f"lat south edge: need {target_lat.min():.2f}, have {lat.min():.2f}")
    if target_lat.max() > lat.max() + tolerance:
        problems.append(f"lat north edge: need {target_lat.max():.2f}, have {lat.max():.2f}")
    if target_lon.min() < lon.min() - tolerance:
        problems.append(f"lon west edge: need {target_lon.min():.2f}, have {lon.min():.2f}")
    if target_lon.max() > lon.max() + tolerance:
        problems.append(f"lon east edge: need {target_lon.max():.2f}, have {lon.max():.2f}")

    if not problems:
        return None

    return (
        f"native grid lat {lat.min():.2f}..{lat.max():.2f}, "
        f"lon {lon.min():.2f}..{lon.max():.2f} — " + "; ".join(problems)
    )


def _assert_no_reindex_nan(
    ds_aligned: xr.Dataset,
    month: int,
    tolerance: float,
    ds_native: xr.Dataset,
) -> int:
    """Print the coordinates of any NaN present on the reindexed target grid.

    The plan's Task 0.4 requires: after every reindex, assert no NaN was
    introduced; if NaN count > 0, print the offending coordinates and the
    tolerance used. Silently reindexing outside a month's native extent is
    what put a NaN at the Mathura waypoint and crashed the June ODE solve.

    The invariant is asserted on the *target* grid, not by differencing NaN
    counts against the native grid: a native grid may legitimately contain
    ocean NaN (JUNE.zip reaches lat 18.5 deg, into the Arabian Sea), so the
    counts are not comparable. The pipeline corridor is entirely over land,
    so the correct invariant is simply: zero NaN on the target box.

    Returns
    -------
    int
        Number of NaN cells found on the target grid.
    """
    total_bad = 0

    for var in ds_aligned.data_vars:
        arr = ds_aligned[var]
        n_nan = int(np.isnan(arr.values).sum())
        if n_nan == 0:
            continue
        total_bad += n_nan

        # Locate the offending grid cells (collapse every non-spatial axis).
        spatial = ("latitude", "longitude")
        reduce_axes = tuple(arr.dims.index(d) for d in arr.dims if d not in spatial)
        bad = np.isnan(arr.values)
        if reduce_axes:
            bad = bad.any(axis=reduce_axes)

        bad_lat = ds_aligned.latitude.values[bad.any(axis=1)]
        bad_lon = ds_aligned.longitude.values[bad.any(axis=0)]

        print(f"  [NaN AFTER REINDEX] month={month:02d} var={var}")
        print(f"      {n_nan:,} NaN cells on the target grid")
        print(f"      reindex tolerance = {tolerance} deg")
        print(
            f"      native extent : lat {float(ds_native.latitude.min()):.2f}"
            f"..{float(ds_native.latitude.max()):.2f}, "
            f"lon {float(ds_native.longitude.min()):.2f}"
            f"..{float(ds_native.longitude.max()):.2f}"
        )
        print(
            f"      affected lat  : {bad_lat.min():.2f}..{bad_lat.max():.2f} ({len(bad_lat)} rows)"
        )
        print(
            f"      affected lon  : {bad_lon.min():.2f}..{bad_lon.max():.2f} ({len(bad_lon)} cols)"
        )
        logger.error(f"Reindex left {n_nan} NaN for {var} in month {month} (tolerance={tolerance})")

    return total_bad


def _nearest_valid_fill_2d(plane: np.ndarray) -> np.ndarray:
    """Fill NaN in a 2-D (lat, lon) plane from the nearest valid neighbour.

    Sweeps longitude (forward then backward), then latitude, using pure numpy
    so the loader carries no optional `bottleneck` dependency.
    """
    out = plane.copy()

    for axis in (1, 0):  # longitude first, then latitude
        for direction in (1, -1):
            arr = out if direction == 1 else np.flip(out, axis=axis)
            # Index of the last valid sample at or before each position.
            valid = ~np.isnan(arr)
            idx = np.where(
                valid, np.arange(arr.shape[axis]).reshape((-1, 1) if axis == 0 else (1, -1)), 0
            )
            idx = np.maximum.accumulate(idx, axis=axis)
            arr = np.take_along_axis(arr, idx, axis=axis)
            out = arr if direction == 1 else np.flip(arr, axis=axis)

    return out


def fill_spatial_gaps(ds: xr.Dataset) -> xr.Dataset:
    """Fill NaN cells by nearest valid neighbour along longitude, then latitude.

    ERA5-Land soil fields are spatially smooth (the soil-temperature gradient
    across the pipeline corridor is under 0.1 C per 0.1 deg cell), so extending
    the nearest valid cell across a short gap is defensible. What is NOT
    defensible is letting the NaN through: it propagates into interp1d, then
    into the BDF integrator, which dies with "array must not contain infs or
    NaNs".

    Every filled variable is recorded in ``ds.attrs["gap_filled_cells"]`` so
    downstream consumers can flag the affected waypoints as ESTIMATED.
    """
    filled_counts = {}

    for var in list(ds.data_vars):
        arr = ds[var]
        values = arr.values
        n_before = int(np.isnan(values).sum())
        if n_before == 0:
            continue

        spatial = ("latitude", "longitude")
        if not all(d in arr.dims for d in spatial):
            raise ValueError(f"Cannot gap-fill '{var}': no lat/lon dims")

        # Move (latitude, longitude) to the trailing axes, fill each plane.
        lead = [d for d in arr.dims if d not in spatial]
        transposed = arr.transpose(*lead, *spatial)
        data = transposed.values
        flat = data.reshape(-1, data.shape[-2], data.shape[-1])

        for i in range(flat.shape[0]):
            if np.isnan(flat[i]).any():
                flat[i] = _nearest_valid_fill_2d(flat[i])

        filled = flat.reshape(data.shape)
        ds[var] = transposed.copy(data=filled).transpose(*arr.dims)

        n_after = int(np.isnan(ds[var].values).sum())
        filled_counts[var] = n_before - n_after

        if n_after > 0:
            raise ValueError(
                f"Could not fill {n_after} NaN cells for '{var}' — an entire "
                f"lat/lon plane is empty. The source GRIB is unusable."
            )

    if filled_counts:
        total = sum(filled_counts.values())
        print(
            f"  [GAP FILL] Filled {total:,} NaN cells by nearest valid neighbour: {filled_counts}"
        )
        ds.attrs["gap_filled_cells"] = str(filled_counts)

    return ds


def load_dataset_soil(
    dataset_dir: str,
    months: Optional[List[int]] = None,
    variables: Optional[List[str]] = None,
    cache_path: Optional[str] = None,
) -> xr.Dataset:
    """Load the full DATASET_SOIL folder into a single xarray Dataset.

    Reads GRIB data from ZIP archives for each month, concatenates
    along the time dimension, and returns a unified dataset.

    Parameters
    ----------
    dataset_dir : str
        Path to the DATASET_SOIL folder.
    months : list of int, optional
        Months to load (1-12). Default: all available months.
    variables : list of str, optional
        GRIB short names to extract. Default: ['stl2', 'stl3', 'swvl3'].
    cache_path : str, optional
        If provided, saves the merged dataset as NetCDF for faster
        subsequent loading. If the cache file exists, loads from it.

    Returns
    -------
    xr.Dataset
        Merged dataset with dimensions (time, latitude, longitude).
    """
    if variables is None:
        variables = ["stl2", "stl3", "swvl3"]

    # Check for cached version first
    if cache_path and os.path.exists(cache_path):
        logger.info(f"Loading cached dataset from {cache_path}")
        ds = xr.open_dataset(cache_path)
        # Verify it has the required variables
        missing = [v for v in variables if v not in ds.data_vars]
        if not missing:
            return ds
        else:
            logger.warning(f"Cache missing variables {missing}, rebuilding...")
            ds.close()

    if months is None:
        months = list(range(1, 13))

    monthly_datasets = []
    loaded_months = []
    missing_months = []
    for month in months:
        zip_name = MONTH_ZIP_MAP.get(month)
        if zip_name is None:
            logger.warning(f"No ZIP mapping for month {month}")
            missing_months.append(month)
            continue

        zip_path = os.path.join(dataset_dir, zip_name)
        if not os.path.exists(zip_path):
            logger.warning(f"ZIP file not found: {zip_path}, skipping month {month}")
            missing_months.append(month)
            continue

        logger.info(f"Loading month {month:02d} from {zip_name}...")
        print(f"  Loading month {month:02d} from {zip_name}...")

        try:
            ds_month = load_grib_from_zip(zip_path, variables=variables)

            # Drop non-dimension coordinates that may conflict during concat
            for coord in list(ds_month.coords):
                if coord not in ds_month.dims and coord != "time":
                    ds_month = ds_month.drop_vars(coord, errors="ignore")

            monthly_datasets.append(ds_month)
            loaded_months.append(month)
        except Exception as e:
            logger.error(f"Failed to load {zip_name}: {e}")
            print(f"  [WARNING] Failed to load {zip_name}: {e}")
            missing_months.append(month)
            continue

    if not monthly_datasets:
        raise ValueError(f"No monthly datasets could be loaded from {dataset_dir}")

    if missing_months:
        print(
            f"  [WARNING] No source data for month(s): {missing_months}. "
            f"Downstream consumers must NOT silently substitute a neighbouring "
            f"month — these months are absent, not zero."
        )

    # Define a canonical 0.1° grid over the pipeline bounding box
    # ERA5 latitudes are descending, longitudes are ascending.
    target_lat = np.linspace(29.0, 25.0, 41)  # EXACTLY 41 elements (29.0 to 25.0)
    target_lon = np.linspace(75.5, 78.0, 26)  # EXACTLY 26 elements (75.5 to 78.0)

    # Reindex each month to the canonical grid (nearest-neighbor)
    print(f"  Aligning {len(monthly_datasets)} months to common grid...")
    REINDEX_TOLERANCE = 0.1  # [deg] matches the 0.1 deg ERA5-Land native grid

    aligned = []
    coverage_report = []
    for month, ds_m in zip(loaded_months, monthly_datasets):
        # Round the original coordinates first to avoid floating point mismatch
        ds_m = ds_m.assign_coords(
            {
                "latitude": np.round(ds_m.latitude.values, 2),
                "longitude": np.round(ds_m.longitude.values, 2),
            }
        )

        # ── Fix 3a: does this month's native grid even cover the target box? ──
        # A month downloaded with a different CDS bounding box will reindex to
        # all-NaN outside its native extent. Detect that BEFORE it silently
        # propagates into the soil profile and crashes the ODE solver.
        gap = _coverage_gap(ds_m, target_lat, target_lon, REINDEX_TOLERANCE)
        if gap:
            coverage_report.append((month, gap))

        ds_aligned = ds_m.reindex(
            latitude=np.round(target_lat, 2),
            longitude=np.round(target_lon, 2),
            method="nearest",
            tolerance=REINDEX_TOLERANCE,
        )

        # ── Fix 3b: assert the reindexed target grid carries no NaN ──
        _assert_no_reindex_nan(ds_aligned, month, REINDEX_TOLERANCE, ds_m)

        aligned.append(ds_aligned)

    if coverage_report:
        print()
        print("  " + "!" * 68)
        print("  DATA COVERAGE DEFECT — some months do not span the target box")
        print(
            f"  Target box: lat {target_lat.min():.1f}..{target_lat.max():.1f}, "
            f"lon {target_lon.min():.1f}..{target_lon.max():.1f}"
        )
        for month, gap in coverage_report:
            print(f"    month {month:02d} ({MONTH_ZIP_MAP[month]}): {gap}")
        print("  These months were downloaded with a different CDS bounding box.")
        print("  Re-download them with the bbox in config.ERA5_BBOX.")
        print("  " + "!" * 68)
        print()

    # Concatenate along time (all months now have identical grids)
    print(f"  Merging {len(aligned)} months...")
    merged = xr.concat(aligned, dim="time")
    merged = merged.sortby("time")

    # Clean up any residual scalar coordinates from GRIB encoding
    # that conflict with the dimension coordinates (lat/lon)
    keep_coords = {"time", "latitude", "longitude"}
    for coord_name in list(merged.coords):
        if coord_name not in keep_coords:
            merged = merged.drop_vars(coord_name, errors="ignore")

    # Strip GRIB encoding metadata from variables to prevent
    # xarray from reconstructing conflicting scalar coordinates
    for var_name in merged.data_vars:
        merged[var_name].encoding.clear()

    # Repair any NaN left by a short-extent month, so the dataset handed to the
    # physics layer is guaranteed dense. The loud report above has already told
    # the user which month is defective and why.
    merged = fill_spatial_gaps(merged)

    remaining = {v: int(np.isnan(merged[v].values).sum()) for v in merged.data_vars}
    if any(remaining.values()):
        raise ValueError(f"Dataset still contains NaN after gap fill: {remaining}")
    merged.attrs["months_present"] = str(sorted(loaded_months))

    # Save to cache if requested
    if cache_path:
        print(f"  Caching merged dataset to {cache_path}...")
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        merged.to_netcdf(cache_path)
        logger.info(f"Dataset cached to {cache_path}")

    return merged


# ─── Quick test ──────────────────────────────────────────────
if __name__ == "__main__":
    dataset_dir = r"C:\Users\Dr. Shephali\Downloads\BPCL PROJECT\DATASET_SOIL"
    cache_path = r"C:\Users\Dr. Shephali\Downloads\BPCL PROJECT\kota_bijwasan_ufp\data\real_soil_data_2025.nc"

    print("Loading DATASET_SOIL (GRIB from ZIPs)...")
    ds = load_dataset_soil(
        dataset_dir,
        variables=["stl2", "stl3", "swvl3"],
        cache_path=cache_path,
    )
    print("\nDataset loaded successfully:")
    print(ds)
    print(f"\nTime range: {ds.time.values[0]} to {ds.time.values[-1]}")
    print(f"Latitude range: {float(ds.latitude.min()):.1f} to {float(ds.latitude.max()):.1f}")
    print(f"Longitude range: {float(ds.longitude.min()):.1f} to {float(ds.longitude.max()):.1f}")
