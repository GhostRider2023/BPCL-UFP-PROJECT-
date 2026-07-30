"""
MMBL Pipeline Thermal-Hydraulic Simulator — Control Dashboard
==============================================================

Simulates how a petroleum batch evolves — thermally and hydraulically — as it
travels through the buried MMBL line, and presents the full state at every
kilometre.

This is a SIMULATOR. It is not a leak detector and not an accounting
reconciliation engine. It answers one question: given a product dispatched at a
temperature, volume, flow rate and pressure, in a given month — what is its
physical state at every point on the route?

Launch:  streamlit run dashboard/app.py
"""

import math
import os
import sys
import warnings

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import (
    AIR_CACHE_TTL_S,
    AIR_STATIONS,
    BURIAL_DEPTH_M,
    FLOW_SPLITS,
    PRODUCTS,
    SOIL_TEMPERATURE_VAR,
    VELOCITY_SLIDER_CAP_MS,
)
from dashboard.schematic import pipeline_schematic
from dashboard.theme import (
    AMBER,
    BLUE,
    CSS,
    CYAN,
    GOLD,
    GREEN,
    GRID,
    MUTED,
    PLOT_LAYOUT,
    RED,
    VIOLET,
    callout,
    kpi,
    masthead,
    rail_label,
    readout,
    section,
)
from data.air_temperature import (
    build_environment_profile,
    describe_provider,
    fetch_current_air_temperatures,
)
from geo.route import Route
from model.kernel import HydraulicFeasibilityWarning, SimulationInputs, simulate
from model.soil_profile import _load_soil_csv, available_months

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

