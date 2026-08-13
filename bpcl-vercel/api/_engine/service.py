"""
Service layer — the simulator as JSON
======================================

Everything the browser can ask for, as plain dicts. The HTTP shells around this
(`api/simulate.py`, `api/meta.py` on Vercel; `tools/devserver.py` locally) do
nothing but parse a request, call one function here, and serialise the result —
so the two environments cannot drift apart in behaviour.

Nothing in this module computes physics. It arranges inputs for `model.kernel`
and reshapes its output; the DataFrame that comes back from `simulate()` is
still the product of the simulator, and this just puts it on the wire.

Serialisation rules
-------------------
The state table goes out **columnar** — one array per column rather than one
object per row. At 620 chainages and ~20 columns that is roughly a third of the
JSON, and it is the shape the charts want anyway.

`NaN` is not valid JSON, and `T_air_C` is NaN at every chainage where no air
measurement exists — which is almost all of them, by design. NaN is therefore
encoded as `null`, and the client renders `null` as "no data" rather than as a
number. A missing air reading must never arrive at the browser as a 0.
"""

from __future__ import annotations

import math
import os
import sys
import time
import warnings
from typing import Any, Dict, List, Optional

import numpy as np

ENGINE_ROOT = os.path.dirname(os.path.abspath(__file__))
if ENGINE_ROOT not in sys.path:
    sys.path.insert(0, ENGINE_ROOT)

from config import (  # noqa: E402
    AIR_CACHE_TTL_S,
    AIR_STATIONS,
    BURIAL_DEPTH_M,
    FLOW_SPLITS,
    MIN_SUCTION_BAR,
    PRODUCTS,
    PUMP_STATIONS,
    SOIL_TEMPERATURE_VAR,
    VELOCITY_SLIDER_CAP_MS,
)
from geo.route import Route  # noqa: E402
from model.kernel import HydraulicFeasibilityWarning, SimulationInputs, simulate  # noqa: E402
from model.soil_profile import _load_soil_csv, available_months  # noqa: E402

MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]

# Columns sent to the browser. Deliberately a subset of the ~31 the kernel
# produces: these are the ones something on screen reads. The CSV export runs
# server-side off the full frame, so nothing is lost by trimming here.
PROFILE_COLUMNS = [
    "km",
    "lat",
    "lon",
    "elevation_m",
    "T_C",
    "T_env_C",
    "T_soil_C",
    "T_air_C",
    "P_bar",
    "P_vapour_bar",
    "rho_kgm3",
    "mu_cP",
    "velocity_ms",
    "Re",
    "friction_factor",
    "V_gross_KL",
    "V_std_KL",
    "U_Wm2K",
    "L_star_km",
    "slack_flow",
]

# Extra columns that only appear in the per-station table.
STATION_COLUMNS = PROFILE_COLUMNS + ["waypoint_name", "T_env_basis", "CTL", "CPL", "k_soil_WmK"]


# ═══════════════════════════════════════════════════════════════════
# Process-lifetime caches
#
# A serverless function is not a fresh process per request: Vercel reuses a warm
# instance for as long as traffic keeps it alive. Route geometry and the soil
# table are immutable inputs, so loading them once per instance turns a ~90 ms
# cost into a once-per-cold-start cost.
# ═══════════════════════════════════════════════════════════════════

_ROUTE: Optional[Route] = None
_SOIL = None
_AIR_CACHE: Dict[str, Any] = {"snapshot": None, "at": 0.0}


def _engine():
    global _ROUTE, _SOIL
    if _ROUTE is None or _SOIL is None:
        _ROUTE = Route.from_csv()
        _SOIL = _load_soil_csv()
    return _ROUTE, _SOIL


def _clean(value):
    """JSON-safe scalar. NaN and infinity become null, NumPy types become Python.

    NaN is load-bearing in this model: `T_air_C` is NaN wherever no air
    measurement exists, which is every chainage except the two terminals. It has
    to reach the client as `null` and be rendered as absence — never coerced to
    a number, which would put a fabricated air temperature on screen.
    """
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        v = float(value)
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.ndarray, list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if value != value:  # NaN that escaped the checks above
        return None
    return value


