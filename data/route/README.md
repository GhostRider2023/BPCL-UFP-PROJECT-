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

### 2. The 8-inch tail segment has been REMOVED pending verification

`config.py` previously declared the final 20 km (Piyala → Bijwasan) as **8-inch**. Pushing
the full mainline throughput through an 8-inch bore implies:

```
v ≈ 6 m/s          (design maximum is ~2.5 m/s)
ΔP ≈ 120+ bar      over just 20 km
```

Both are physically implausible for a mainline and were dominating the total pressure
drop, producing nonsense hydraulics.

The most likely explanation is that Piyala → Bijwasan is a **spur** carrying only part of
the flow, not the full mainline. Until that is confirmed, this file models a **uniform
16-inch line end to end**, which is the conservative and defensible assumption.

**Action required:** confirm the true diameter and the flow split at Piyala against PNGRB
tariff filings or BPCL drawings, then set `od_inch` for waypoint 7 accordingly. If it is
genuinely an 8-inch spur, the model needs a flow-split node at Piyala, not just a
diameter change.
