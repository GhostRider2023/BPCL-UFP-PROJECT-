# MMBL Thermal-Hydraulic Simulator — Vercel build

The BPCL/MMBL **Bina → Kota → Bharatpur → Piyala → Bijwasan** pipeline simulator,
rebuilt as a React control room on Vercel instead of a Streamlit app.

**The physics is unchanged.** `config.py`, `model/` and `geo/` are the same modules
the Streamlit project runs, and the numbers this deployment produces are pinned
against that project across 243 scenarios (see [Validation](#validation)). What
changed is everything around them: the UI, the transport, and one numerical
dependency that could not fit in a serverless function.

---

## What this is

Given a product dispatched at a temperature, volume, flow rate and pressure, in a
given month — what is its physical state at every kilometre of the route?

It is a **simulator**, not a leak detector and not an accounting reconciliation
engine. The energy and momentum equations are integrated together as one ODE
system, because they are coupled three ways: temperature sets viscosity and
therefore friction, friction heats the oil, and temperature sets density and
therefore the static head.

No machine learning anywhere in the physics path. Sources: API MPMS Ch. 11.1 /
11.2.1, Johansen (1975), Colebrook / Swamee–Jain, Çengel Ch. 3, ERA5-Land.

---

## Running it locally

Requires **Node 18+** and **Python 3.9+**. No Vercel account needed.

```bash
npm install
pip install -r requirements.txt
npm run dev
```

That starts both halves: `tools/devserver.py` on port 8000 serving the API, and
Vite on <http://localhost:5173> proxying `/api/*` to it. Open the Vite URL.

If you have the Vercel CLI, `vercel dev` runs the real serverless handlers
instead. Both call the same functions in `api/_engine/service.py`, so they behave
identically — that equivalence is why the service layer exists.

```bash
npm run build     # typecheck + production bundle into dist/
npm run lint      # typecheck only
```

---

## Deploying

```bash
npm i -g vercel     # once
vercel login
vercel              # preview deployment
vercel --prod       # production
```

Vercel detects the Vite frontend and the Python functions in `api/` on its own;
`vercel.json` pins the Python runtime, the function memory and the SPA rewrite.
Or connect the repo at <https://vercel.com/new> and it deploys on push.

### Environment variables (all optional)

| Variable | Default | Effect |
|---|---|---|
| `OPENWEATHER_API_KEY` | unset | Use OpenWeather for the live air temperature. |
| `AIR_TEMPERATURE_PROVIDER` | `auto` | `auto`, `openweather` (fail rather than fall back), or `open-meteo`. |

**No key is required.** Without one the app uses Open-Meteo, which needs none, and
labels every reading with the source that produced it. Set them under Project
Settings → Environment Variables.

---

## Architecture

```
api/
  simulate.py        POST  one run: state at every km, station table, KPIs
  meta.py            GET   products, months, route, slider bounds, presets
  air.py             GET   live air at the two terminals  (?force=1 to bypass cache)
  export.py          POST  the full state table as CSV
  _http.py                 shared request/response plumbing (not routed — leading _)
  _engine/                 the physics, vendored from kota_bijwasan_ufp/
    service.py             the only layer that knows about JSON
    numerics.py            RK45 + interp1d in NumPy  ← the one real change
    config.py  model/  geo/  data/
src/                       React control room (Vite + TypeScript + Tailwind v4)
tools/
  devserver.py             local API, same service functions as the Vercel handlers
  validate_against_scipy.py
  _run_grid.py
```

The browser holds the console state, debounces changes by 220 ms, and POSTs the
input vector to `/api/simulate`. A run takes **~0.3 s** of solve time on 620
chainages. In-flight requests are aborted when parameters change again, so a slow
response can never land after a fast one and repaint the screen with numbers that
do not match the console.

---

## The one substantive change: SciPy is gone

A Vercel serverless function has a **250 MB** unzipped budget. The dependency set
the Streamlit app runs does not fit:

| package | installed |
|---|---:|
| numpy | 33 MB |
| pandas | 65 MB |
| **scipy** | **118 MB** |
| xarray + netCDF4 + cfgrib + eccodes | ~90 MB |

SciPy supplied exactly two things to this project: `solve_ivp` and `interp1d`.
Both are reimplemented in `api/_engine/numerics.py` in NumPy alone —
a Dormand–Prince 5(4) integrator with the same tableau and the same dense-output
interpolant as `scipy.integrate.RK45`, and a 1-D interpolator with linear
extrapolation.

The NetCDF stack is only needed to **build** the soil table from ERA5, which
happens offline on a workstation with a CDS key. At runtime the simulator reads
the derived 8 KB CSV and never opens a NetCDF file — so those five packages are
not deployed at all.

Result: **~100 MB installed**, comfortably inside the budget, and several seconds
faster to cold-start.

### Is RK45 the right substitute for LSODA?

LSODA switches between an Adams method (non-stiff) and BDF (stiff) on a running
stiffness estimate. This system is not stiff and LSODA runs it in Adams mode
throughout: the temperature equation is a smooth relaxation with a characteristic
length of hundreds of kilometres, the pressure equation is a smooth quadrature,
and the only discontinuities on the route — booster stations and flow splits —
are handled by *breaking the integration at those chainages* rather than by
stepping over them. An explicit RK45 at the same tolerances solves the same
problem the same way.

That argument is not taken on faith.

---

## Validation

```bash
pip install scipy          # dev-only; not in requirements.txt
python tools/validate_against_scipy.py
```

Runs the full `simulate()` over 243 scenarios — 3 products × 3 months × 3 flows ×
3 dispatch temperatures × 3 pressures, including the infeasible slack-flow corner
— against **both** engines in separate processes, and compares every reported
column at all 620 chainages.

```
  column             max abs diff     budget  / column span
  ----------------- ------------- ---------- --------------
  T_C                   1.000e-04    5.0e-04       3.33e-06
  P_bar                 5.000e-04    5.0e-03       3.62e-06
  rho_kgm3              5.207e-05    1.0e-03       4.08e-07
  mu_cP                 2.000e-06    1.0e-04       5.12e-07
  velocity_ms           1.300e-07    1.0e-05       3.40e-08
  Re                    1.000e+00    5.0e+00       7.45e-07
  friction_factor       1.000e-08    1.0e-06       8.91e-07
  CTL                   2.000e-08    1.0e-06       5.52e-07
  CPL                   6.000e-08    1.0e-06       4.70e-06
  V_gross_KL            7.000e-05    5.0e-03       1.10e-06
  V_std_KL              0.000e+00    5.0e-03       0.00e+00
  U_Wm2K                0.000e+00    1.0e-06       0.00e+00
  L_star_km             0.000e+00    1.0e-06       0.00e+00
  T_env_C               0.000e+00    1.0e-06       0.00e+00
  T_soil_C              0.000e+00    1.0e-06       0.00e+00

  PASS — no column differs from the SciPy build by more than 4.7e-06 of its
  own range, and every difference sits at the last digit the kernel stores.
```

`V_std_KL`, both boundary-condition columns, `U` and `L*` are **bit-identical**.
Everything else agrees at the last stored digit: `model/kernel.py` rounds each
column before it enters the DataFrame (`round(T_C, 4)`, `round(Re, 0)`), and two
integrators that agree perfectly in double precision still land on opposite sides
of a rounding boundary. The tolerances are set per column at a few quanta of that
column's own stored precision, and each disagreement is also reported as a
fraction of the column's full range — which is the physically meaningful number,
and is undefined for a relative test on the scenarios where gauge pressure passes
through zero.

**Re-run this whenever `numerics.py` changes.**

---

## What the UI does that matters

Most of it is presentation, but four decisions are load-bearing:

- **Air is drawn as markers, never as a line.** There is a live air measurement at
  exactly two points on a 619 km route. Joining them with a curve would draw an
  air temperature across hundreds of kilometres of buried pipe where none was
  measured and none is used. In the station table those cells are *blank*, and the
  wire format types them `number | null` so no `?? 0` can creep in.
- **The standard-volume axis is pinned to ±1 KL around its own mean.** Left to
  autoscale, a quantity that is invariant by construction renders as a dramatic
  wiggle of floating-point noise — motion where the model's strongest guarantee is
  that there is none.
- **A failed air request is reported as a failure**, on the masthead, in a callout,
  and as a blank cell. No air temperature is ever invented to fill a gap; the run
  falls back to soil alone and says so.
- **Ablated physics is flagged in red.** A model missing its viscous-heating term
  produces plausible numbers, and plausible-but-wrong is the failure worth
  shouting about.

The console rail replaces the sidebar: each slot *reads back* the value currently
loaded into the model and opens into the control that sets it. A sidebar shows you
widgets and never shows you state; a control room does the opposite.

---

## Known limits

- **Soil data gap at Bina.** The cached ERA5 datasets stop at 25.0 °N / 78.0 °E, but
  Bina sits at 24.19 °N / 78.19 °E. Its soil row is extrapolated from the nearest
  edge cell ~95 km away and is flagged `EXTRAPOLATED_OUTSIDE_ERA5_BBOX` in
  `data/kota_bijwasan_soil_profile.csv`. It does not affect Bina's own boundary
  condition — the pipe is above ground there and uses (air + fuel)/2 — but it does
  affect the Bina→Kota corridor. Re-download ERA5 with a CDS key to close it.
- **The soil table has 11 months, not 12.** The month selector offers what the table
  actually contains rather than interpolating a twelfth.
- **`config.FLOW_SPLITS` is empty by design.** The mid-route delivery mechanism
  exists and is tested, but enabling it would place an off-take inside the
  dispatch-vs-receipt comparison this model exists to make, and turn delivered
  product into apparent UFP.
- **U-values are ±30 % at best.** Johansen plus the burial correlations are what
  they are; the `U ×` multiplier is the knob a calibration step would fit against a
  measured receipt temperature. 1.00 means uncalibrated.
- **Cold starts.** The first request to an idle function pays NumPy and pandas
  import time (~1–2 s). Warm requests are ~0.3 s. Route geometry and the soil table
  are cached for the life of the instance.

---

## Regenerating the soil data

Unchanged from the original project, and still done there — it needs the NetCDF
stack that this deployment deliberately omits:

```bash
cd ../kota_bijwasan_ufp
python -m data.era5_fetch          # real ERA5-Land (needs a CDS API key)
python -m data.era5_synthetic      # or synthetic instead
python -m data.generate_soil_csv   # derive the profile CSV
cp data/kota_bijwasan_soil_profile.csv ../bpcl-vercel/api/_engine/data/
```

The `.nc` archives are 220 MB and are `.vercelignore`d and `.gitignore`d — they are
regeneration inputs, never runtime data.
