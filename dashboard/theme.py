"""
Dashboard theme — industrial SCADA styling
===========================================

Colour tokens, CSS and reusable HTML components for the MMBL simulator UI.
Kept separate from app.py so the physics and the presentation do not tangle.

Palette is built around BPCL's brand blue and gold, on a deep navy control-room
background. Every colour has a job:

    gold    -> the product / the thing being tracked
    cyan    -> the ground / the boundary condition
    blue    -> pressure and hydraulics
    green   -> conserved quantities (standard volume) and healthy status
    red     -> hydraulic infeasibility, vapour pressure, alarms

Input model — the console rail
------------------------------
There is no sidebar. Simulation parameters live in a horizontal instrument
cluster below the masthead: each slot is a READOUT showing the value that is
currently loaded, and the readout itself opens into the controls that set it.

A sidebar shows widgets permanently and the resulting state nowhere. A control
room does the opposite — the panel always reads back the state of the plant, and
you reach for a control only when you intend to change something. The rail is
the second of those. It also means the simulation's full input vector is legible
in one glance, on the same screen as its output, which is what actually matters
when you are comparing runs.
"""

import base64
import os

# ── Colour tokens ────────────────────────────────────────────────────
BG = "#080C15"
PANEL = "#111827"
PANEL_2 = "#161F32"
BORDER = "#243049"
TEXT = "#E8EEF9"
MUTED = "#8397B8"

GOLD = "#F5A623"  # product
CYAN = "#2DD4BF"  # soil / ground
BLUE = "#3B82F6"  # pressure
GREEN = "#22C55E"  # conserved / OK
VIOLET = "#A78BFA"  # viscosity
RED = "#EF4444"  # alarm
AMBER = "#F59E0B"  # warning

# Temperature colourscale for the pipeline schematic (cool -> hot)
TEMP_SCALE = [
    [0.00, "#1E3A8A"],
    [0.25, "#2DD4BF"],
    [0.50, "#22C55E"],
    [0.70, "#F5A623"],
    [1.00, "#EF4444"],
]

PLOT_LAYOUT = dict(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="Inter, Segoe UI, system-ui, sans-serif", size=12, color=TEXT),
    margin=dict(l=60, r=30, t=40, b=40),
    hoverlabel=dict(bgcolor=PANEL_2, bordercolor=BORDER, font=dict(color=TEXT, size=12)),
    legend=dict(bgcolor="rgba(0,0,0,0)", borderwidth=0),
)

GRID = dict(gridcolor="rgba(36,48,73,0.55)", zerolinecolor="rgba(36,48,73,0.9)", linecolor=BORDER)


CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap');

