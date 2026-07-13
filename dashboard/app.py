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

import os
import sys
import warnings

import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import BURIAL_DEPTH_M, PRODUCTS, SOIL_TEMPERATURE_VAR
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


def _nearest_month(target: int) -> int:
    """Presets ask for a month; the soil table decides which ones exist."""
    return target if target in MONTH_OPTS else min(MONTH_OPTS, key=lambda m: abs(m - target))


DEFAULTS = {
    "w_product": FIRST_PRODUCT,
    "w_rho": float(PRODUCTS[FIRST_PRODUCT].density_ref_kgm3),
    "w_V": 1000.0,
    "w_T": 40.0,
    "w_P": 70.0,
    "w_Q": 400.0,
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
    "Peak summer": {"w_month": _nearest_month(5), "w_T": 45.0, "w_Q": 500.0},
    "Winter dispatch": {"w_month": _nearest_month(1), "w_T": 28.0, "w_Q": 300.0},
    "Max throughput": {"w_Q": 650.0, "w_P": 90.0},
    "Cold + slow": {"w_month": _nearest_month(1), "w_T": 22.0, "w_Q": 150.0},
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
            "Dispatch temperature [°C]",
            10.0,
            55.0,
            step=0.5,
            key="w_T",
            help="The initial condition on the energy equation. Everything "
            "downstream relaxes from here toward the soil.",
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
            100.0,
            700.0,
            step=10.0,
            key="w_Q",
            help="Sets velocity, hence Reynolds number, hence friction — "
            "and hence both the pressure drop and the viscous heating.",
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
u_dispatch = (flow_rate / 3600.0) / route.area(0.0)
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
        sub=f"<span class='hot'>{u_dispatch:.2f} m/s</span> at dispatch",
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
soil = (
    soil_all[soil_all["month"] == month]
    .rename(columns={"waypoint_km": "km"})
    .reset_index(drop=True)
)

with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    with st.spinner("Integrating the coupled energy + momentum equations…"):
        df = simulate(inputs, route, soil, U_scale=U_scale)
    infeasible = [w for w in caught if issubclass(w.category, HydraulicFeasibilityWarning)]

first, last = df.iloc[0], df.iloc[-1]
feasible = df.attrs["hydraulically_feasible"]

pills = [
    ("ok", "HYDRAULICS OK") if feasible else ("bad", "SLACK FLOW"),
    ("info", f"ERA5 {SOIL_TEMPERATURE_VAR.upper()} · {MONTHS[month - 1]}"),
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
    kpi("Soil at receipt", f"{last.T_soil_C:.1f}", "°C", "boundary condition", "none", CYAN),
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
        f"drift {drift:.1e} %  ✓ conserved",
        "flat",
        GREEN,
    ),
    kpi(
        "Transit time",
        f"{(route.length_km * 1000 * route.area(0.0)) / (flow_rate / 3600) / 3600:.1f}",
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
            "Temperature — product relaxes toward the ground",
            "Gross volume — breathes with temperature and pressure",
            "Standard volume @ 15 °C — must stay flat",
        ),
    )
    fig.add_trace(trace("T_C", "Product", GOLD, width=3), 1, 1)
    fig.add_trace(trace("T_soil_C", "Soil (boundary condition)", CYAN, "dash"), 1, 1)
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
    st.markdown(section("State at each station"), unsafe_allow_html=True)
    wp = df[df["waypoint_name"].notna()].copy()
    cols = {
        "km": "km",
        "waypoint_name": "Station",
        "elevation_m": "Elev [m]",
        "T_C": "Temp [°C]",
        "T_soil_C": "Soil [°C]",
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
                "Temp [°C]": 2,
                "Soil [°C]": 2,
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
        section("Route definition", "geometry is data — extending to Bina is a CSV edit"),
        unsafe_allow_html=True,
    )
    st.dataframe(route.waypoint_frame(), use_container_width=True, hide_index=True)
    st.markdown(
        callout(
            "warn",
            "<b>Every waypoint is flagged <code>ESTIMATED</code>.</b> "
            "Elevations, diameters and chainages are approximations and "
            "must be replaced with surveyed values before any result is "
            "presented as authoritative. The 8-inch tail segment has been "
            "removed pending verification — see "
            "<code>data/route/README.md</code>.",
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

$$\dot m\,C_p\,\frac{dT}{dx} = \underbrace{-\,U(x)\,\pi D_o\,\bigl(T - T_{soil}(x)\bigr)}_{\text{heat exchange with the ground}} \;+\; \underbrace{\dot m\,\frac{f\,u^{2}}{2 D_i}}_{\text{viscous dissipation}}$$

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
the steel wall and the surrounding soil.
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
