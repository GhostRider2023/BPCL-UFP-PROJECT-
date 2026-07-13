# BPCL Kota–Bijwasan Thermal UFP Quantification System

A pure-physics engine for quantifying Unaccounted-For Product (UFP) on the
BPCL/MMBL Kota → Bharatpur → Piyala → Bijwasan petroleum pipeline.

All thermal and volumetric computations follow published standards:

- API MPMS Chapter 11.1 (VCF / CTL)
- Johansen (1975) soil thermal conductivity
- Darcy-Weisbach / Colebrook-White friction
- ERA5-Land reanalysis for soil climate data

No machine learning. No hardcoded temperatures. Pure physics.

## Project structure

```
config.py           Pipeline geometry, fluid properties, terrain zones, constants
model/               Core physics: friction, heat transfer, VCF, soil profile, UFP, optimizer
geo/                 Route centreline and geodesy
data/                ERA5 fetch/synthesis, GRIB loading, soil CSV generation
ingestion/           SCADA data validation
dashboard/           Streamlit UI (app, schematic, theme)
tests/               Unit tests
validation/          Physics regression and validation suites
```

## Setup

```bash
pip install -r requirements.txt
```

## Data files

The large NetCDF soil/climate datasets (`data/*.nc`) are not committed to this
repository (they exceed GitHub's file size limits). Regenerate them locally with:

```bash
python -m data.era5_fetch        # fetch real ERA5-Land data (requires a CDS API key, see below)
python -m data.era5_synthetic    # or generate synthetic data instead
python -m data.generate_soil_csv # derive the soil profile CSV used by the model
```

To fetch real ERA5 data, either place a CDS personal access token in
`~/.cdsapirc`, or set the `CDSAPI_URL` / `CDSAPI_KEY` environment variables.
See `data/era5_fetch.py` for details.

The pipeline route geometry lives in `data/route/waypoints.csv` — see
`data/route/README.md` for column definitions and current data-quality caveats.

## Tests

```bash
pytest tests/ validation/
```

## Dashboard

```bash
streamlit run dashboard/app.py
```