.stApp {{
    background:
      radial-gradient(1200px 600px at 15% -10%, #16233D 0%, transparent 55%),
      radial-gradient(900px 500px at 95% 0%, #1A1633 0%, transparent 50%),
      {BG};
    color: {TEXT};
    font-family: 'Inter', system-ui, sans-serif;
}}

/* Kill Streamlit chrome. The sidebar is gone entirely — the console rail
   replaces it — so its collapse arrow must go too, or it reads as a bug. */
#MainMenu, footer, header {{ visibility: hidden; }}
section[data-testid="stSidebar"],
[data-testid="stSidebarCollapsedControl"] {{ display: none !important; }}
.block-container {{ padding-top: 1.1rem; padding-bottom: 2.5rem; max-width: 1600px; }}

/* ── Masthead ─────────────────────────────────────────────── */
.masthead {{
    display: flex; align-items: center; gap: 18px;
    padding: 16px 22px; margin-bottom: 16px;
    background: linear-gradient(100deg, {PANEL} 0%, {PANEL_2} 55%, rgba(30,58,138,0.30) 100%);
    border: 1px solid {BORDER};
    border-radius: 14px;
    box-shadow: 0 8px 30px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.05);
}}
.masthead .logo {{ flex: 0 0 auto; display: flex; align-items: center; }}
/* The BPCL mark is supplied on a white field. Sitting it directly on the dark
   masthead would read as a stray white rectangle, so it gets a proper brand
   lockup tile — white ground, soft border, gold rim light. Standard practice
   for a corporate mark on a dark UI, and it keeps the artwork untouched. */
.masthead .logo img {{
    height: 54px; width: auto; display: block;
    background: #FFFFFF;
    padding: 5px 9px;
    border-radius: 9px;
    border: 1px solid rgba(245,166,35,0.35);
    box-shadow: 0 3px 14px rgba(0,0,0,0.35),
                inset 0 0 0 1px rgba(255,255,255,0.9);
}}
.masthead .rule {{
    width: 1px; height: 58px; flex: 0 0 auto;
    background: linear-gradient(180deg, transparent, {BORDER}, transparent);
}}
.masthead .titles {{ flex: 1 1 auto; min-width: 0; }}
.masthead h1 {{
    margin: 0; font-size: 1.32rem; font-weight: 800; letter-spacing: -0.015em;
    color: {TEXT}; line-height: 1.25;
}}
.masthead h1 .accent {{ color: {GOLD}; }}
.masthead .sub {{
    margin-top: 3px; font-size: 0.78rem; color: {MUTED}; font-weight: 500;
    letter-spacing: 0.02em;
}}
.masthead .pills {{ display: flex; gap: 8px; flex: 0 0 auto; flex-wrap: wrap;
                    justify-content: flex-end; }}

.pill {{
    display: inline-flex; align-items: center; gap: 6px;
    padding: 5px 11px; border-radius: 999px;
    font-size: 0.70rem; font-weight: 600; letter-spacing: 0.03em;
    border: 1px solid; white-space: nowrap;
}}
.pill .dot {{ width: 7px; height: 7px; border-radius: 50%; }}
.pill-ok    {{ color: {GREEN}; border-color: rgba(34,197,94,0.35);  background: rgba(34,197,94,0.10); }}
.pill-ok    .dot {{ background: {GREEN}; box-shadow: 0 0 8px {GREEN}; }}
.pill-bad   {{ color: {RED};   border-color: rgba(239,68,68,0.40);  background: rgba(239,68,68,0.12); }}
.pill-bad   .dot {{ background: {RED};   box-shadow: 0 0 8px {RED}; animation: pulse 1.4s infinite; }}
.pill-info  {{ color: {MUTED}; border-color: {BORDER}; background: rgba(255,255,255,0.03); }}
.pill-info  .dot {{ background: {MUTED}; }}
@keyframes pulse {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: 0.35; }} }}

/* ══ CONSOLE RAIL ══════════════════════════════════════════════════
   The input model. Each slot is a readout card whose lower edge is the
   popover trigger that opens its controls — so the card reads as one
   physical instrument: face on top, adjustment knob beneath.            */

.rail-label {{
    display: flex; align-items: center; gap: 9px;
    font-size: 0.62rem; font-weight: 800; letter-spacing: 0.16em;
    text-transform: uppercase; color: {MUTED};
    margin: 2px 0 9px 2px;
}}
.rail-label::after {{
    content: ''; flex: 1 1 auto; height: 1px;
    background: linear-gradient(90deg, {BORDER}, transparent);
}}
.rail-label .lamp {{
    width: 6px; height: 6px; border-radius: 50%; background: {GOLD};
    box-shadow: 0 0 7px {GOLD};
}}