# ═══════════════════════════════════════════════════════════════════
# Air temperature
# ═══════════════════════════════════════════════════════════════════


def _air_snapshot(force: bool = False):
    """Current air at the two terminals, cached for `AIR_CACHE_TTL_S`.

    The cache is per warm instance, so a burst of parameter changes issues one
    weather request rather than one per keystroke. A cold instance simply
    fetches. `force=True` is the refresh control.

    A failure here is never fatal and never filled in: the snapshot carries its
    own failures, `build_environment_profile` falls back to soil alone, and the
    UI says so.
    """
    now = time.time()
    cached = _AIR_CACHE["snapshot"]
    if not force and cached is not None and (now - _AIR_CACHE["at"]) < AIR_CACHE_TTL_S:
        return cached

    from data.air_temperature import fetch_current_air_temperatures

    try:
        snap = fetch_current_air_temperatures()
    except Exception as exc:  # network, DNS, provider outage — all non-fatal
        if cached is not None:
            return cached
        raise RuntimeError(f"air temperature providers unreachable: {exc}") from exc

    _AIR_CACHE["snapshot"] = snap
    _AIR_CACHE["at"] = now
    return snap


def _air_payload(snapshot) -> Dict[str, Any]:
    from data.air_temperature import describe_provider

    return {
        "provider": snapshot.provider,
        "providerLabel": describe_provider(snapshot),
        "ageSeconds": round(snapshot.age_s(), 1),
        "observedAt": snapshot.observed_at,
        "complete": bool(snapshot.complete),
        "anyData": bool(snapshot.any_data),
        "stations": [
            {
                "name": name,
                "lat": lat,
                "lon": lon,
                "tempC": _clean(snapshot.temperatures.get(name)),
                "failure": snapshot.failures.get(name),
            }
            for name, (lat, lon) in AIR_STATIONS.items()
        ],
        "failures": dict(snapshot.failures),
    }


