# BPCL Bina–Bijwasan Thermal UFP Quantification System

A pure-physics engine for quantifying Unaccounted-For Product (UFP) on the
BPCL/MMBL **Bina → Kota → Bharatpur → Piyala → Bijwasan** petroleum pipeline.
Product is dispatched at the Bina refinery (km 0) and received in full at
Bijwasan (km 619); nothing is taken off in between.

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

## Real-time air temperature (OpenWeather)

The energy equation couples the product to an **environment temperature**.
Live air is blended in **only at the two terminals**:

```
Bina     (km 0,   above ground):  T_env = (T_air_current + T_fuel_dispatch) / 2
Bijwasan (km 619, buried):        T_env = (T_air_current + T_soil) / 2
everywhere else, incl. Kota:      T_env = T_soil
```

The terminals are where the line surfaces — above-ground manifolds, meter skids
and shallow approach spools track the open air. Between them the pipe is buried
at 1.2 m for hundreds of kilometres, where the daily air signal does not
penetrate and the ERA5 deep-soil temperature is the only correct boundary
condition. Air is therefore **queried at those two terminals and nowhere else** —
fetching the intermediate stations would put numbers on screen that change
nothing in the physics. See `config.AIR_STATIONS`.

`T_soil` is the existing ERA5-Land layer-4 (100–289 cm) monthly climatology,
unchanged. `T_air_current` is the **current** air temperature at that station's
own coordinates, fetched at simulation time from a live weather API. It is never
a forecast, never interpolated in time, and never a climatology.

**Why Bina is different.** The product leaves the refinery in **surface
pipework** — not yet buried, so the soil is not in contact with it at all. Its
environment is the open air and the product it carries, hence
`T_env = (T_air + T_fuel)/2`, reported as basis `air+fuel`. `T_fuel` is the
temperature the product leaves the refinery at, entered by the operator; it is
a meter measurement, not a solved quantity, which is what makes it admissible
in a boundary condition. This applies at the refinery only — anywhere
downstream the product temperature is solved, and using it would be circular.
**Kota is an ordinary buried station** at km 259, on soil like every other
intermediate point.

If a station's request fails, that station is reported as failed — in the log,
in the station table (blank `Air Temp`), and on the dashboard masthead — and
falls back to soil alone. **No air temperature is ever invented to fill a gap.**

### Sources, in order

| # | Source | Key needed | Notes |
|---|---|---|---|
| 1 | **OpenWeather** Current Weather API | yes | The specified source. `units=metric`, `main.temp`, one request per station. |
| 2 | **Open-Meteo** | **no** | Automatic fallback. One batched request for all 8 stations. Used when no key is configured, the key is unactivated (401), rate-limited (429), or the service is down. |

**The dashboard works out of the box with no API key** — it will use Open-Meteo
and label every reading with the source that produced it.

Both are real-time sources for *now*; neither is a forecast. They differ in
kind: OpenWeather's `main.temp` is closer to a station observation, Open-Meteo's
`current` is the current hour of a numerical weather analysis interpolated to
the point. The dashboard prints which one answered, so the provenance of the
numbers on screen is never assumed.

If **both** fail, the run proceeds on ERA5 soil temperature alone and says so.
An air temperature is never invented.

Pin a provider explicitly with `AIR_TEMPERATURE_PROVIDER=openweather` (fail
rather than fall back), `=open-meteo`, or `=auto` (default).

```bash
python data/air_temperature.py   # whichever source is available
python data/openweather.py       # OpenWeather only
python data/open_meteo.py        # Open-Meteo only, no key
```

### Configuring the OpenWeather API key (optional)

Get a free key at <https://openweathermap.org/api> ("Current Weather Data"). A
new key takes about 10 minutes to activate; before that the API returns HTTP
401 and the app falls back to Open-Meteo.

The key is never stored in source. It is resolved in this order:

1. the `OPENWEATHER_API_KEY` environment variable
2. `.env` at the project root — `OPENWEATHER_API_KEY=...` (see `.env.example`)
3. `.streamlit/secrets.toml` — `OPENWEATHER_API_KEY = "..."` (see
   `.streamlit/secrets.toml.example`); also the right choice when deploying to
   Streamlit Community Cloud
4. `openweather_key.txt` at the project root, key on a single line — mirroring
   the convention `data/era5_fetch.py` already uses for the CDS token

All four are gitignored.

```bash
# quick check — prints the current temperature at Bina and Bijwasan
python data/openweather.py
```

## Line configuration

| | |
|---|---|
| Bina → Piyala | **18″** (NPS 18, Sch 40, 14.27 mm) — 599 km |
| Piyala → Bijwasan | **8″** (NPS 8, Sch 40, 8.18 mm) — 20 km |
| Booster stations | 7, every 80 km from km 80 to km 560, discharging at 100 bar |
| Mid-route delivery | **none** — the batch runs Bina → Bijwasan intact |

> **Soil data gap.** The cached ERA5 datasets stop at 25.0 °N / 78.0 °E, but Bina sits at
> 24.19 °N / 78.19 °E. Its soil row is therefore extrapolated from the nearest edge cell
> ~95 km away and is flagged `EXTRAPOLATED_OUTSIDE_ERA5_BBOX` in
> `data/kota_bijwasan_soil_profile.csv`. It does **not** affect Bina's own boundary
> condition — the pipe is above ground there and uses (air + fuel)/2 — but it does affect
> the Bina→Kota corridor. `config.ERA5_BBOX` is already widened; re-download ERA5 with a
> CDS key to close the gap.

Geometry lives in `data/route/waypoints.csv`; pumping is operational configuration in
`config.py` (`PUMP_STATIONS`).

### The 8″ tail sets the throughput

The whole batch passes through both bores, and the tail's area is **4.47× smaller**, so
it runs 4.47× faster than the mainline. The tail therefore binds:

```
349 m3/hr  ->  0.67 m/s mainline,  3.00 m/s tail     <- tail at design velocity
465 m3/hr  ->  0.90 m/s mainline,  4.00 m/s tail     <- petrol marginal, diesel slack
```

The 18″ mainline is correspondingly under-utilised. That is what a trunk line narrowing
for its final approach into a city terminal does — it is not a modelling artefact, and
the fix is not to invent a mid-route delivery.

Feasibility at the top of that range is a property of the **product**, not just the flow:
at 465 m³/hr petrol survives the worst month by +9 bar while diesel, ~6× more viscous,
goes slack by −20 bar.

### Reading the volume figures

Nothing is taken off between the terminals, so the strong invariant holds and is the
project's headline acceptance test:

```
V_std(Bijwasan) == V_std(Bina)      to machine precision
```

Gross volume breathes with temperature and pressure; standard volume does not move.
Any difference between the dispatch and receipt standard volumes is therefore genuine
unaccounted-for product, which is the entire point of the exercise.

`config.FLOW_SPLITS` is empty by design. The mid-route delivery *mechanism* exists and is
tested — the real MMBPL line does have a delivery terminal at Piyala — but enabling it
would place an off-take inside the very dispatch-vs-receipt comparison this model makes,
and turn delivered product into apparent UFP.

See `data/route/README.md` for column definitions and data-quality caveats.

## Tests

```bash
pytest tests/ validation/
```

## Dashboard

```bash
streamlit run dashboard/app.py
```