st.set_page_config(
    page_title="MMBL Thermal-Hydraulic Simulator",
    page_icon="🛢️",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.markdown(CSS, unsafe_allow_html=True)


@st.cache_resource
def load_inputs():
    """Route geometry and the precomputed soil boundary condition.

    Both are DATA. The simulator never opens a GRIB file at runtime, and never
    falls back to synthetic climate data — a missing soil table is an error, not
    something to paper over.
    """
    return Route.from_csv(), _load_soil_csv()


@st.cache_data(ttl=AIR_CACHE_TTL_S, show_spinner=False)
def load_air_temperatures(_nonce: int = 0):
    """Current air temperature at Bina and Bijwasan — the two terminals.

    Those are the only stations whose air enters the boundary condition, so
    they are the only ones queried. Cached for AIR_CACHE_TTL_S so a Streamlit
    rerun — which fires on every widget change — does not re-issue the request.
    `_nonce` exists only so the "refresh" control can bust the cache on demand.

    Uses OpenWeather when an API key is configured and Open-Meteo (no key
    needed) when it is not — see data/air_temperature.py. Either way the value
    is a current real-time reading; never a modelled or seasonal air
    temperature. A station that fails is carried through as a failure and
    shown as one.
    """
    return fetch_current_air_temperatures()


try:
    route, soil_all = load_inputs()
except Exception as exc:
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(
        callout(
            "bad",
            f"<b>Cannot start.</b> Route or soil profile "
            f"failed to load: <code>{exc}</code><br>"
            f"Rebuild with "
            f"<code>python data/generate_soil_csv.py</code>.",
        ),
        unsafe_allow_html=True,
    )
    st.stop()


# ═══════════════════════════════════════════════════════════════════
# CONSOLE RAIL — the input model
#
# No sidebar. The eleven simulation parameters live in a horizontal
# instrument cluster: each slot READS BACK the value currently loaded into
# the model, and opens into the controls that set it. A sidebar shows you
# widgets and never shows you state; a control panel shows you state and
# hands you a control only when you reach for one. This is the second kind.
#
# The practical payoff: the simulator's entire input vector stays legible on
# the same screen as its output, which is what you need when comparing runs.
# ═══════════════════════════════════════════════════════════════════

MONTH_OPTS = available_months()
FIRST_PRODUCT = next(iter(PRODUCTS))

# The route has two bores, so "velocity" is two numbers. Both are reported.
A_MAIN = route.area(0.0)  # 18" mainline, Bina -> Piyala
A_TAIL = route.area(route.length_km)  # 8" tail, Piyala -> Bijwasan
UNIFORM_BORE = abs(A_MAIN - A_TAIL) < 1e-12

# Fraction of the dispatched batch still in the line after every delivery. The
# tail carries only this much, so its velocity is NOT flow/A_TAIL.
TAIL_FRACTION = 1.0
for _s in FLOW_SPLITS:
    TAIL_FRACTION *= 1.0 - _s.delivered_fraction

# Flow-slider ceiling: the flow at which the FIRST segment reaches
# VELOCITY_SLIDER_CAP_MS. With the whole batch passing through both bores that
# is the 8" tail, which runs 4.47x faster than the mainline — so the ceiling is
# set by the tail even though the mainline is nowhere near its own limit.
# Derived rather than hardcoded, so it tracks the geometry and any future
# delivery. Rounded UP to the slider's step so the ceiling genuinely reaches the
# cap rather than landing just short of it.
FLOW_STEP_M3HR = 5.0
_flow_at_cap = min(
    VELOCITY_SLIDER_CAP_MS * A_MAIN * 3600.0,  # mainline carries the full flow
    VELOCITY_SLIDER_CAP_MS * A_TAIL * 3600.0 / TAIL_FRACTION,  # tail carries less
)
FLOW_MAX_M3HR = FLOW_STEP_M3HR * math.ceil(_flow_at_cap / FLOW_STEP_M3HR)


def _nearest_month(target: int) -> int:
    """Presets ask for a month; the soil table decides which ones exist."""
    return target if target in MONTH_OPTS else min(MONTH_OPTS, key=lambda m: abs(m - target))


# Flow-rate defaults are sized for the documented MMBPL geometry: an 18-inch
# mainline (Bina -> Piyala) with an 8-inch tail (Piyala -> Bijwasan), plus the
# booster stations in config.PUMP_STATIONS.
#
# The batch runs Bina -> Bijwasan INTACT: nothing is delivered en route, because
# this is a custody-transfer model and a mid-route off-take would sit inside the
# dispatch-vs-receipt comparison it exists to make. See config.FLOW_SPLITS.
#
# One flow therefore passes through both bores, and the 8" tail is the binding
# constraint — its area is 4.47x smaller, so it runs 4.47x faster:
#
#     349 m3/hr  ->  0.67 m/s mainline, 3.00 m/s tail   <- tail at design speed
#     500 m3/hr  ->  0.96 m/s mainline, 4.30 m/s tail   <- goes slack
#
# The 18" mainline is correspondingly under-utilised. That is what a trunk line
# narrowing for its final approach into a city terminal actually does; it is not
# a modelling artefact, and the answer is not to invent a delivery.
#
# These are INPUT defaults chosen to land inside the line's feasible envelope —
# no output has been retuned, and every value stays editable.
DEFAULTS = {
    "w_product": FIRST_PRODUCT,
    "w_rho": float(PRODUCTS[FIRST_PRODUCT].density_ref_kgm3),
    "w_V": 1000.0,
    "w_T": 40.0,
    "w_P": 70.0,
    "w_Q": 349.0,  # 3.00 m/s in the 8" tail — the binding constraint
    "w_month": _nearest_month(1),
    "w_visc": True,
    "w_elev": True,
    "w_cpl": True,
    "w_U": 1.0,
}
for _k, _v in DEFAULTS.items():
    st.session_state.setdefault(_k, _v)

# Named dispatch scenarios. A preset is a starting point, not a mode: it pushes
# values into the console and every one of them stays editable afterwards. The
# highlighted chip therefore means "last loaded", not "currently in".
PRESETS = {
    "Baseline": DEFAULTS,
    "Peak summer": {"w_month": _nearest_month(5), "w_T": 45.0, "w_Q": 380.0},
    "Winter dispatch": {"w_month": _nearest_month(1), "w_T": 28.0, "w_Q": 280.0},
    "Max throughput": {"w_Q": 430.0, "w_P": 90.0},
    "Cold + slow": {"w_month": _nearest_month(1), "w_T": 22.0, "w_Q": 150.0},
    # The 8" tail at the 4 m/s ceiling. Petrol just survives this (+9 bar in
    # the worst month); diesel does not, because it is ~6x more viscous — so
    # the preset switches product too, or it would not reliably show the guard
    # firing. That contrast is itself the point: feasibility here is a property
    # of the product, not only of the flow rate.
    "Overdrive → slack": {
        "w_Q": 465.0,
        "w_P": 70.0,
        "w_product": "diesel",
        "w_rho": float(PRODUCTS["diesel"].density_ref_kgm3),
    },
}


def _sync_density():
    """Density is a property of the product until the operator overrides it."""
    st.session_state.w_rho = float(PRODUCTS[st.session_state.w_product].density_ref_kgm3)


def _apply_preset():
    name = st.session_state.w_preset
    if name:
        st.session_state.update(PRESETS[name])


mast = st.empty()  # filled once the run is done — it reports on the result

lab, pre = st.columns([1.05, 2.4])
with lab:
    st.markdown(rail_label("Dispatch console"), unsafe_allow_html=True)
with pre:
    st.segmented_control(
        "Preset",
        list(PRESETS),
        key="w_preset",
        on_change=_apply_preset,
        label_visibility="collapsed",
        help="Loads a scenario into the console as a starting "
        "point. Every value stays editable afterwards.",
    )

slots = st.columns(6)

with slots[0]:
    face = st.empty()
    with st.popover("⚙  batch", use_container_width=True):
        st.selectbox(
            "Product",
            list(PRODUCTS),
            key="w_product",
            format_func=lambda p: PRODUCTS[p].name,
            on_change=_sync_density,
        )
        st.number_input("Dispatch volume [KL]", 100.0, 5000.0, step=50.0, key="w_V")
        st.number_input(
            "Density [kg/m³]",
            650.0,
            950.0,
            step=0.5,
            key="w_rho",
            help="Observed density at the dispatch meter. One ρ₆₀ is resolved "
            "from this and reused at every point on the line. Changing "
            "the product resets this to its reference density.",
        )

with slots[1]:
    face_T = st.empty()
    with st.popover("⚙  temperature", use_container_width=True):
        st.slider(
            "Fuel temperature at the refinery [°C]",
            10.0,
            55.0,
            step=0.5,
            key="w_T",
            help="Temperature the product leaves the Bina refinery at, as "
            "read at the meter. It is BOTH the initial condition on the "
            "energy equation and, because the pipe is above ground there, "
            "half of Bina's environment temperature (air + fuel)/2. "
            "Everything downstream relaxes toward the soil.",
        )

with slots[2]:
    face_P = st.empty()
    with st.popover("⚙  pressure", use_container_width=True):
        st.slider(
            "Dispatch pressure [bar g]",
            10.0,
            100.0,
            step=1.0,
            key="w_P",
            help="Initial condition on the momentum equation. Too low and "
            "the line goes slack before Bijwasan.",
        )

with slots[3]:
    face_Q = st.empty()
    with st.popover("⚙  flow", use_container_width=True):
        st.slider(
            "Flow rate [m³/hr]",
            25.0,
            FLOW_MAX_M3HR,
            step=5.0,
            key="w_Q",
            help=(
                f"Sets velocity, hence Reynolds number, hence friction — and "
                f"hence both the pressure drop and the viscous heating. The "
                f"range runs to {FLOW_MAX_M3HR:.0f} m³/hr, which is "
                f"{VELOCITY_SLIDER_CAP_MS:.0f} m/s in the 8″ tail — the "
                f"narrower bore, and the one that limits the line since the "
                f"whole batch passes through it. The 18″ mainline is only at "
                f"{VELOCITY_SLIDER_CAP_MS * A_TAIL / A_MAIN / TAIL_FRACTION:.2f} m/s there."
            ),
        )
        st.caption(
            f"3 m/s in the **8″ tail** = {3.0 * A_TAIL / TAIL_FRACTION * 3600:.0f} m³/hr "
            f"— the binding constraint, since the whole batch passes through it · "
            f"slider ceiling {FLOW_MAX_M3HR:.0f} m³/hr "
            f"= {VELOCITY_SLIDER_CAP_MS:.0f} m/s in the mainline"
        )

with slots[4]:
    face_M = st.empty()
    with st.popover("⚙  season", use_container_width=True):
        st.selectbox(
            "Month",
            MONTH_OPTS,
            key="w_month",
            format_func=lambda m: MONTHS[m - 1],
            help="Soil temperature is a seasonal boundary condition, so "
            "the month sets how the product exchanges heat with "
            "the ground.",
        )
        st.caption(
            f"Soil: ERA5-Land **{SOIL_TEMPERATURE_VAR}** (100–289 cm) · burial {BURIAL_DEPTH_M} m"
        )

with slots[5]:
    face_X = st.empty()
    with st.popover("⚙  model", use_container_width=True):
        st.caption(
            "Switch a term off to see what it contributes. All on = the physically complete model."
        )
        st.checkbox(
            "Viscous heating",
            key="w_visc",
            help="Friction dissipates pump work as heat in the oil: ΔT ≈ 0.58 °C per 10 bar.",
        )
        st.checkbox(
            "Elevation (ρ·g·dz)", key="w_elev", help="100 m of elevation is ~8 bar of static head."
        )
        st.checkbox(
            "Pressure correction (CPL)",
            key="w_cpl",
            help="API MPMS 11.2.1. Shifts density ~0.5 % at 50 bar.",
        )
        st.slider(
            "U-value multiplier",
            0.5,
            2.0,
            step=0.05,
            key="w_U",
            help="Johansen + burial correlations are ±30 % at best. This is the "
            "knob a calibration step would fit against a measured receipt "
            "temperature. 1.0 = uncalibrated.",
        )

product = st.session_state.w_product
density = st.session_state.w_rho
V_dispatch = st.session_state.w_V
T_dispatch = st.session_state.w_T
P_dispatch = st.session_state.w_P
flow_rate = st.session_state.w_Q
month = st.session_state.w_month
visc, elev, cpl = (st.session_state.w_visc, st.session_state.w_elev, st.session_state.w_cpl)
U_scale = st.session_state.w_U

ablated = [n for n, on in (("viscous heating", visc), ("elevation", elev), ("CPL", cpl)) if not on]
n_terms = 3 - len(ablated)
u_main = (flow_rate / 3600.0) / A_MAIN
u_tail = (flow_rate * TAIL_FRACTION / 3600.0) / A_TAIL  # TAIL_FRACTION == 1.0 here
mass_t = V_dispatch * density / 1000.0

face.markdown(
    readout(
        "Product",
        PRODUCTS[product].name.split(" (")[0],
        f"· {PRODUCTS[product].is_standard}",
        glyph="⛽",
        accent=GOLD,
        sub=f"{V_dispatch:,.0f} KL · ρ {density:.1f} kg/m³ · {mass_t:,.0f} t",
    ),
    unsafe_allow_html=True,
)
face_T.markdown(
    readout(
        "Dispatch temp",
        f"{T_dispatch:.1f}",
        "°C",
        glyph="🌡",
        accent=GOLD,
        sub="initial condition · energy equation",
    ),
    unsafe_allow_html=True,
)
face_P.markdown(
    readout(
        "Dispatch pressure",
        f"{P_dispatch:.0f}",
        "bar g",
        glyph="◈",
        accent=BLUE,
        sub="initial condition · momentum equation",
    ),
    unsafe_allow_html=True,
)
face_Q.markdown(
    readout(
        "Flow rate",
        f"{flow_rate:.0f}",
        "m³/hr",
        glyph="➤",
        accent=BLUE,
        sub=(
            (
                f"<span class='hot'>{u_main:.2f} m/s</span> in the 8″ bore"
                if UNIFORM_BORE and TAIL_FRACTION >= 1.0
                else f"<span class='hot'>{u_main:.2f} m/s</span> in the 18″ main · "
                f"<span class='hot'>{u_tail:.2f} m/s</span> in the 8″ tail"
            )
        ),
    ),
    unsafe_allow_html=True,
)
face_M.markdown(
    readout(
        "Season",
        MONTHS[month - 1],
        glyph="🜨",
        accent=CYAN,
        sub=f"ERA5 soil · burial {BURIAL_DEPTH_M} m",
    ),
    unsafe_allow_html=True,
)
face_X.markdown(
    readout(
        "Model",
        f"{n_terms} / 3",
        "terms",
        glyph="∫",
        accent=GREEN if not ablated and abs(U_scale - 1.0) < 1e-9 else AMBER,
        sub=(
            f"U × {U_scale:.2f} · complete"
            if not ablated
            else f"U × {U_scale:.2f} · <span class='hot'>off: {', '.join(ablated)}</span>"
        ),
    ),
    unsafe_allow_html=True,
)


# ═══════════════════════════════════════════════════════════════════
# REAL-TIME AIR TEMPERATURE — BINA AND BIJWASAN
#
# The soil temperature is a monthly ERA5 climatology; the air temperature is
# today's, measured. Their mean is the environment the energy equation couples
# to — at the two terminals, which are the only places the line surfaces and
# therefore the only places air is queried at all. Everything about this block
# is built so that a failed request is impossible to mistake for a reading.
# ═══════════════════════════════════════════════════════════════════

TERMINALS = list(AIR_STATIONS)  # ["Bina", "Bijwasan"]

st.session_state.setdefault("w_air_nonce", 0)

air_head, air_btn = st.columns([5.2, 1])
with air_head:
    st.markdown(
        rail_label(f"Live air temperature · {' & '.join(TERMINALS)}"), unsafe_allow_html=True
    )
with air_btn:
    if st.button(
        "↻  refresh", use_container_width=True, help="Re-query both terminals now."
    ):
        st.session_state.w_air_nonce += 1

snapshot = load_air_temperatures(st.session_state.w_air_nonce)

soil = (
    soil_all[soil_all["month"] == month]
    .rename(columns={"waypoint_km": "km"})
    .reset_index(drop=True)
)
soil = build_environment_profile(soil, snapshot, T_fuel_C=T_dispatch)

air_ok = bool(snapshot.temperatures)

if not air_ok:
    reason = next(iter(snapshot.failures.values()), "unknown")
    st.markdown(
        callout(
            "bad",
            f"<b>No real-time air temperature for "
            f"{' or '.join(TERMINALS)}.</b> Those are the only stations whose "
            f"air enters the calculation, so the run below uses the ERA5 soil "
            f"temperature <i>alone</i> end to end — no air temperature has "
            f"been estimated to stand in for it. Check network access; "
            f"optionally set <code>OPENWEATHER_API_KEY</code> as an "
            f"environment variable, in <code>.env</code>, or in "
            f"<code>.streamlit/secrets.toml</code>.<br>"
            f"<code>{reason}</code>",
        ),
        unsafe_allow_html=True,
    )
elif snapshot.failures:
    rows = "<br>".join(
        f"· <b>{name}</b> — {reason}" for name, reason in sorted(snapshot.failures.items())
    )
    st.markdown(
        callout(
            "bad",
            f"<b>{', '.join(sorted(snapshot.failures))} returned no reading.</b> "
            f"That terminal falls back to the soil temperature alone — no air "
            f"temperature has been substituted for it.<br>{rows}",
        ),
        unsafe_allow_html=True,
    )

if air_ok:
    for col, station in zip(st.columns(len(TERMINALS)), TERMINALS):
        temp = snapshot.temperatures.get(station)
        col.markdown(
            readout(
                station,
                f"{temp:.1f}" if temp is not None else "—",
                "°C" if temp is not None else "",
                glyph="◉" if temp is not None else "⚠",
                accent=GOLD if temp is not None else RED,
                sub=(
                    "<span class='hot'>in T_env</span>" if temp is not None else "no data"
                ),
            ),
            unsafe_allow_html=True,
        )
    st.caption(
        f"{describe_provider(snapshot)} · fetched {snapshot.age_s():.0f} s ago · "
        f"**T_env blends air at {' and '.join(TERMINALS)} only** — every "
        f"intermediate station and all chainage between them uses soil alone, "
        f"because the pipe is buried at {BURIAL_DEPTH_M} m where the daily air "
        f"signal does not reach."
    )


# ═══════════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════════

inputs = SimulationInputs(
    product=product,
    T_dispatch_C=T_dispatch,
    V_dispatch_KL=V_dispatch,
    flow_rate_m3hr=flow_rate,
    month=month,
    P_dispatch_bar=P_dispatch,
    density_kgm3=density,
    include_viscous_heating=visc,
    include_elevation=elev,
    include_pressure_correction=cpl,
)

with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    with st.spinner("Integrating the coupled energy + momentum equations…"):
        df = simulate(inputs, route, soil, U_scale=U_scale)
    infeasible = [w for w in caught if issubclass(w.category, HydraulicFeasibilityWarning)]

first, last = df.iloc[0], df.iloc[-1]
feasible = df.attrs["hydraulically_feasible"]

n_air = df.attrs.get("n_stations_with_air", 0)
pills = [
    ("ok", "HYDRAULICS OK") if feasible else ("bad", "SLACK FLOW"),
    ("info", f"ERA5 {SOIL_TEMPERATURE_VAR.upper()} · {MONTHS[month - 1]}"),
    (
        ("ok" if n_air == len(TERMINALS) else "bad"),
        (
            f"LIVE AIR {n_air}/{len(TERMINALS)} TERMINALS · "
            f"{snapshot.provider.upper()}"
            if n_air
            else "NO LIVE AIR — SOIL ONLY"
        ),
    ),
]
if ablated:
    pills.append(("bad", "PHYSICS ABLATED: " + ", ".join(ablated)))
if abs(U_scale - 1.0) > 1e-9:
    pills.append(("info", f"U × {U_scale:.2f}"))

mast.markdown(
    masthead(
        subtitle=(
            f"{route.waypoints[0].name.split(' (')[0]} → "
            f"{route.waypoints[-1].name.split(' (')[0]}  ·  "
            f"{route.length_km:.0f} km  ·  {PRODUCTS[product].name}  ·  "
            f"{V_dispatch:,.0f} KL @ {T_dispatch:.1f} °C  ·  "
            f"{flow_rate:.0f} m³/hr"
        ),
        pills=pills,
    ),
    unsafe_allow_html=True,
)

if infeasible:
    st.markdown(
        callout("bad", f"<b>🚨 Hydraulically infeasible.</b> {infeasible[0].message}"),
        unsafe_allow_html=True,
    )


# ═══════════════════════════════════════════════════════════════════
# KPI ROW
# ═══════════════════════════════════════════════════════════════════

dT = last.T_C - first.T_C
dP = last.P_bar - first.P_bar
dVg = last.V_gross_KL - first.V_gross_KL
dVg_pct = dVg / first.V_gross_KL * 100.0
drift = abs(last.V_std_KL - first.V_std_KL) / first.V_std_KL * 100.0

cards = [
    kpi(
        "Receipt temperature",
        f"{last.T_C:.1f}",
        "°C",
        f"{dT:+.1f} °C over {route.length_km:.0f} km",
        "down" if dT < 0 else "up",
        GOLD,
    ),
    kpi(
        "Environment at receipt",
        f"{last.T_env_C:.1f}",
        "°C",
        (
            f"(air {last.T_air_C:.1f} + soil {last.T_soil_C:.1f}) / 2"
            if last.T_air_C == last.T_air_C  # NaN-safe: NaN != NaN
            else f"soil {last.T_soil_C:.1f} °C only — no live air"
        ),
        "none",
        CYAN,
    ),
    kpi(
        "Receipt pressure",
        f"{last.P_bar:.1f}",
        "bar",
        f"{dP:+.1f} bar",
        "down" if dP < 0 else "up",
        BLUE if feasible else RED,
    ),
    kpi(
        "Gross volume",
        f"{last.V_gross_KL:,.1f}",
        "KL",
        f"{dVg:+.1f} KL  ({dVg_pct:+.2f} %)",
        "down" if dVg < 0 else "up",
        GOLD,
    ),
    kpi(
        "Standard volume @ 15 °C",
        f"{last.V_std_KL:,.2f}",
        "KL",
        (
            f"of {df.attrs['V_std_KL']:,.0f} KL dispatched · "
            f"{df.attrs['V_std_delivered_KL']:,.0f} KL delivered en route"
            if df.attrs.get("n_flow_splits")
            else f"drift {drift:.1e} %  ✓ conserved"
        ),
        "flat",
        GREEN,
    ),
    kpi(
        "Transit time",
        # Line fill / volumetric rate. Uses the real two-diameter line fill,
        # not length x mainline area — the 8" tail holds far less per km.
        f"{route.linefill_m3() / (flow_rate / 3600) / 3600:.1f}",
        "hr",
        f"{df.velocity_ms.mean():.2f} m/s mean",
        "none",
        VIOLET,
    ),
]
for col, card in zip(st.columns(6), cards):
    col.markdown(card, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════
# PIPELINE SCHEMATIC
# ═══════════════════════════════════════════════════════════════════

st.markdown(
    section(
        "Pipeline mimic", "pipe colour = product temperature · terrain below drives the static head"
    ),
    unsafe_allow_html=True,
)
st.plotly_chart(
    pipeline_schematic(df, route), use_container_width=True, config={"displayModeBar": False}
)

if df.attrs.get("n_flow_splits"):
    bal_err = abs(df.attrs["V_std_balance_error_KL"])
    rows = " · ".join(
        f"<b>{s['name']}</b> {s['V_std_delivered_KL']:,.1f} KL ({s['delivered_pct']:.0f} %)"
        for s in df.attrs["flow_splits"]
    )
    st.markdown(
        callout(
            "ok" if bal_err < 1e-6 else "bad",
            f"<b>{df.attrs['V_std_KL']:,.1f} KL dispatched = "
            f"{df.attrs['V_std_delivered_KL']:,.1f} KL delivered en route + "
            f"{df.attrs['V_std_receipt_KL']:,.1f} KL received at Bijwasan</b> "
            f"(balance closes to {bal_err:.1e} KL).<br>{rows}.<br>"
            f"The receipt figure is <i>{100 * (1 - df.attrs['mass_fraction_delivered']):.0f} % "
            f"of the batch by design</i> — product taken off at a delivery "
            f"terminal, not product lost. Standard volume is still invariant "
            f"<i>within</i> each segment; what is conserved across the whole "
            f"line is the total, not the endpoint value.",
        ),
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        callout(
            "ok",
            f"<b>Standard volume is conserved to {drift:.1e} %</b> across the "
            f"whole line, while gross volume moves by <b>{abs(dVg_pct):.2f} %</b>. "
            f"The product physically expands and contracts; the <i>quantity</i> "
            f"of product does not change. This is the API MPMS correction doing "
            f"exactly its job.",
        )
        if drift < 0.01
        else callout(
            "bad",
            f"<b>Standard volume drifted {drift:.2e} %.</b> The VCF chain is "
            f"not internally consistent — this should never happen.",
        ),
        unsafe_allow_html=True,
    )


# ═══════════════════════════════════════════════════════════════════
# CHARTS
# ═══════════════════════════════════════════════════════════════════


def trace(y, name, colour, dash=None, width=2.4):
    return go.Scatter(
        x=df["km"],
        y=df[y],
        mode="lines",
        name=name,
        line=dict(color=colour, width=width, dash=dash),
    )


def style(fig, height, ytitles):
    lay = dict(PLOT_LAYOUT)
    lay.update(
        height=height,
        hovermode="x unified",
        legend=dict(orientation="h", y=1.06, x=1, xanchor="right", bgcolor="rgba(0,0,0,0)"),
    )
    fig.update_layout(**lay)
    for i, t in enumerate(ytitles, start=1):
        fig.update_yaxes(title_text=t, row=i, col=1, **GRID, title_font=dict(size=11, color=MUTED))
    fig.update_xaxes(**GRID)
    fig.update_xaxes(
        title_text="Chainage from dispatch [km]",
        row=len(ytitles),
        col=1,
        title_font=dict(size=11, color=MUTED),
    )
    return fig


t1, t2, t3, t4, t5 = st.tabs(
    [
        "🌡️  Thermal & Volume",
        "💧  Hydraulics",
        "📋  Station Report",
        "🗺️  Route",
        "📐  Model",
    ]
)

with t1:
    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.075,
        subplot_titles=(
            "Temperature — product relaxes toward the environment, (air + soil) / 2",
            "Gross volume — breathes with temperature and pressure",
            "Standard volume @ 15 °C — must stay flat",
        ),
    )
    fig.add_trace(trace("T_C", "Product", GOLD, width=3), 1, 1)
    fig.add_trace(trace("T_env_C", "Environment (boundary condition)", CYAN, width=2.6), 1, 1)
    fig.add_trace(trace("T_soil_C", "Soil — ERA5", BLUE, "dash", 1.8), 1, 1)
    if air_ok:
        # Air exists at two points, not along a curve. Drawn as markers so the
        # chart cannot imply an air measurement where there is none.
        air_pts = df[df["T_air_C"].notna()]
        fig.add_trace(
            go.Scatter(
                x=air_pts["km"],
                y=air_pts["T_air_C"],
                mode="markers",
                name=f"Air — live, {'/'.join(TERMINALS)} only",
                marker=dict(color=AMBER, size=12, symbol="diamond", line=dict(width=1.5)),
            ),
            1,
            1,
        )
    fig.add_trace(trace("V_gross_KL", "Gross volume", GOLD, width=3), 2, 1)
    fig.add_trace(trace("V_std_KL", "Standard volume", GREEN, width=3), 3, 1)

    # Pin the V_std axis around its mean so a flat line reads as flat rather
    # than as magnified floating-point noise.
    v = df["V_std_KL"].mean()
    fig.update_yaxes(range=[v - 1.0, v + 1.0], row=3, col=1)

    st.plotly_chart(
        style(fig, 780, ["T [°C]", "V_gross [KL]", "V_std [KL]"]), use_container_width=True
    )

with t2:
    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.075,
        subplot_titles=(
            "Pressure — friction plus static head",
            "Density — rises as the product cools",
            "Viscosity — rises as the product cools",
        ),
    )
    fig.add_trace(trace("P_bar", "Pressure", BLUE, width=3), 1, 1)
    fig.add_trace(trace("P_vapour_bar", "Vapour pressure", RED, "dot", 1.6), 1, 1)
    fig.add_trace(trace("rho_kgm3", "Density", CYAN, width=3), 2, 1)
    fig.add_trace(trace("mu_cP", "Viscosity", VIOLET, width=3), 3, 1)

    if not feasible:
        onset = df.attrs["slack_flow_onset_km"]
        fig.add_vrect(
            x0=onset,
            x1=df["km"].iloc[-1],
            row=1,
            col=1,
            fillcolor="rgba(239,68,68,0.12)",
            line_width=0,
            annotation_text="slack flow",
            annotation_position="top left",
            annotation_font=dict(size=10, color=RED),
        )

    st.plotly_chart(style(fig, 780, ["P [bar g]", "ρ [kg/m³]", "μ [cP]"]), use_container_width=True)

    with st.expander("Reynolds number and friction factor"):
        f2 = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.1,
            subplot_titles=("Reynolds number", "Darcy friction factor"),
        )
        f2.add_trace(trace("Re", "Re", GOLD), 1, 1)
        f2.add_trace(trace("friction_factor", "f", VIOLET), 2, 1)
        st.plotly_chart(style(f2, 460, ["Re [-]", "f [-]"]), use_container_width=True)

