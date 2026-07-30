# Route definition — the pipeline as data

`waypoints.csv` is the single source of truth for the pipeline's geometry. The physics
code reads it; nothing about the route is hardcoded. Extending the model to
**Bina → Kota → Bharatpur → Piyala → Bijwasan** is an edit to this file, not a code change:
add the upstream waypoints, renumber `chainage_km`, done.

## Columns

| column | units | meaning |
|---|---|---|
| `waypoint_id` | — | ordinal along the route |
| `name` | — | display name |
| `lat`, `lon` | deg | WGS-84 |
| `chainage_km` | km | cumulative route distance from the dispatch terminal |
| `elevation_m` | m | ground elevation — drives the `ρ·g·dz` term in the momentum equation |
| `od_inch` | in | pipe outer diameter |
| `wall_thickness_mm` | mm | wall thickness → inner diameter |
| `roughness_mm` | mm | absolute internal roughness (API 5L new steel ≈ 0.045 mm) |
| `burial_depth_m` | m | depth to pipe centre — sets the soil thermal resistance |
| `station_type` | — | `dispatch` / `receipt` / `waypoint` / `crossing` / `pump` |
| `source` | — | `SURVEYED` or `ESTIMATED` — **never leave this blank** |

Values apply **from this waypoint forward** until the next one overrides them.

## Data-quality flags — read before trusting any output

**Everything in this file is currently `ESTIMATED`.** It must be replaced with surveyed
values from BPCL/PNGRB before any result is presented as authoritative.

### 1. Elevations are approximate

Sourced from public terrain knowledge, not a DEM. They are good to perhaps ±30 m, which
is ±2.4 bar of static head. Replace with SRTM/Cartosat sampling along the centreline.

Elevation is **not** cosmetic: 100 m of elevation is ~8 bar. The old plan proposed
stubbing it to zero, which would have silently corrupted the pressure profile and any
slack-flow assessment.

### 2. The line is an 18″ mainline with an 8″ tail

| segment | chainage | `od_inch` | wall | D_inner | area |
|---|---|---|---|---|---|
| Bina → Piyala | 0–599 km | 18.0 (NPS 18) | 14.27 mm Sch 40 | 0.42866 m | **0.144316 m²** |
| Piyala → Bijwasan | 599–619 km | 8.625 (NPS 8) | 8.18 mm Sch 40 | 0.20271 m | **0.032275 m²** |

Line fill: **87,035 KL**.

Sources describe the MMBPL Manmad → Piyala → Bijwasan section as **18″/8″** over 750 km,
and the BPCL system's maximum diameter as 18″. That matches this project's original
declaration of Piyala→Bijwasan as an 8″ spur, which a later revision removed.

> **`od_inch` carries the TRUE outer diameter, not the nominal size.** For NPS 14 and
> below the two differ: NPS 8 is 8.625 in OD, NPS 10 is 10.75 in. From NPS 14 up,
> nominal equals actual — so NPS 18 really is 18.000 in. Earlier revisions used literal
> nominal inches for the small sizes, which understated the bore.

### 2a. Why 3 m/s is achievable — and what got it wrong before

Modelling the whole mainline as 8″ made a 3 m/s design velocity look impossible: it
implied ~900 bar (petrol) to ~1200 bar (diesel) of friction head, a gradient of
2.5–3.3 bar/km against 0.05–0.15 bar/km typical for product lines. Two input errors
caused that, neither of them in the physics:

1. **Diameter.** The mainline is 18″, not 8″. At 3 m/s an 18″ bore loses **1.02 bar/km**,
   not 2.50.
2. **Pumping.** A long line is not pumped from one end. With booster stations every
   ~80 km each section only has to make up **81 bar**, comfortably inside a ~100 bar
   MAOP, and the line runs 3 m/s **with no slack flow** — which is what BPCL operations
   describe. See `config.PUMP_STATIONS`.

Turbulent flow is *not* an error and never was: `Re` here is 10⁵–10⁶, which is exactly
where product pipelines are designed to sit, and where the friction factor is nearly
Reynolds-independent. The only incorrect claim was "laminar", which would need
`μ ≈ 219 cP` — heavy-crude territory, not MS/HSD/ATF. Pinned by
`validation/test_openweather.py::TestMMBPLGeometry::test_flow_is_turbulent_at_realistic_velocities`.

### 2b. No mid-route delivery — the tail sets the throughput

`config.FLOW_SPLITS` is **empty by design**. The batch is dispatched at the Bina refinery
(km 0) and received in full at Bijwasan (km 619); nothing is taken off in between.

That is a modelling decision, not an oversight. This simulator answers a custody-transfer
question — how much left Bina, how much arrived at Bijwasan, and is the difference
thermal contraction or real loss. A mid-route off-take would sit inside that very
comparison and turn delivered product into apparent unaccounted-for product.

**Consequence.** One flow passes through both bores, so the 8″ tail binds:

```
349 m3/hr  ->  0.67 m/s mainline,  3.00 m/s tail    <- tail at design velocity
465 m3/hr  ->  0.90 m/s mainline,  4.00 m/s tail    <- petrol marginal, diesel slack
```

The 18″ mainline runs well below its own design velocity. A trunk line narrowing for its
final approach into a city terminal behaves exactly this way. **Do not add a FlowSplit to
raise the apparent throughput** — the honest answer to a low ceiling is a lower flow rate.

The split mechanism is retained and tested (`validation/test_simulator.py::TestFlowSplit`)
because the real MMBPL line does deliver at Piyala, and modelling the wider system will
need it. `TestFlowSplit::test_the_shipped_configuration_has_no_delivery` pins the empty
default so it cannot be enabled by accident.

### 3. Bina has no ERA5 soil coverage

The route now starts at the **Bina refinery, km 0** (24.1866 °N, 78.1919 °E). The cached
ERA5 datasets were downloaded for a box that stops at **25.0 °N / 78.0 °E**, so Bina lies
outside it on both axes:

```
ERA5 coverage : lat 25.00-29.00,  lon 75.50-78.00
Bina          : lat 24.19      ,  lon 78.19        <- outside, both axes
```

`xarray`'s `method="nearest"` never fails — it silently returns the closest edge cell,
~95 km away. `data/generate_soil_csv.py` now measures that offset and labels anything
beyond 25 km as `EXTRAPOLATED_OUTSIDE_ERA5_BBOX`, recorded per row in `soil_source` and
`era5_cell_offset_km`.

**What this does and does not affect.** Bina's own boundary condition is `(air + fuel)/2`
— the pipe is above ground at the refinery — so the extrapolated soil value is not used
there. It *is* used along the Bina→Kota corridor, roughly the first 259 km.

**Action required:** `config.ERA5_BBOX` is already widened to `(29.0, 75.5, 23.5, 79.0)`.
Re-download with a CDS personal access token and re-run `python data/generate_soil_csv.py`;
the warning will disappear when every row reads `ERA5`.

### 4. Weather coordinates are held separately

`lat`/`lon` here define **pipeline geometry** — they drive geodesic chainage and the
ERA5 soil lookup, and must not be perturbed. The coordinates used to query the current
air temperature at the two terminals — Bina and Bijwasan, the only stations whose air
enters the physics — live in `config.AIR_STATIONS`, and are mapped onto these waypoints
by `config.AIR_STATION_TO_WAYPOINT`.
