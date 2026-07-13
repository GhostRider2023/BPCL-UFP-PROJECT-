"""
ERA5-Land Data Fetcher
======================

Downloads ERA5-Land reanalysis data from the Copernicus Climate
Data Store (CDS) for the Kota–Bijwasan pipeline bounding box.

Variables downloaded:
  - soil_temperature_level_2  (7–28 cm depth)  [K]
  - soil_temperature_level_3  (28–100 cm depth) [K]  ← primary
  - volumetric_soil_water_layer_3 (28–100 cm)   [m³/m³]

Requirements:
  1. CDS account: https://cds.climate.copernicus.eu/
  2. API key in ~/.cdsapirc or in api key.txt
  3. Accept ERA5-Land Terms of Use on CDS website

Strategy:
  - Downloads year-by-year to avoid CDS request timeout
  - Retries failed requests up to 3 times
  - Merges annual files into single NetCDF
  - Falls back to synthetic data if download fails
"""

import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

import xarray as xr

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import (
    ERA5_BBOX,
    ERA5_DAYS,
    ERA5_MONTHS,
    ERA5_OUTPUT_FILE,
    ERA5_TIMES,
    ERA5_VARIABLES,
    ERA5_YEARS,
)

logger = logging.getLogger(__name__)


def _setup_cdsapi_key(api_key_file: Optional[str] = None) -> bool:
    """Ensure CDS API key is configured.

    Checks (in order):
      1. Existing ~/.cdsapirc file
      2. CDSAPI_URL + CDSAPI_KEY environment variables
      3. api_key_file parameter (reads token from file)

    Parameters
    ----------
    api_key_file : str, optional
        Path to a text file containing the CDS personal access token.

    Returns
    -------
    bool
        True if a CDS API key is available, False otherwise.
    """
    # Check existing .cdsapirc
    cdsapirc = Path.home() / ".cdsapirc"
    if cdsapirc.exists():
        content = cdsapirc.read_text().strip()
        if "key:" in content and len(content) > 20:
            logger.info("CDS API key found in ~/.cdsapirc")
            return True

    # Check environment variables
    if os.environ.get("CDSAPI_KEY"):
        logger.info("CDS API key found in environment variable")
        return True

    # Try to read from api_key_file
    if api_key_file:
        key_path = Path(api_key_file)
        if key_path.exists():
            token = key_path.read_text().strip()
            if len(token) > 10:
                os.environ["CDSAPI_URL"] = "https://cds.climate.copernicus.eu/api"
                os.environ["CDSAPI_KEY"] = token
                logger.info(f"CDS API key loaded from {api_key_file}")
                return True

    logger.warning("No CDS API key found. Will use synthetic ERA5 data.")
    return False


def download_era5_year(
    year: int,
    output_dir: str,
    max_retries: int = 3,
) -> Optional[str]:
    """Download ERA5-Land data for a single year.

    Parameters
    ----------
    year : int
        Year to download (e.g., 2023).
    output_dir : str
        Directory to save the annual NetCDF file.
    max_retries : int
        Maximum number of retry attempts.

    Returns
    -------
    str or None
        Path to downloaded file, or None if download failed.
    """
    try:
        import cdsapi
    except ImportError:
        logger.error("cdsapi not installed. Run: pip install cdsapi")
        return None

    output_path = os.path.join(output_dir, f"era5_soil_{year}.nc")

    # Skip if already downloaded
    if os.path.exists(output_path):
        logger.info(f"ERA5 data for {year} already exists: {output_path}")
        return output_path

    north, west, south, east = ERA5_BBOX
    request = {
        "variable": ERA5_VARIABLES,
        "year": [str(year)],
        "month": [f"{m:02d}" for m in ERA5_MONTHS],
        "day": [f"{d:02d}" for d in ERA5_DAYS],
        "time": ERA5_TIMES,
        "area": [north, west, south, east],
        "data_format": "netcdf",
        "download_format": "unarchived",
    }

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Downloading ERA5 data for {year} (attempt {attempt}/{max_retries})...")
            client = cdsapi.Client()
            client.retrieve(
                "reanalysis-era5-land",
                request,
                output_path,
            )
            logger.info(f"✓ ERA5 {year} downloaded: {output_path}")
            return output_path

        except Exception as e:
            logger.warning(f"ERA5 download failed for {year} (attempt {attempt}): {e}")
            if attempt < max_retries:
                wait = 30 * attempt  # exponential-ish backoff
                logger.info(f"  Retrying in {wait}s...")
                time.sleep(wait)
            else:
                logger.error(f"✗ ERA5 download for {year} failed after {max_retries} attempts")
                return None


def fetch_era5(
    output_dir: str = ".",
    api_key_file: Optional[str] = None,
    years: Optional[list] = None,
) -> Optional[str]:
    """Download and merge ERA5-Land data for all configured years.

    Parameters
    ----------
    output_dir : str
        Directory for downloaded files and merged output.
    api_key_file : str, optional
        Path to a text file containing the CDS API token.
    years : list of int, optional
        Override configured years. Default: ERA5_YEARS from config.

    Returns
    -------
    str or None
        Path to the merged NetCDF file, or None if download failed.
    """
    if years is None:
        years = ERA5_YEARS

    os.makedirs(output_dir, exist_ok=True)
    merged_path = os.path.join(output_dir, ERA5_OUTPUT_FILE)

    # Check if merged file already exists
    if os.path.exists(merged_path):
        logger.info(f"Merged ERA5 data already exists: {merged_path}")
        return merged_path

    # Setup API key
    if not _setup_cdsapi_key(api_key_file):
        logger.warning("No API key. Generating synthetic data instead.")
        from data.era5_synthetic import generate_synthetic_era5

        generate_synthetic_era5(output_path=merged_path)
        return merged_path

    # Download year by year
    annual_files = []
    for year in years:
        filepath = download_era5_year(year, output_dir)
        if filepath:
            annual_files.append(filepath)
        else:
            logger.warning(f"Skipping year {year} — download failed")

    if not annual_files:
        logger.error("No ERA5 data downloaded. Falling back to synthetic data.")
        from data.era5_synthetic import generate_synthetic_era5

        generate_synthetic_era5(output_path=merged_path)
        return merged_path

    # Merge annual files
    logger.info(f"Merging {len(annual_files)} annual files...")
    datasets = [xr.open_dataset(f) for f in annual_files]
    merged = xr.concat(datasets, dim="time")
    merged = merged.sortby("time")
    merged.to_netcdf(merged_path)

    # Close and optionally clean up annual files
    for ds in datasets:
        ds.close()

    logger.info(f"✓ Merged ERA5 data saved: {merged_path}")
    return merged_path


# ─── CLI usage ────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # Look for api key in project root
    project_root = Path(__file__).parent.parent.parent
    api_key_path = project_root / "api key.txt"

    result = fetch_era5(
        output_dir=str(project_root / "data"),
        api_key_file=str(api_key_path),
    )

    if result:
        print(f"\nERA5 data ready: {result}")
    else:
        print("\nFailed to obtain ERA5 data.")