with t3:
    splits = df.attrs.get("flow_splits", [])
    if splits:
        st.markdown(
            section(
                "Deliveries en route",
                "product taken off the line before Bijwasan — this is why the "
                "receipt volume is smaller than the dispatch volume",
            ),
            unsafe_allow_html=True,
        )
        st.dataframe(
            pd.DataFrame(splits).rename(
                columns={
                    "name": "Delivery",
                    "km": "km",
                    "delivered_pct": "Delivered [%]",
                    "V_std_delivered_KL": "V_std delivered [KL]",
                    "mass_delivered_kg": "Mass [kg]",
                    "m_dot_delivered_kgs": "ṁ off-take [kg/s]",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

    stations = df.attrs.get("pump_stations", [])
    if stations:
        st.markdown(
            section(
                "Booster pump stations",
                "a 360 km line is not pumped from one end — each station "
                "re-pressurises the product to its discharge setting",
            ),
            unsafe_allow_html=True,
        )
        st.dataframe(
            pd.DataFrame(stations).rename(
                columns={
                    "name": "Station",
                    "km": "km",
                    "P_suction_bar": "Suction [bar]",
                    "P_discharge_bar": "Discharge [bar]",
                    "boost_bar": "Boost [bar]",
                    "suction_ok": "Suction OK",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )
        cav = df.attrs.get("cavitating_stations", [])
        if cav:
            st.markdown(
                callout(
                    "bad",
                    f"<b>Inadequate suction at {', '.join(cav)}.</b> The product "
                    f"arrives below the minimum suction pressure, so the pump "
                    f"would cavitate rather than boost. Reduce the flow, raise "
                    f"the upstream discharge, or move the station.",
                ),
                unsafe_allow_html=True,
            )

    st.markdown(section("State at each station"), unsafe_allow_html=True)
    wp = df[df["waypoint_name"].notna()].copy()
    # The four temperature columns are laid out air → soil → environment →
    # product, left to right, so the chain is readable across the row:
    # two measured surroundings, their mean, and the solved fluid temperature
    # that came out of using that mean as the boundary condition.
    cols = {
        "km": "km",
        "waypoint_name": "Station",
        "elevation_m": "Elev [m]",
        "T_air_C": "Air Temp [°C]",
        "T_soil_C": "Soil Temp [°C]",
        "T_env_C": "Environment Temp [°C]",
        "T_env_basis": "Env basis",
        "T_C": "Product Temp [°C]",
        "P_bar": "Press [bar]",
        "rho_kgm3": "ρ [kg/m³]",
        "mu_cP": "μ [cP]",
        "Re": "Re",
        "V_gross_KL": "Gross V [KL]",
        "V_std_KL": "Std V @15°C [KL]",
    }
    show = (
        wp[list(cols)]
        .rename(columns=cols)
        .round(
            {
                "Air Temp [°C]": 2,
                "Soil Temp [°C]": 2,
                "Environment Temp [°C]": 2,
                "Product Temp [°C]": 2,
                "Press [bar]": 2,
                "ρ [kg/m³]": 2,
                "μ [cP]": 3,
                "Gross V [KL]": 2,
                "Std V @15°C [KL]": 3,
            }
        )
    )
    st.dataframe(show, use_container_width=True, hide_index=True)

    st.markdown(
        callout(
            "ok" if air_ok else "warn",
            (
                f"<b>Air Temp</b> is the live reading at that station's own "
                f"coordinates, and is shown <i>only where it enters the "
                f"physics</i> — <b>{' and '.join(TERMINALS)}</b>. "
                f"Blank elsewhere because air is not used elsewhere. "
                f"<b>Soil Temp</b> is the unchanged ERA5-Land "
                f"<code>{SOIL_TEMPERATURE_VAR}</code> climatology at "
                f"{BURIAL_DEPTH_M} m burial. <b>Environment Temp</b> is what "
                f"the energy equation drives toward, and <b>Env basis</b> says "
                f"how each one was formed: <code>air+soil</code> at the "
                f"terminals, <code>soil</code> along the buried run, "
                f"<code>air+fuel</code> at the refinery, where the pipe is "
                f"above ground and the soil is not its environment. "
                f"<b>Product Temp</b> is the "
                f"solved fluid temperature — a result, not an input."
            ),
        ),
        unsafe_allow_html=True,
    )

    st.markdown(
        callout(
            "ok",
            "Gross volume moves with temperature and pressure. Standard "
            "volume — the same product, corrected to 15 °C by API MPMS "
            "11.1 / 11.2.1 — does not. That is the entire point of volume "
            "correction, and it is the simulator's strongest self-check.",
        ),
        unsafe_allow_html=True,
    )

    st.download_button(
        "📥  Download full state table (every km)",
        df.to_csv(index=False),
        file_name=(
            f"mmbl_{product}_{MONTHS[month - 1].lower()}_{T_dispatch:.0f}C_{flow_rate:.0f}m3hr.csv"
        ),
        mime="text/csv",
    )

with t4:
    fig = go.Figure(
        go.Scattermapbox(
            lat=df["lat"],
            lon=df["lon"],
            mode="lines",
            line=dict(width=4, color=GOLD),
            name="Pipeline",
            hovertext=[f"km {k:.0f} · {t:.1f} °C" for k, t in zip(df["km"], df["T_C"])],
        )
    )
    wp = df[df["waypoint_name"].notna()]
    fig.add_trace(
        go.Scattermapbox(
            lat=wp["lat"],
            lon=wp["lon"],
            mode="markers+text",
            marker=dict(size=12, color=CYAN),
            text=[n.split(" (")[0] for n in wp["waypoint_name"]],
            textposition="top right",
            textfont=dict(size=11, color="#E8EEF9"),
            name="Stations",
        )
    )
    fig.update_layout(
        mapbox=dict(
            style="carto-darkmatter",
            center=dict(lat=df["lat"].mean(), lon=df["lon"].mean()),
            zoom=5.7,
        ),
        height=560,
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)

    st.markdown(
        section("Route definition", "geometry is data — the whole route is one CSV"),
        unsafe_allow_html=True,
    )
    st.dataframe(route.waypoint_frame(), use_container_width=True, hide_index=True)
    st.markdown(
        callout(
            "warn",
            f"<b>Every waypoint is flagged <code>ESTIMATED</code>.</b> "
            f"Elevations, diameters and chainages are approximations and "
            f"must be replaced with surveyed values before any result is "
            f"presented as authoritative. The line is modelled as an "
            f"<b>18″ (0.4572 m OD, 14.27 mm wall) mainline</b> from Bina to "
            f"Piyala at km {route.waypoints[-2].chainage_km:.0f}, then an "
            f"<b>8″ (0.219 m OD, 8.18 mm wall) tail</b> into Bijwasan — "
            f"see <code>data/route/README.md</code>.",
        ),
        unsafe_allow_html=True,
    )

with t5:
    st.markdown(section("What this simulator solves"), unsafe_allow_html=True)
    st.markdown(r"""
The energy and momentum equations are integrated **together**, as one ODE system in the
state $y = [T, P]$ — because they are coupled three ways: temperature sets viscosity (and
therefore friction), friction heats the oil, and temperature sets density (and therefore
the static head). Solving them in sequence, as the earlier model did, is not physically
possible.

**Energy**

$$\dot m\,C_p\,\frac{dT}{dx} = \underbrace{-\,U(x)\,\pi D_o\,\bigl(T - T_{env}(x)\bigr)}_{\text{heat exchange with the environment}} \;+\; \underbrace{\dot m\,\frac{f\,u^{2}}{2 D_i}}_{\text{viscous dissipation}}$$

with the environment temperature the mean of the two measured surroundings,

$$T_{env} = \frac{T_{air}^{\,\text{current}} + T_{soil}}{2}$$

Elevation does **not** appear here: potential energy trades reversibly with pressure and
does not heat the fluid. Only the irreversible friction loss does.

**Momentum**

$$\frac{dP}{dx} = \underbrace{-\,f\,\frac{\rho u^{2}}{2 D_i}}_{\text{Darcy–Weisbach}} \;\underbrace{-\;\rho\,g\,\frac{dz}{dx}}_{\text{elevation}}$$

**Volume.** The batch's mass is fixed, so gross volume follows the density,
$V_{gross}(x) = m / \rho(T,P)$, while the standard volume
$V_{15} = V_{gross}\cdot C_{TL}(T)\cdot C_{PL}(P)$ is invariant by construction.

**Soil.** $T_{soil}$ and $\theta$ come from ERA5-Land **layer 4 (100–289 cm)** — the layer
the pipe actually occupies at 1.2 m. They are *boundary conditions*: measured inputs, never
predicted. $k_{soil}$ follows from Johansen (1975), and $U$ from radial conduction through
the steel wall and the surrounding soil. Note that $k_{soil}$ is **not** averaged with
anything — the conduction path out of the pipe is through soil, so the soil's own
conductivity still sets $U$. Only the *sink temperature* is the air/soil mean.

**Air.** $T_{air}$ is the **current** observed temperature at the two terminals — **Bina**
and **Bijwasan** — queried at each terminal's own coordinates. Those are the only two
points where the line surfaces, so they are the only two where air belongs in the boundary
condition, and the only two that are queried at all. OpenWeather's Current Weather API is
used when a key is configured (`units=metric`, read from `main.temp`); Open-Meteo's current
analysis when it is not. Either way the value is never forecast, never modelled, and never
interpolated in time. If a terminal's request fails, it is reported as failed and falls
back to soil alone — no air temperature is substituted for it.
""")
    st.markdown(section("Sources"), unsafe_allow_html=True)
    st.markdown("""
- Çengel, *Heat Transfer: A Practical Approach*, Ch. 3 — buried-cylinder conduction
- Colebrook (1939); Swamee & Jain (1976); Moody (1944) — friction factor
- Johansen (1975) — soil thermal conductivity from moisture
- API MPMS Ch. 11.1 (CTL) and Ch. 11.2.1 (CPL) — volume correction
- ERA5-Land reanalysis (ECMWF/Copernicus) — soil temperature and moisture
""")

st.markdown(
    '<div class="foot">'
    "<b>MMBL Thermal-Hydraulic Simulator</b> · physics-based · no ML in the "
    "physics path · validated against energy, momentum and mass conservation "
    "(75 tests).<br>"
    "Route and soil profile are data files; the simulator never reads a GRIB "
    "file at runtime and never substitutes synthetic climate data. "
    "Known limits are documented in <code>README.md</code>."
    "</div>",
    unsafe_allow_html=True,
)
