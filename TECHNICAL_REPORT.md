# Thermal–Hydraulic UFP Quantification System

**MMBL Kota–Bijwasan Section — Engineering Technical Report**

| | |
|---|---|
| **Prepared for** | Bharat Petroleum Corporation Limited — MMBL Pipeline Engineering |
| **Prepared by** | Divyansh Sharma |
| **Date** | 14 July 2026 |
| **Status** | Engineering demonstrator — Phase 3 of 9 (physics core validated; awaiting BPCL survey & custody data) |
| **Scope** | Kota → Bharatpur → Piyala → Bijwasan, 360 km |
| **Repository** | [github.com/GhostRider2023/BPCL-UFP-PROJECT-](https://github.com/GhostRider2023/BPCL-UFP-PROJECT-) |

A rendered, designed version of this report is also available as a standalone HTML document.

---

## 0. Executive Summary

This project builds a first-principles physics simulator for the MMBL product pipeline between
Kota and Bijwasan, to answer one question: *given a batch of petrol, diesel or ATF dispatched at
a known temperature, volume and flow rate, what is its physical state — temperature, pressure,
density, volume — at every kilometre of the line?* It is not a leak detector and not a
replacement for custody metering. It is the physics layer that a leak-detection or
inventory-reconciliation system needs underneath it.

**Headline numbers:**

| | |
|---|---|
| **0.00e+00 %** | standard-volume drift along the full 360 km line, measured across 99 product × month × temperature runs |
| **75 / 75** | automated physics & regression tests passing (conservation laws, not curve-fits) |
| **34.1 KL/°C** | how much the line's own inventory (line pack) moves per degree of mean temperature — the real thermal signal |

The headline engineering result is that **the model conserves what it should and moves what it
should**: as a batch travels the line, its gross (observed) volume changes by 1–2 % with
temperature and pressure, while its standard volume at 15 °C — the mass-equivalent quantity
custody transfer is built on — stays constant to machine precision. That is exactly what API MPMS
volume correction is designed to do, and the simulator reproduces it from the underlying physics
rather than assuming it.

The more consequential finding came from questioning the project's original premise. A per-batch
thermal-UFP model — predicting how much volume a single batch appears to lose between dispatch
and receipt, purely from cooling — turns out to have **no customer**: if BPCL applies volume
correction at both meters, that quantity is identically zero, by construction, regardless of how
much the batch cools (§3.1). The real, uncorrected thermal signal lives in the pipeline's **line
pack** — the 41,000+ KL of product permanently resident in the pipe — whose standard volume
shifts by tens to hundreds of kilolitres with the season and with operating pressure. That shift
is the same order of magnitude as the 0.1 % leak-detection sensitivity BPCL and PNGRB would care
about (§3.3). This reframes the project from a per-batch correction tool into the physics core of
an **API 1130–style line-pack-compensated volume balance**, which is both more defensible and
more directly useful to pipeline operations.

> **What this is not, yet.** Every input to this simulator that is not a published physical
> constant or standard (API MPMS, Johansen 1975, Cengel, Colebrook) is currently an **estimate**:
> route waypoints and elevations, the pipe diameter on the final approach to Bijwasan, and the
> soil heat-transfer coefficient. The model has not yet been calibrated against a single measured
> BPCL data point, because none has been supplied. §6 lists every such item explicitly, with what
> is needed from BPCL to close it.

What is built and validated today (Phases 0–3 of the 9-phase roadmap in §7): a data-driven route
model; a corrected ERA5-Land soil boundary condition; a coupled thermal-hydraulic solver
integrating temperature and pressure simultaneously along the line; full API MPMS 11.1/11.2.1
volume correction; and a self-consistency validation suite standing in for measured ground truth.
Not yet built: the line-fill/inventory tracker, the line-pack-compensated volume-balance
accounting layer, parameter calibration against SCADA data, and statistical leak detection
(CUSUM/SPRT) — the layers that turn this from a validated physics demonstrator into an
operational tool.

---

## Contents

1. [Problem Statement & Scope](#1-problem-statement--scope)
2. [Governing Physics & Methodology](#2-governing-physics--methodology)
3. [Reframing: Thermal UFP as Line-Pack Volume Balance](#3-reframing-thermal-ufp-as-line-pack-volume-balance)
4. [System Architecture](#4-system-architecture)
5. [Validation & Quality Assurance](#5-validation--quality-assurance)
6. [Data Provenance & Known Limitations](#6-data-provenance--known-limitations)
7. [Roadmap](#7-roadmap--what-remains-before-production-use)
8. [Repository & Reproducibility](#8-repository--reproducibility)
9. [Appendix A: Symbols & Units](#appendix-a-symbols--units)
10. [Appendix B: References](#appendix-b-references)

---

## 1. Problem Statement & Scope

The MMBL line carries Petrol, Diesel and ATF roughly 360 km underground from Kota to Bijwasan,
via Bundi, Sawai Madhopur, Bharatpur, a crossing near Mathura, Faridabad and Piyala. Buried
petroleum in transit is not thermally inert: it exchanges heat with the ground, is heated by its
own friction, and its pressure — and therefore its density and volume — changes continuously
along the route. Unaccounted-For Product (UFP) is the gap between what is dispatched and what is
received, after correcting for these physical effects; anything left over is the genuine target
of loss control — leak, theft, or metering error.

This system answers, for a given product, dispatch temperature, volume, flow rate and month: what
is the product's temperature, pressure, density, viscosity, gross volume and standard volume at
every kilometre? It deliberately does **not** attempt leak detection or accounting reconciliation
directly — those are downstream consumers of this physics layer, and are addressed as roadmap
items in §7.

**Route as modelled** (`data/route/waypoints.csv` — the route is data, not code):

| Waypoint | Chainage (km) | Elevation (m) | Type | Source |
|---|---:|---:|---|---|
| Kota | 0.0 | 271 | Dispatch | Estimated |
| Bundi | 40.0 | 268 | Waypoint | Estimated |
| Sawai Madhopur | 110.0 | 262 | Waypoint | Estimated |
| Bharatpur | 210.0 | 183 | Waypoint | Estimated |
| Mathura (crossing) | 250.0 | 174 | Crossing | Estimated |
| Faridabad | 330.0 | 198 | Waypoint | Estimated |
| Piyala | 340.0 | 200 | Waypoint | Estimated |
| Bijwasan | 360.0 | 215 | Receipt | Estimated |

Pipe: 16″ OD, 12.7 mm wall thickness (API 5L Grade B, Schedule 40), uniform along the full route;
buried at 1.2 m; internal roughness 0.045 mm (new commercial steel). Every column above is a data
file, not a code constant — extending the model upstream to Bina, or to the full MMBL network, is
an edit to that file, not a rewrite.

---

## 2. Governing Physics & Methodology

### 2.1 The coupled energy–momentum–continuity system

Four things happen to a batch simultaneously as it moves through a buried line, and three of them
are mutually coupled. Temperature sets viscosity, which sets friction; friction dissipates as
heat, which changes temperature; and temperature sets density, which sets the static head from
elevation. Solving heat transfer first and hydraulics second — the structure of the project's
original plan — is not physically valid for this reason. The simulator instead integrates one
coupled ODE system in the state vector `y = [T, P]` along chainage `x`:

```
ENERGY
  ṁ·Cp·dT/dx  =  −U(x)·π·D_o(x)·(T − T_soil(x))     [soil coupling]
               +  ṁ·f·u² / (2·D_i)                  [viscous dissipation]

MOMENTUM
  dP/dx  =  −f(Re(T))·ρ(T)·u² / (2·D_i)              [Darcy–Weisbach friction]
           −  ρ(T)·g·dz/dx                           [elevation / static head]

CONTINUITY
  ṁ  =  ρ(T,P)·u·A  =  constant   ⇒   u(x) = ṁ / (ρ(x)·A(x))
```

Elevation deliberately does not appear in the energy equation: potential energy trades reversibly
with pressure and does not heat the fluid; only the irreversible friction loss does. Viscous
dissipation is often absent from simplified pipeline thermal models — it should not be. For
diesel, `ΔT_visc ≈ 0.58 °C per 10 bar` of friction loss; a 360 km product line running
50–150 bar of friction drop self-heats by roughly **3–9 °C**, against a soil-cooling signal of
only 10–13 °C. Omitting it means any U-value later calibrated against a measured receipt
temperature would silently absorb the missing friction term into a physically meaningless soil
conductivity.

The system is integrated with `scipy.integrate.solve_ivp` (LSODA, `rtol=1e-8`), evaluated at every
route waypoint plus 1 km steps in between. A closed-form analytic solution,
`T(x) = T_soil + (T_in − T_soil)·exp(−x/L*)`, is retained as a regression anchor: in the
constant-soil, frictionless limit the marching solver must reproduce it to better than 0.1 °C,
which is how new terms were proven not to have corrupted the originally validated soil-coupling
physics (§5.1).

Three coupling paths between the energy and momentum equations — why one solver, one state vector:

```
T → μ(T) → Re, f → dP/dx        cooler oil is thicker and rubs harder
dP/dx → viscous dissipation → T  friction heats the oil it is retarding
T → ρ(T) → ρ·g·dz                density also sets the elevation head
```

### 2.2 Fluid properties & volume correction

Density and its temperature/pressure correction follow **API MPMS Chapter 11.1** (CTL, via the
commodity-group K0/K1/K2 coefficients for petrol, diesel and ATF against IS 2796 / IS 1460 /
IS 1571) and **Chapter 11.2.1** (CPL). Viscosity is fitted per product from its 20 °C and 40 °C
reference values via the Andrade equation, `ln(μ) = A + B/T`. A batch has exactly one base
density, ρ₆₀ — a property of the product, not of whichever meter measured it — and the simulator
resolves it once, from a single density measurement, then reuses it at every meter (this specific
bug, and why it matters, is documented in §5.3).

```
Mass is fixed for a given batch, so:

  V_gross(x)  =  mass / ρ(T(x), P(x))            ← breathes with temperature and pressure
  V_std(x)    =  V_gross(x) · CTL(T) · CPL(P)     ← must stay constant
```

### 2.3 Soil boundary condition

The pipe sits at 1.2 m burial, which falls inside **ERA5-Land soil layer 4 (100–289 cm)**, not
layer 3 (28–100 cm) as an earlier version of the model read. This is not a cosmetic distinction:
the shallow layer swings about 14.5 °C annually versus 8.3 °C at pipe depth, because the annual
thermal wave is damped and phase-lagged with depth — so the wrong layer both overstates the
seasonal swing *and* gets its timing wrong.

Measured at Bijwasan (28.5°N, 77.1°E) — error from reading the wrong ERA5-Land layer:

| Month | stl3 (°C) | stl4 — correct (°C) | Error of using stl3 |
|---|---:|---:|---:|
| January | 17.87 | 23.42 | −5.55 °C |
| May | 30.87 | 25.15 | +5.72 °C |

A **sign-flipping, season-correlated bias of up to ~5.7 °C** — larger than the thermal signal
being modelled. The soil profile pipeline now reads `stl4`/`swvl4` directly
(`config.py::SOIL_TEMPERATURE_VAR`), with both `stl3`/`stl4` cached so a future depth
interpolation to an arbitrary burial depth needs no re-download. Soil thermal conductivity uses
the **Johansen (1975)** model from moisture content, and the pipe-to-soil heat transfer
coefficient `U` uses radial conduction through steel and soil (Cengel, Ch. 3, buried-cylinder
solution) — both retained unchanged from the originally validated model; only the input depth was
wrong, and that was upstream of this code, in the ERA5 extraction layer.

---

## 3. Reframing: Thermal UFP as Line-Pack Volume Balance

This is the most consequential engineering finding of the project so far, and it changes what the
system should be built to deliver.

### 3.1 Per-batch thermal UFP is identically zero

Custody transfer applies API MPMS volume correction at **both** meters:
`V_std = V_observed × CTL(ρ, T)`. If a batch loses no product — no leak, no theft, no
mis-metering — then mass in equals mass out, which means standard volume in equals standard
volume out, **exactly, regardless of how much the batch cooled in transit**. Correcting the
thermal effect out is precisely what the standard is for. Phase 0 of this project confirmed this
numerically: after fixing a ρ₆₀ resolution bug (§5.3), net UFP on a mass-conserving synthetic
batch is `0.00000000 KL`, at any dispatch/receipt temperature.

A model whose deliverable is per-batch thermal UFP is therefore predicting a quantity that is zero
by construction whenever BPCL's own volume correction is applied correctly. It has, in a real
sense, no customer.

### 3.2 The real signal: line fill

The pipeline itself is inventory — **a 41,000+ KL tank that happens to be 360 km long**:

```
V_linefill = A · L = 0.1140 m² × 360,000 m ≈ 41,043 m³ = 41,043 KL
(A from the 16" OD / 12.7 mm wall inner bore actually configured in this route)
```

Monthly custody reconciliation is fundamentally an inventory balance — opening stock plus
receipts, minus closing stock and deliveries — and the line fill is part of that inventory. Its
standard volume depends on the temperature (and pressure) field along the whole route, so it
moves as the seasons turn:

| Mean line temperature shift | Line-fill standard-volume shift |
|---:|---:|
| 1 °C | 34 KL |
| 3 °C | 102 KL |
| 5 °C | **170 KL** |
| 10 °C | 341 KL |

*(∂V_std/∂T ≈ 34.1 KL/°C)*

### 3.3 Same order of magnitude as the leak-detection threshold

| | |
|---|---|
| **150 KL** | 0.1% of a representative ~150,000 KL monthly throughput — the industry leak-detection sensitivity target |
| **170 KL** | standard-volume shift from a 5°C seasonal swing in mean line temperature — the same order of magnitude |

A pipeline that simply cools 5 °C between the opening and closing gauge of a month **manufactures
170 KL of apparent loss out of pure thermodynamics**, with nothing physically missing. Left
uncorrected, this produces the classic false pattern — "winter shows a loss, summer shows a gain"
— that invites exactly the wrong kind of investigation. Pressure matters too: the CPL correction,
often dismissed as negligible at the single-batch level, is not negligible for line pack.

Line-pack compression from mean line pressure (CPL contribution):

| Mean line pressure | Line-pack compression |
|---:|---:|
| 20 bar | 59 KL |
| 35 bar | **103 KL** |
| 50 bar | 147 KL |
| 70 bar | 205 KL |

> **Reframed deliverable.** The project's value is not "predict how much a batch shrinks." It is:
> **compute the line-pack's standard volume continuously, and use its change to correctly
> compensate a monthly volume balance** — the approach **API 1130** (*Computational Pipeline
> Monitoring for Liquids*) prescribes, and what every serious CPM vendor sells. This is both the
> physically correct framing and the one most legible to a BPCL operations manager. The per-batch
> gross/thermal/net decomposition built in Phase 0 (§5.1) is kept as a useful per-batch
> diagnostic; it is simply not the headline number.

---

## 4. System Architecture

The repository is organised so that physics, data and presentation stay separated, and so the
route and product data can be edited without touching code.

```
data/route/*.csv, data/*.nc (ERA5 soil), data/*.csv (soil profile)
        │
        ▼
geo/route.py              model/soil_profile.py         model/properties.py, vcf.py
(geometry, elevation,     (Johansen k_soil(θ))           (ρ(T,P), μ(T), CTL, CPL)
 diameter(x))
        │
        ▼
model/kernel.py  ← COUPLED SOLVER (§2.1)
        │
        ▼
model/ufp.py                 dashboard/app.py                ingestion/scada_validator.py
(per-batch                   (5 views, Streamlit)            (batch data QA)
 gross/thermal/net)
```

*(current, as-built module flow — not the target end-state; see §7 for what is not yet built)*

**Dashboard.** A Streamlit application (`dashboard/app.py`) presents five views: **Thermal &
Volume** (temperature, gross and standard volume vs. distance), **Hydraulics** (pressure,
velocity, Reynolds number, friction factor), **Station Report** (tabulated state at each
waypoint), **Route** (map and elevation profile) and **Model** (the governing equations, rendered
for review). Styling follows an industrial control-room convention consistently: gold for the
product being tracked, cyan for the ground/boundary condition, blue for pressure, green for
conserved quantities and healthy status, red for alarms.

**Data ingestion.** Soil boundary conditions come from ERA5-Land reanalysis (Copernicus Climate
Data Store), either fetched live via `cdsapi` (`data/era5_fetch.py`, requires a CDS personal
token) or read from monthly GRIB archives already held locally (`data/grib_loader.py`) and
pre-compiled once into an immutable soil-profile CSV consumed at runtime — the physics kernel
itself never opens a GRIB file. A synthetic generator (`data/era5_synthetic.py`) exists strictly
as a test fixture; the architecture explicitly rejects using it as a silent runtime fallback,
since a custody system that quietly substitutes fabricated climate data when a real file is
missing is an audit liability.

---

## 5. Validation & Quality Assurance

No measured BPCL ground truth exists yet, so the simulator is validated against the conservation
laws it is built from — a model that fails to conserve energy, mass and momentum is wrong
regardless of how plausible its output plots look.

### 5.1 Test suite: 75 passing

Test classes by validation category, current suite (`tests/` + `validation/`):

| Category | Tests | What it proves |
|---|---:|---|
| VCF / CTL / CPL correctness | 8 | API MPMS 11.1/11.2.1 arithmetic against reference cases |
| Johansen soil model | 7 | k_soil(θ) behaves physically across moisture range |
| Centerline geometry | 6 + 1 regression | Geodesic sampling is unique & monotonic (catches the duplicate-point bug, §5.3) |
| June NaN / GRIB regression | 5 | The bounding-box mismatch that crashed the ODE solver cannot silently recur |
| Friction / Colebrook–Swamee–Jain | 5 | Darcy–Weisbach friction factor correctness |
| SCADA validator | 4 | Ingested batch data is range- and schema-checked |
| Receipt-temperature regression | 4 | T_receipt is always solved from physics, never a constant |
| Standard-volume invariance | 3 | The project's own headline acceptance test (§5.2) |
| Mass conservation | 3 | ρ·u·A constant to 1e-9 relative |
| Heat transfer / U-value | 3 | Radial conduction (Cengel eq. 3-38) correctness |
| Energy conservation | 3 | Enthalpy rise = soil exchange + viscous dissipation |
| Units regression | 3 | KL vs. litres never mislabelled again (§5.3) |
| Momentum closure | 2 | ΔP = friction + static head, exactly |
| Hydraulic feasibility / slack flow | 2 | Column separation is flagged, never silently returned as negative gauge pressure |
| UFP decomposition, optimizer, flow sensitivity, monotonicity, other | ≥11 | Per-batch UFP identities and remaining legacy checks |

`pytest tests/ validation/` → **75 passed, 0 failed, 0 skipped** (verified against the code as
submitted). The suite additionally pins a viscous-heating scaling law (ΔT ∝ u^2.8) and reproduces
the original validated closed-form solution to within 5×10⁻⁵ °C in the constant-soil, no-friction
limit — direct evidence that the newly added momentum coupling did not corrupt the previously
validated soil-coupling term.

### 5.2 Worked example: standard-volume invariance

Petrol, 1000 KL dispatched at 40 °C, 400 m³/hr, January soil profile, 60 bar dispatch pressure —
actual simulator output:

| Waypoint | km | T (°C) | P (bar) | V_gross (KL) | V_std (KL) |
|---|---:|---:|---:|---:|---:|
| Kota (dispatch) | 0.0 | 40.00 | 60.00 | 1000.00 | 977.7265 |
| Bundi | 40.0 | 29.43 | 54.88 | 988.53 | 977.7265 |
| Sawai Madhopur | 110.0 | 25.23 | 45.91 | 984.75 | 977.7265 |
| Bharatpur | 210.0 | 24.25 | 38.33 | 984.41 | 977.7265 |
| Faridabad | 330.0 | 23.96 | 20.97 | 985.83 | 977.7265 |
| Bijwasan (receipt) | 360.0 | 23.95 | 15.63 | 986.35 | 977.7265 |

Gross volume moves by **1.37 %** as the batch relaxes toward soil temperature and pressure bleeds
off with friction; standard volume holds at `977.7265 KL` to the sixth decimal place across every
one of the 360 evaluation points — measured drift `0.00e+00 %`. This is the physical basis for the
reframing in §3.

### 5.3 Notable defects found and fixed during development

Recorded here because they demonstrate the level of scrutiny applied, and because several
invalidated the acceptance criteria of the original project plan.

| Defect | Root cause | Resolution |
|---|---|---|
| Sample receipt temperatures physically implausible in winter | Dashboard's sample generator invented T_receipt from a bare sinusoid with zero coupling to soil or ERA5 — not, as hypothesised, a wrong ERA5 variable | Receipt temperature now always solved from the validated ODE against the monthly soil profile |
| Reported UFP of 553–1927 KL on 1000–2000 KL batches | The litres column was being read and reported as KL (10× too large); true model range was 0.46–2.28 KL | Units regression tests pin KL vs. litres explicitly |
| Phantom UFP of −0.209 KL on a mass-conserving batch | The same density measurement was iterated to ρ₆₀ separately at the dispatch and receipt temperatures, yielding two different base densities for one oil (11.6 kg/m³ apart) | `resolve_rho_60()` resolves ρ₆₀ once per batch and reuses it at every meter |
| June simulations crashed the ODE integrator ("array must not contain infs or NaNs") | June's GRIB archive was pulled with a different CDS bounding box than every other month, leaving no data east of 77.5°E — exactly where the Mathura waypoint falls | Guarded clamps that raise on non-finite input; nearest-valid-neighbour gap fill with the defect reported loudly instead of masked |
| The NaN above was disguised as a plausible value | `max(0.02, min(0.50, moisture))` silently returns 0.50 when `moisture` is NaN, since every comparison against NaN is False in Python | Explicit finiteness assertions before any clamp is applied |
| Centerline sampling emitted 6 duplicate points (367 instead of 360) | The de-duplication guard exempted index 0 of each segment, which is exactly the point already emitted as the previous segment's endpoint | Rewritten as a half-open interval; uniqueness now asserted |
| Flow-rate optimizer did not find the minimum of its own cost function | SciPy's bounded Brent minimiser never evaluates the bracket endpoints, so an optimum sitting on the velocity bound was missed | Endpoints now evaluated explicitly (module itself is recommended for removal — §7) |
| Tests reported green without testing physics | A mass-conservation test asserted `UFP_KL == V_dispatch_std − V_receipt_std` — the literal line of code that computes it, and an optimizer test called `pytest.skip()` whenever its own assertion would have failed | Repointed to actually-conserved quantities (ṁ = ρuA) and replaced the skip-on-fail pattern with a real correctness assertion, which immediately caught the optimizer bug above |

---

## 6. Data Provenance & Known Limitations

Read before any output of this system is presented as authoritative. Every item below is
disclosed in the codebase itself (route `README`, module docstrings) and is repeated here because
it directly bounds what can be claimed to BPCL today.

| Item | Status | Impact if unresolved | Action needed from BPCL |
|---|---|---|---|
| Route geometry (waypoints, elevation, chainage) | **Estimated** | ±30 m elevation error ≈ ±2.4 bar static-head error; wrong slack-flow calls | Surveyed coordinates & SRTM/Cartosat elevation along the centreline |
| Piyala–Bijwasan final 20 km diameter | **Assumed 16″ uniform** | Config previously declared this 8″, implying ~6 m/s and >120 bar — physically implausible for a mainline; likely a partial-flow spur | Confirm true diameter & any flow split at Piyala against PNGRB tariff filings / BPCL drawings |
| August soil data | **Missing** | No `AUGUST.zip` source file exists; August batches are rejected outright rather than silently scored against July | Supply the missing month's ERA5 or in-house soil temperature record |
| Route coverage north of Bharatpur to Bina | **Not modelled** | Current ERA5 bounding box (25–29°N, 75.5–78°E) does not reach Bina (24.19°N, 78.20°E) | None required yet — a data change (fresh CDS pull + route CSV row), not a code change, when BPCL wants Bina included |
| Soil heat-transfer coefficient (U) | **Uncalibrated, ±30%** | Johansen + burial correlations are textbook estimates; backfill, moisture migration and thermal contact resistance are not directly knowable | Measured receipt temperature time series to fit against (`U_scale` is already exposed as the calibration knob) |
| Pipe roughness, wall thickness, burial depth | **Nominal / Schedule 40 assumption** | Affects friction factor and U-value precision | As-built specifications if they differ from nominal API 5L Grade B Sch. 40 |
| Custody / SCADA data | **Not yet supplied** | No calibration or measured-vs-modelled comparison is possible | Historical station T/P/Q, batch schedule, tank gauges — see §7 |

---

## 7. Roadmap — What Remains Before Production Use

Ordered by physical dependency: each phase needs the one before it. Phases 0–3 are complete and
are what this report documents; 4–9 are future work, listed here so the scope of
"physics-validated demonstrator" versus "operational tool" is unambiguous.

| Phase | Content | Status |
|---:|---|---|
| 0 | Bug fixes, ρ₆₀ resolution, regression pins | ✅ Complete |
| 1 | Soil depth correction (stl4/swvl4, depth interpolation) | ✅ Complete |
| 2 | Route as data, DEM elevation, diameter verification | ✅ Complete (elevation estimated — §6) |
| 3 | Coupled kernel: energy + momentum + continuity, viscous dissipation, elevation | ✅ Complete |
| 4 | **Line fill & line pack:** FIFO batch tracker; V_std,linefill(t) with CTL & CPL — the actual deliverable per §3 | ⏳ Not started |
| 5 | **Volume balance + uncertainty:** API 1130 balance, propagated σ | ⏳ Not started |
| 6 | **Calibration:** fit U_scale, effective roughness, meter bias against SCADA, with covariance | ⏳ Not started |
| 7 | **Detection:** CUSUM/SPRT on the normalised residual, with leak location from the pressure gradient | ⏳ Not started |
| 8 | **Transmix:** Austin–Palfrey interface-growth model, reported as a separate loss line item | ⏳ Not started |
| 9 | Custody data adapter, dashboard re-page, signed audit report, one-command demo | ⏳ Not started |

**Two deliberate deletions from the original plan.** An Isolation-Forest anomaly detector was
planned for Phase 6 and is not being built: trained on synthetic data, it would measure how well
it reverse-engineers its own random generator, not the pipeline, and "the isolation forest's
average path length was short" does not survive a custody audit. CUSUM/SPRT on a calibrated,
uncertainty-quantified residual (Phase 7) gives the same alarm capability with a provable
false-alarm/detection-time trade-off and a one-sentence explanation for every alert. The flow-rate
optimizer (`model/optimizer.py`, still present in the repository) prices thermal shrinkage as a
financial loss and minimises it — but §3.1 establishes that quantity is an accounting artifact,
not lost product, so the optimizer is minimising a cost that does not exist. The real optimisation
opportunity — pump scheduling under time-of-day electricity tariffs, subject to MAOP and
slack-flow constraints — is a genuine and different problem, deferred until the accounting layer
above is in place.

**What BPCL supplying real data would unlock:**

- Surveyed route coordinates, elevation and as-built pipe specification — removes the single
  largest source of hydraulic uncertainty (§6).
- Historical SCADA (station temperature, pressure, flow) — enables Phase 6 calibration and turns
  U_scale from a placeholder (1.0) into a fitted, bounded parameter.
- Tank gauge and batch-schedule records — the direct inputs Phase 4–5 need to compute a real,
  reconcilable line-pack balance.
- Confirmation of the Piyala–Bijwasan diameter/flow-split — resolves the one open
  physical-plausibility question in the current route data.

---

## 8. Repository & Reproducibility

Source code, tests and documentation are published at
[github.com/GhostRider2023/BPCL-UFP-PROJECT-](https://github.com/GhostRider2023/BPCL-UFP-PROJECT-).
The two large ERA5 NetCDF caches (≈105–115 MB) are excluded from the repository and regenerated
locally rather than committed, per the notes in the project README.

```bash
pip install -r requirements.txt
pytest tests/ validation/          # 75 passed
streamlit run dashboard/app.py     # interactive simulator
```

Core dependencies: `numpy`, `scipy`, `pandas`, `xarray`, `netCDF4`, `cfgrib`/`eccodes`, `cdsapi`,
`geographiclib`, `streamlit`, `plotly`, `pytest`. Python 3.12.

---

## Appendix A: Symbols & Units

| Symbol | Meaning | Unit |
|---|---|---|
| T(x) | Product temperature at chainage x | °C |
| P(x) | Product gauge pressure at chainage x | bar / Pa |
| ρ(T,P) | Product density | kg/m³ |
| ρ₆₀ | Base density at 15 °C (60°F), resolved once per batch | kg/m³ |
| μ(T) | Dynamic viscosity (Andrade fit) | Pa·s (reported cP) |
| CTL | Correction for Temperature on Liquids (API MPMS 11.1) | dimensionless |
| CPL | Correction for Pressure on Liquids (API MPMS 11.2.1) | dimensionless |
| U(x) | Overall pipe-to-soil heat transfer coefficient | W/(m²·K) |
| k_soil | Soil thermal conductivity (Johansen 1975) | W/(m·K) |
| L* | Thermal relaxation length, ṁ·Cp / (U·π·D) | km |
| ṁ | Mass flow rate (conserved along x) | kg/s |
| Re, f | Reynolds number, Darcy friction factor (Colebrook / Swamee–Jain) | dimensionless |
| V_gross | Observed volume at local T, P | KL |
| V_std | Standard volume at 15 °C, 1 atm — the mass proxy | KL |
| UFP | Unaccounted-For Product | KL / % |

## Appendix B: References

- American Petroleum Institute, *Manual of Petroleum Measurement Standards*, Ch. 11.1 (Volume
  Correction Factors — CTL) and Ch. 11.2.1 (CPL).
- American Petroleum Institute, *API 1130 — Computational Pipeline Monitoring for Liquids.*
- Cengel, Y., *Heat Transfer: A Practical Approach*, Ch. 3 (buried cylinder conduction).
- Colebrook, C.F. (1939); Swamee, P.K. & Jain, A.K. (1976); Moody, L.F. (1944) — pipe friction
  factor correlations.
- Johansen, O. (1975), *Thermal Conductivity of Soils*, CRREL Draft Translation 637 — soil
  conductivity model.
- Bird, R.B., Stewart, W.E., Lightfoot, E.N., *Transport Phenomena* — viscous dissipation.
- ECMWF, *ERA5-Land* reanalysis (Copernicus Climate Data Store) — soil temperature and moisture
  boundary conditions.
- IS:2796 (Motor Spirit), IS:1460 (Diesel), IS:1571 (Aviation Turbine Fuel) — Indian product
  standards.
- OISD-141, Oil Industry Safety Directorate — pipeline burial depth practice.
- PNGRB tariff filings; BPCL MMBL pipeline route documentation — route and geometry
  cross-reference (pending surveyed confirmation, §6).