def get_air(force: bool = False) -> Dict[str, Any]:
    """GET /api/air — the live reading on its own, for the refresh control."""
    try:
        return {"ok": True, "air": _air_payload(_air_snapshot(force=force))}
    except RuntimeError as exc:
        return {"ok": False, "air": None, "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════
# Meta — everything the console needs to render before the first run
# ═══════════════════════════════════════════════════════════════════


def get_meta() -> Dict[str, Any]:
    route, _ = _engine()
    months = available_months()

    a_main = route.area(0.0)
    a_tail = route.area(route.length_km)

    # Fraction of the dispatched batch still in the line after every delivery.
    # With FLOW_SPLITS empty this is 1.0 and the tail carries the whole batch —
    # which is exactly why the tail, not the mainline, sets the ceiling.
    tail_fraction = 1.0
    for s in FLOW_SPLITS:
        tail_fraction *= 1.0 - s.delivered_fraction

    # Flow-slider ceiling: the flow at which the FIRST bore to reach
    # VELOCITY_SLIDER_CAP_MS gets there. Derived from the geometry rather than
    # hardcoded, so it tracks any change to the route CSV.
    step = 5.0
    flow_at_cap = min(
        VELOCITY_SLIDER_CAP_MS * a_main * 3600.0,
        VELOCITY_SLIDER_CAP_MS * a_tail * 3600.0 / tail_fraction,
    )
    flow_max = step * math.ceil(flow_at_cap / step)

    def nearest_month(target: int) -> int:
        return target if target in months else min(months, key=lambda m: abs(m - target))

    first_product = next(iter(PRODUCTS))
    defaults = {
        "product": first_product,
        "densityKgm3": float(PRODUCTS[first_product].density_ref_kgm3),
        "volumeKL": 1000.0,
        "tempC": 40.0,
        "pressureBar": 70.0,
        "flowM3hr": 349.0,  # 3.00 m/s in the 8" tail — the binding constraint
        "month": nearest_month(1),
        "viscousHeating": True,
        "elevation": True,
        "pressureCorrection": True,
        "uScale": 1.0,
    }

    # Named dispatch scenarios. A preset is a starting point, not a mode: it
    # pushes values into the console and every one stays editable afterwards.
    presets = [
        {"name": "Baseline", "values": {}},
        {
            "name": "Peak summer",
            "values": {"month": nearest_month(5), "tempC": 45.0, "flowM3hr": 380.0},
        },
        {
            "name": "Winter dispatch",
            "values": {"month": nearest_month(1), "tempC": 28.0, "flowM3hr": 280.0},
        },
        {"name": "Max throughput", "values": {"flowM3hr": 430.0, "pressureBar": 90.0}},
        {
            "name": "Cold + slow",
            "values": {"month": nearest_month(1), "tempC": 22.0, "flowM3hr": 150.0},
        },
        {
            # The 8" tail at the 4 m/s ceiling. Petrol just survives this; diesel
            # does not, being ~6x more viscous — so the preset switches product
            # too, or it would not reliably show the guard firing. That contrast
            # is the point: feasibility is a property of the product, not only
            # of the flow rate.
            "name": "Overdrive → slack",
            "values": {
                "flowM3hr": 465.0,
                "pressureBar": 70.0,
                "product": "diesel",
                "densityKgm3": float(PRODUCTS["diesel"].density_ref_kgm3),
            },
        },
    ]

    return {
        "ok": True,
        "route": {
            "lengthKm": route.length_km,
            "linefillM3": route.linefill_m3(),
            "originName": route.waypoints[0].name,
            "terminusName": route.waypoints[-1].name,
            "areaMainM2": a_main,
            "areaTailM2": a_tail,
            "tailFraction": tail_fraction,
            "waypoints": [
                {
                    "id": w.waypoint_id,
                    "name": w.name,
                    "lat": w.lat,
                    "lon": w.lon,
                    "km": w.chainage_km,
                    "elevationM": w.elevation_m,
                    "odInch": w.od_inch,
                    "wallMm": w.wall_thickness_mm,
                    "roughnessMm": w.roughness_mm,
                    "burialM": w.burial_depth_m,
                    "type": w.station_type,
                    "source": w.source,
                }
                for w in route.waypoints
            ],
        },
        "products": [
            {
                "key": key,
                "name": p.name,
                "standard": p.is_standard,
                "densityRef": p.density_ref_kgm3,
                "densityMin": p.density_min_kgm3,
                "densityMax": p.density_max_kgm3,
                "cpJkgK": p.cp_jkgk,
                "mu20": p.viscosity_20c_mPas,
                "mu40": p.viscosity_40c_mPas,
            }
            for key, p in PRODUCTS.items()
        ],
        "months": [{"value": m, "name": MONTHS[m - 1]} for m in months],
        "pumpStations": [
            {"name": s.name, "km": s.km, "dischargeBar": s.discharge_bar} for s in PUMP_STATIONS
        ],
        "flowSplits": [
            {"name": s.name, "km": s.km, "deliveredFraction": s.delivered_fraction}
            for s in FLOW_SPLITS
        ],
        "terminals": list(AIR_STATIONS),
        "limits": {
            "flowMinM3hr": 25.0,
            "flowMaxM3hr": flow_max,
            "flowStepM3hr": step,
            "velocityCapMs": VELOCITY_SLIDER_CAP_MS,
            "tempMinC": 10.0,
            "tempMaxC": 55.0,
            "pressureMinBar": 10.0,
            "pressureMaxBar": 100.0,
            "volumeMinKL": 100.0,
            "volumeMaxKL": 5000.0,
            "densityMinKgm3": 650.0,
            "densityMaxKgm3": 950.0,
            "uScaleMin": 0.5,
            "uScaleMax": 2.0,
            "minSuctionBar": MIN_SUCTION_BAR,
        },
        "constants": {
            "burialDepthM": BURIAL_DEPTH_M,
            "soilVar": SOIL_TEMPERATURE_VAR,
            "baseTempC": 15.0,
        },
        "defaults": defaults,
        "presets": presets,
    }


# ═══════════════════════════════════════════════════════════════════
# Simulate
# ═══════════════════════════════════════════════════════════════════


def _as_float(payload: Dict[str, Any], key: str, default: float, lo: float, hi: float) -> float:
    """Read one number, clamped to its documented range.

    The browser is not trusted to have enforced the slider bounds. Clamping
    rather than rejecting keeps a stale client usable, and every bound here is
    the same one `get_meta()` advertises.
    """
    try:
        v = float(payload.get(key, default))
    except (TypeError, ValueError):
        return default
    if math.isnan(v) or math.isinf(v):
        return default
    return min(max(v, lo), hi)


def run_simulation(payload: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/simulate — one run, and everything on screen that depends on it."""
    route, soil_all = _engine()
    limits = get_meta()["limits"]

    product_key = str(payload.get("product", next(iter(PRODUCTS)))).lower()
    if product_key not in PRODUCTS:
        return {"ok": False, "error": f"unknown product {product_key!r}"}

    months = available_months()
    try:
        month = int(payload.get("month", months[0]))
    except (TypeError, ValueError):
        month = months[0]
    if month not in months:
        return {
            "ok": False,
            "error": f"month {month} is not in the soil table (have {months})",
        }

    inputs = SimulationInputs(
        product=product_key,
        T_dispatch_C=_as_float(payload, "tempC", 40.0, limits["tempMinC"], limits["tempMaxC"]),
        V_dispatch_KL=_as_float(
            payload, "volumeKL", 1000.0, limits["volumeMinKL"], limits["volumeMaxKL"]
        ),
        flow_rate_m3hr=_as_float(
            payload, "flowM3hr", 349.0, limits["flowMinM3hr"], limits["flowMaxM3hr"]
        ),
        month=month,
        P_dispatch_bar=_as_float(
            payload, "pressureBar", 70.0, limits["pressureMinBar"], limits["pressureMaxBar"]
        ),
        density_kgm3=_as_float(
            payload,
            "densityKgm3",
            float(PRODUCTS[product_key].density_ref_kgm3),
            limits["densityMinKgm3"],
            limits["densityMaxKgm3"],
        ),
        include_viscous_heating=bool(payload.get("viscousHeating", True)),
        include_elevation=bool(payload.get("elevation", True)),
        include_pressure_correction=bool(payload.get("pressureCorrection", True)),
    )
    u_scale = _as_float(payload, "uScale", 1.0, limits["uScaleMin"], limits["uScaleMax"])

    # ── Boundary condition ──────────────────────────────────────────
    soil = (
        soil_all[soil_all["month"] == month]
        .rename(columns={"waypoint_km": "km"})
        .reset_index(drop=True)
    )

    air_payload: Optional[Dict[str, Any]] = None
    air_error: Optional[str] = None
    if payload.get("useAir", True):
        from data.air_temperature import build_environment_profile

        try:
            snapshot = _air_snapshot(force=bool(payload.get("refreshAir", False)))
            soil = build_environment_profile(soil, snapshot, T_fuel_C=inputs.T_dispatch_C)
            air_payload = _air_payload(snapshot)
        except RuntimeError as exc:
            # Soil alone, and said so. No air temperature is invented to fill it.
            air_error = str(exc)

    # ── Run ─────────────────────────────────────────────────────────
    t0 = time.perf_counter()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        df = simulate(inputs, route, soil, U_scale=u_scale)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    feasibility_warnings = [
        str(w.message) for w in caught if issubclass(w.category, HydraulicFeasibilityWarning)
    ]

    # ── Reshape ─────────────────────────────────────────────────────
    profile = {}
    for col in PROFILE_COLUMNS:
        if col not in df.columns:
            continue
        if col == "slack_flow":
            profile[col] = [bool(v) for v in df[col]]
        else:
            profile[col] = _clean(df[col].to_numpy())

    wp = df[df["waypoint_name"].notna()]
    stations = [
        {c: _clean(row[c]) for c in STATION_COLUMNS if c in df.columns}
        for _, row in wp.iterrows()
    ]

    first, last = df.iloc[0], df.iloc[-1]
    d_temp = float(last.T_C - first.T_C)
    d_press = float(last.P_bar - first.P_bar)
    d_gross = float(last.V_gross_KL - first.V_gross_KL)
    v_std_drift_pct = abs(float(last.V_std_KL - first.V_std_KL)) / float(first.V_std_KL) * 100.0

    a_main = route.area(0.0)
    a_tail = route.area(route.length_km)
    tail_fraction = 1.0
    for s in FLOW_SPLITS:
        tail_fraction *= 1.0 - s.delivered_fraction

    ablated = [
        label
        for label, on in (
            ("viscous heating", inputs.include_viscous_heating),
            ("elevation", inputs.include_elevation),
            ("CPL", inputs.include_pressure_correction),
        )
        if not on
    ]

    attrs = df.attrs
    return {
        "ok": True,
        "elapsedMs": round(elapsed_ms, 1),
        "inputs": {
            "product": product_key,
            "productName": PRODUCTS[product_key].name,
            "productStandard": PRODUCTS[product_key].is_standard,
            "tempC": inputs.T_dispatch_C,
            "volumeKL": inputs.V_dispatch_KL,
            "flowM3hr": inputs.flow_rate_m3hr,
            "pressureBar": inputs.P_dispatch_bar,
            "densityKgm3": inputs.density_kgm3,
            "month": month,
            "monthName": MONTHS[month - 1],
            "uScale": u_scale,
            "viscousHeating": inputs.include_viscous_heating,
            "elevation": inputs.include_elevation,
            "pressureCorrection": inputs.include_pressure_correction,
            "ablated": ablated,
            "velocityMainMs": (inputs.flow_rate_m3hr / 3600.0) / a_main,
            "velocityTailMs": (inputs.flow_rate_m3hr * tail_fraction / 3600.0) / a_tail,
            "massTonnes": inputs.V_dispatch_KL * inputs.density_kgm3 / 1000.0,
        },
        "profile": profile,
        "stations": stations,
        "kpi": {
            "receiptTempC": _clean(last.T_C),
            "deltaTempC": d_temp,
            "receiptEnvC": _clean(last.T_env_C),
            "receiptSoilC": _clean(last.T_soil_C),
            "receiptAirC": _clean(last.T_air_C),
            "receiptEnvBasis": str(last.T_env_basis) if "T_env_basis" in df.columns else "",
            "receiptPressureBar": _clean(last.P_bar),
            "deltaPressureBar": d_press,
            "grossVolumeKL": _clean(last.V_gross_KL),
            "deltaGrossKL": d_gross,
            "deltaGrossPct": d_gross / float(first.V_gross_KL) * 100.0,
            "stdVolumeKL": _clean(last.V_std_KL),
            "stdVolumeDriftPct": v_std_drift_pct,
            # Line fill / volumetric rate — the real two-diameter line fill, not
            # length x mainline area. The 8" tail holds far less per km.
            "transitHours": route.linefill_m3() / (inputs.flow_rate_m3hr / 3600.0) / 3600.0,
            "meanVelocityMs": float(df.velocity_ms.mean()),
            "minPressureBar": _clean(attrs.get("P_min_bar")),
        },
        "physics": {
            "feasible": bool(attrs.get("hydraulically_feasible", True)),
            "slackOnsetKm": _clean(attrs.get("slack_flow_onset_km")),
            "warnings": feasibility_warnings,
            "rho60Kgm3": _clean(attrs.get("rho_60_kgm3")),
            "massKg": _clean(attrs.get("mass_kg")),
            "mDotKgs": _clean(attrs.get("m_dot_kgs")),
            "vStdDispatchKL": _clean(attrs.get("V_std_KL")),
            "vStdReceiptKL": _clean(attrs.get("V_std_receipt_KL")),
            "vStdDeliveredKL": _clean(attrs.get("V_std_delivered_KL")),
            "vStdBalanceErrorKL": _clean(attrs.get("V_std_balance_error_KL")),
            "pumpStations": _clean(attrs.get("pump_stations", [])),
            "cavitatingStations": list(attrs.get("cavitating_stations", [])),
            "flowSplits": _clean(attrs.get("flow_splits", [])),
            "airTemperatureUsed": bool(attrs.get("air_temperature_used", False)),
            "stationsWithAir": int(attrs.get("n_stations_with_air", 0)),
        },
        "air": air_payload,
        "airError": air_error,
    }


def simulation_csv(payload: Dict[str, Any]) -> str:
    """The full state table at every kilometre, as CSV.

    Re-runs rather than caching the frame from a previous call: a serverless
    instance that served the run the user is looking at may not be the one that
    serves the download, so the request has to carry its own inputs and be
    reproducible from them. It is — the simulator is deterministic given its
    inputs and the air snapshot, and the download states the same numbers the
    screen does.
    """
    route, soil_all = _engine()
    limits = get_meta()["limits"]

    product_key = str(payload.get("product", next(iter(PRODUCTS)))).lower()
    if product_key not in PRODUCTS:
        raise ValueError(f"unknown product {product_key!r}")
    month = int(payload.get("month", available_months()[0]))

    inputs = SimulationInputs(
        product=product_key,
        T_dispatch_C=_as_float(payload, "tempC", 40.0, limits["tempMinC"], limits["tempMaxC"]),
        V_dispatch_KL=_as_float(
            payload, "volumeKL", 1000.0, limits["volumeMinKL"], limits["volumeMaxKL"]
        ),
        flow_rate_m3hr=_as_float(
            payload, "flowM3hr", 349.0, limits["flowMinM3hr"], limits["flowMaxM3hr"]
        ),
        month=month,
        P_dispatch_bar=_as_float(
            payload, "pressureBar", 70.0, limits["pressureMinBar"], limits["pressureMaxBar"]
        ),
        density_kgm3=_as_float(
            payload,
            "densityKgm3",
            float(PRODUCTS[product_key].density_ref_kgm3),
            limits["densityMinKgm3"],
            limits["densityMaxKgm3"],
        ),
        include_viscous_heating=bool(payload.get("viscousHeating", True)),
        include_elevation=bool(payload.get("elevation", True)),
        include_pressure_correction=bool(payload.get("pressureCorrection", True)),
    )

    soil = (
        soil_all[soil_all["month"] == month]
        .rename(columns={"waypoint_km": "km"})
        .reset_index(drop=True)
    )
    if payload.get("useAir", True):
        from data.air_temperature import build_environment_profile

        try:
            soil = build_environment_profile(
                soil, _air_snapshot(), T_fuel_C=inputs.T_dispatch_C
            )
        except RuntimeError:
            pass  # soil alone; the CSV's T_air_C column is blank and says so

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = simulate(
            inputs,
            route,
            soil,
            U_scale=_as_float(payload, "uScale", 1.0, limits["uScaleMin"], limits["uScaleMax"]),
        )
    return df.to_csv(index=False)


def csv_filename(payload: Dict[str, Any]) -> str:
    product = str(payload.get("product", "product")).lower()
    month = int(payload.get("month", 1))
    temp = float(payload.get("tempC", 40.0))
    flow = float(payload.get("flowM3hr", 349.0))
    return f"mmbl_{product}_{MONTHS[month - 1].lower()}_{temp:.0f}C_{flow:.0f}m3hr.csv"


__all__: List[str] = [
    "get_meta",
    "get_air",
    "run_simulation",
    "simulation_csv",
    "csv_filename",
]