/* The readout face */
.rdo {{
    position: relative; overflow: hidden;
    background: linear-gradient(165deg, {PANEL} 0%, {PANEL_2} 100%);
    border: 1px solid {BORDER}; border-bottom: 0;
    border-radius: 12px 12px 0 0;
    padding: 11px 14px 10px 15px;
    min-height: 92px;
}}
/* Faint scanline texture — reads as an instrument face, not a web card. */
.rdo::after {{
    content: ''; position: absolute; inset: 0; pointer-events: none;
    background: repeating-linear-gradient(
        180deg, rgba(255,255,255,0.022) 0 1px, transparent 1px 3px);
}}
.rdo::before {{
    content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
    background: var(--accent, {GOLD});
    box-shadow: 0 0 12px var(--accent, {GOLD});
    opacity: 0.85;
}}
.rdo .k {{
    display: flex; align-items: center; gap: 6px;
    font-size: 0.60rem; font-weight: 800; letter-spacing: 0.10em;
    text-transform: uppercase; color: {MUTED}; margin-bottom: 6px;
}}
.rdo .v {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 1.18rem; font-weight: 600; color: {TEXT};
    line-height: 1.15; letter-spacing: -0.01em;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.rdo .v .u {{ font-size: 0.76rem; color: {MUTED}; font-weight: 400; margin-left: 3px; }}
.rdo .s {{
    margin-top: 5px; font-size: 0.68rem; color: {MUTED}; font-weight: 500;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.rdo .s .hot {{ color: var(--accent, {GOLD}); font-weight: 600; }}

/* The trigger, welded to the bottom edge of the face it belongs to.
   Popovers are used nowhere else in the app, so targeting them globally
   is safe and keeps this free of brittle nth-child selectors. */
[data-testid="stPopover"] {{ margin-top: -1rem; }}
[data-testid="stPopover"] button {{
    width: 100%;
    background: rgba(255,255,255,0.025);
    border: 1px solid {BORDER}; border-top: 1px dashed rgba(36,48,73,0.9);
    border-radius: 0 0 12px 12px;
    color: {MUTED};
    font-size: 0.62rem; font-weight: 800; letter-spacing: 0.14em;
    text-transform: uppercase;
    padding: 6px 0 7px 0; min-height: 0;
    transition: background .15s ease, color .15s ease, border-color .15s ease;
}}
[data-testid="stPopover"] button:hover {{
    background: rgba(245,166,35,0.09);
    color: {GOLD};
    border-color: rgba(245,166,35,0.40);
}}
[data-testid="stPopover"] button:focus:not(:active) {{ color: {GOLD}; }}
[data-testid="stPopoverBody"] {{
    background: {PANEL}; border: 1px solid {BORDER};
    border-radius: 12px; padding: 14px 16px 10px 16px;
    box-shadow: 0 18px 50px rgba(0,0,0,0.6);
}}

/* Scenario presets. Streamlit renders these through BaseWeb's button group,
   which ships its own light-mode palette, so the tokens have to be forced. */
[data-testid="stSegmentedControl"] [role="group"],
[data-testid="stButtonGroup"] [role="group"] {{ gap: 6px; }}
[data-testid="stSegmentedControl"] button,
[data-testid="stButtonGroup"] button {{
    background: rgba(255,255,255,0.025) !important;
    border: 1px solid {BORDER} !important;
    border-radius: 8px !important;
    color: {MUTED} !important;
    font-size: 0.72rem !important; font-weight: 600 !important;
    padding: 5px 13px !important;
}}
[data-testid="stSegmentedControl"] button:hover,
[data-testid="stButtonGroup"] button:hover {{
    border-color: rgba(245,166,35,0.45) !important;
    color: {GOLD} !important;
}}
[data-testid="stSegmentedControl"] button[aria-checked="true"],
[data-testid="stButtonGroup"] button[aria-checked="true"] {{
    background: rgba(245,166,35,0.14) !important;
    border-color: rgba(245,166,35,0.5) !important;
    color: {GOLD} !important;
}}

/* ── KPI cards ────────────────────────────────────────────── */
.kpi {{
    position: relative; overflow: hidden;
    background: linear-gradient(160deg, {PANEL} 0%, {PANEL_2} 100%);
    border: 1px solid {BORDER}; border-radius: 12px;
    padding: 14px 16px 13px 18px; height: 100%;
    transition: transform .16s ease, border-color .16s ease;
}}
.kpi:hover {{ transform: translateY(-2px); border-color: rgba(245,166,35,0.35); }}
.kpi::before {{
    content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
    background: var(--accent, {GOLD});
}}
.kpi .label {{
    font-size: 0.66rem; font-weight: 700; letter-spacing: 0.09em;
    text-transform: uppercase; color: {MUTED}; margin-bottom: 7px;
}}
.kpi .value {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 1.55rem; font-weight: 600; color: {TEXT};
    line-height: 1.1; letter-spacing: -0.02em;
}}
.kpi .value .unit {{ font-size: 0.85rem; color: {MUTED}; margin-left: 3px; font-weight: 400; }}
.kpi .delta {{
    margin-top: 6px; font-size: 0.74rem; font-weight: 600;
    font-family: 'JetBrains Mono', monospace;
}}
.kpi .delta.up   {{ color: {RED}; }}
.kpi .delta.down {{ color: {CYAN}; }}
.kpi .delta.flat {{ color: {GREEN}; }}
.kpi .delta.none {{ color: {MUTED}; font-weight: 500; }}

/* ── Section headings ─────────────────────────────────────── */
.section {{
    display: flex; align-items: center; gap: 10px;
    margin: 22px 0 12px 0;
}}
.section .bar {{ width: 3px; height: 17px; background: {GOLD}; border-radius: 2px; }}
.section h3 {{
    margin: 0; font-size: 0.94rem; font-weight: 700; color: {TEXT};
    letter-spacing: -0.005em;
}}
.section .hint {{ font-size: 0.74rem; color: {MUTED}; margin-left: auto; font-weight: 500; }}

/* ── Panels ───────────────────────────────────────────────── */
.panel {{
    background: linear-gradient(160deg, rgba(17,24,39,0.85), rgba(22,31,50,0.85));
    border: 1px solid {BORDER}; border-radius: 12px; padding: 6px 10px 2px 10px;
}}

/* ── Callouts ─────────────────────────────────────────────── */
.callout {{
    border-radius: 10px; padding: 13px 16px; margin: 6px 0 4px 0;
    font-size: 0.84rem; line-height: 1.55; border: 1px solid;
}}
.callout b {{ font-weight: 700; }}
.callout code {{
    font-family: 'JetBrains Mono', monospace; font-size: 0.80rem;
    padding: 1px 5px; border-radius: 4px; background: rgba(255,255,255,0.07);
}}
.callout-ok   {{ background: rgba(34,197,94,0.07);  border-color: rgba(34,197,94,0.28);  color: #C9F2D8; }}
.callout-bad  {{ background: rgba(239,68,68,0.09);  border-color: rgba(239,68,68,0.35);  color: #FBD5D5; }}
.callout-warn {{ background: rgba(245,158,11,0.07); border-color: rgba(245,158,11,0.30); color: #FDE9C8; }}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {{ gap: 4px; border-bottom: 1px solid {BORDER}; }}
.stTabs [data-baseweb="tab"] {{
    height: 40px; padding: 0 18px; background: transparent;
    border-radius: 8px 8px 0 0; color: {MUTED};
    font-size: 0.84rem; font-weight: 600;
}}
.stTabs [aria-selected="true"] {{
    background: {PANEL_2}; color: {TEXT};
    border: 1px solid {BORDER}; border-bottom: 2px solid {GOLD};
}}

/* Dataframe */
[data-testid="stDataFrame"] {{ border: 1px solid {BORDER}; border-radius: 10px; }}

/* Inputs */
.stSlider [data-baseweb="slider"] div[role="slider"] {{ background: {GOLD}; }}
.stSelectbox div[data-baseweb="select"] > div,
.stNumberInput div[data-baseweb="input"] > div {{
    background: {PANEL_2}; border-color: {BORDER};
}}
.stDownloadButton button {{
    background: linear-gradient(135deg, {GOLD}, #D97706);
    color: #1A1200; border: 0; font-weight: 700; border-radius: 8px;
}}
.stDownloadButton button:hover {{ filter: brightness(1.08); }}

/* Footer note */
.foot {{
    margin-top: 26px; padding-top: 14px; border-top: 1px solid {BORDER};
    font-size: 0.72rem; color: {MUTED}; line-height: 1.6;
}}
</style>
"""


# ── Logo ─────────────────────────────────────────────────────────────

_ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")


def logo_html() -> str:
    """The official BPCL mark if supplied, otherwise a neutral placeholder.

    Drop the real artwork at `assets/bpcl_logo.png` and it is picked up
    automatically. We do NOT ship a facsimile of the BPCL logo — the fallback
    below is a generic pipeline emblem, clearly not a corporate mark, so the
    dashboard never misrepresents itself as officially branded.
    """
    for ext in ("png", "jpg", "jpeg", "svg"):
        path = os.path.join(_ASSETS, f"bpcl_logo.{ext}")
        if os.path.exists(path):
            mime = "svg+xml" if ext == "svg" else ext
            with open(path, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode()
            return f'<img src="data:image/{mime};base64,{b64}" alt="BPCL"/>'

    # Fallback emblem: a stylised pipeline cross-section. Not a corporate logo.
    return f"""
    <svg width="46" height="46" viewBox="0 0 46 46" fill="none">
      <rect x="1" y="1" width="44" height="44" rx="11"
            fill="url(#g)" stroke="{BORDER}"/>
      <defs>
        <linearGradient id="g" x1="0" y1="0" x2="46" y2="46">
          <stop offset="0%" stop-color="#1E3A8A"/>
          <stop offset="100%" stop-color="#0B1220"/>
        </linearGradient>
      </defs>
      <circle cx="23" cy="23" r="12.5" stroke="{GOLD}" stroke-width="2.2" fill="none"/>
      <circle cx="23" cy="23" r="6.5"  stroke="{CYAN}" stroke-width="1.6"
              fill="rgba(45,212,191,0.13)"/>
      <path d="M4 23 H10 M36 23 H42" stroke="{GOLD}" stroke-width="2.6"
            stroke-linecap="round"/>
    </svg>"""


def masthead(subtitle: str, pills: list) -> str:
    pill_html = "".join(
        f'<span class="pill pill-{kind}"><span class="dot"></span>{text}</span>'
        for kind, text in pills
    )
    return f"""
    <div class="masthead">
      <div class="logo">{logo_html()}</div>
      <div class="rule"></div>
      <div class="titles">
        <h1>MMBL Pipeline <span class="accent">Thermal-Hydraulic Simulator</span></h1>
        <div class="sub">{subtitle}</div>
      </div>
      <div class="pills">{pill_html}</div>
    </div>"""


def rail_label(text: str) -> str:
    return f'<div class="rail-label"><span class="lamp"></span>{text}</div>'


def readout(
    key: str, value: str, unit: str = "", sub: str = "", accent: str = GOLD, glyph: str = ""
) -> str:
    """One face of the console rail: what is currently loaded into the model.

    The popover rendered directly beneath it in app.py supplies the controls,
    and CSS welds the two into a single instrument.
    """
    g = f"{glyph} " if glyph else ""
    u = f'<span class="u">{unit}</span>' if unit else ""
    s = f'<div class="s">{sub}</div>' if sub else ""
    return f"""
    <div class="rdo" style="--accent:{accent}">
      <div class="k">{g}{key}</div>
      <div class="v">{value}{u}</div>
      {s}
    </div>"""


def kpi(
    label: str, value: str, unit: str = "", delta: str = "", trend: str = "none", accent: str = GOLD
) -> str:
    """One KPI card. `trend` in {up, down, flat, none} colours the delta."""
    d = f'<div class="delta {trend}">{delta}</div>' if delta else ""
    u = f'<span class="unit">{unit}</span>' if unit else ""
    return f"""
    <div class="kpi" style="--accent:{accent}">
      <div class="label">{label}</div>
      <div class="value">{value}{u}</div>
      {d}
    </div>"""


def section(title: str, hint: str = "") -> str:
    h = f'<div class="hint">{hint}</div>' if hint else ""
    return f'<div class="section"><div class="bar"></div><h3>{title}</h3>{h}</div>'


def callout(kind: str, body: str) -> str:
    return f'<div class="callout callout-{kind}">{body}</div>'
