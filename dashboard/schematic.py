"""
Pipeline schematic — the SCADA-style mimic diagram
===================================================

Renders the route as a horizontal pipe whose colour IS the product temperature,
with station nodes above it and the terrain profile below.

Design intent
-------------
Colour carries the primary variable (temperature) so the operator reads the
thermal state of the whole line at a glance, without consulting a chart. Terrain
sits underneath because elevation is what drives the static-head term in the
momentum equation — the two belong on the same axis.

Bina is drawn as a GHOST node. The route data does not yet include it (ERA5
coverage stops short of 24.19 N, 78.20 E), so it is shown greyed and explicitly
labelled as not modelled. Drawing it as if it were simulated would be a lie told
in pixels.
"""

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from dashboard.theme import (
    CYAN,
    GOLD,
    GRID,
    MUTED,
    PANEL_2,
    PLOT_LAYOUT,
    TEMP_SCALE,
    TEXT,
)

# Stations upstream of the modelled route. Chainage is negative: the numbers are
# indicative only, and nothing is computed for them.
GHOST_UPSTREAM = [("Bina", -260.0)]


def _sample_colour(scale, t):
    """Linear interpolation into a plotly colourscale -> 'rgb(r,g,b)'."""
    t = float(np.clip(t, 0.0, 1.0))
    for i in range(len(scale) - 1):
        p0, c0 = scale[i]
        p1, c1 = scale[i + 1]
        if p0 <= t <= p1:
            f = 0.0 if p1 == p0 else (t - p0) / (p1 - p0)
            a = tuple(int(c0.lstrip("#")[j : j + 2], 16) for j in (0, 2, 4))
            b = tuple(int(c1.lstrip("#")[j : j + 2], 16) for j in (0, 2, 4))
            rgb = tuple(int(round(a[k] + f * (b[k] - a[k]))) for k in range(3))
            return f"rgb{rgb}"
    return scale[-1][1]


def pipeline_schematic(df, route, n_segments: int = 90) -> go.Figure:
    """Mimic diagram: temperature-coloured pipe + station nodes + terrain."""
    km = df["km"].values
    T = df["T_C"].values
    T_soil = df["T_soil_C"].values

    # Normalise temperature against the full span shown, so colour is readable
    # even when the batch barely moves.
    lo = float(min(T.min(), T_soil.min())) - 0.5
    hi = float(max(T.max(), T_soil.max())) + 0.5
    span = max(hi - lo, 1e-6)

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.62, 0.38],
        vertical_spacing=0.04,
        subplot_titles=("", ""),
    )

    # ── The pipe: many short segments, each coloured by its mean temperature
    edges = np.linspace(km[0], km[-1], n_segments + 1)
    for i in range(n_segments):
        m = (km >= edges[i]) & (km <= edges[i + 1])
        if not m.any():
            continue
        t_mean = float(T[m].mean())
        fig.add_trace(
            go.Scatter(
                x=[edges[i], edges[i + 1]],
                y=[0, 0],
                mode="lines",
                line=dict(color=_sample_colour(TEMP_SCALE, (t_mean - lo) / span), width=26),
                hoverinfo="skip",
                showlegend=False,
            ),
            row=1,
            col=1,
        )

    # Invisible hover ribbon so every km is inspectable
    fig.add_trace(
        go.Scatter(
            x=km,
            y=np.zeros_like(km),
            mode="lines",
            line=dict(color="rgba(0,0,0,0)", width=30),
            hovertemplate=(
                "<b>km %{x:.0f}</b><br>"
                "Product  %{customdata[0]:.2f} °C<br>"
                "Soil     %{customdata[1]:.2f} °C<br>"
                "Pressure %{customdata[2]:.1f} bar<br>"
                "Gross V  %{customdata[3]:.1f} KL"
                "<extra></extra>"
            ),
            customdata=np.column_stack([T, T_soil, df["P_bar"], df["V_gross_KL"]]),
            showlegend=False,
        ),
        row=1,
        col=1,
    )

    # Colour-bar proxy
    fig.add_trace(
        go.Scatter(
            x=[None],
            y=[None],
            mode="markers",
            marker=dict(
                colorscale=TEMP_SCALE,
                cmin=lo,
                cmax=hi,
                size=0.1,
                color=[lo],
                showscale=True,
                colorbar=dict(
                    title=dict(text="Product<br>T [°C]", font=dict(size=10, color=MUTED)),
                    thickness=10,
                    len=0.55,
                    y=0.76,
                    outlinewidth=0,
                    tickfont=dict(size=9),
                ),
            ),
            hoverinfo="skip",
            showlegend=False,
        ),
        row=1,
        col=1,
    )

    # ── Ghost upstream (Bina) — drawn, but explicitly not modelled
    for name, gkm in GHOST_UPSTREAM:
        fig.add_trace(
            go.Scatter(
                x=[gkm, km[0]],
                y=[0, 0],
                mode="lines",
                line=dict(color="rgba(131,151,184,0.32)", width=20, dash="dot"),
                hovertemplate=(
                    f"<b>{name} → {route.waypoints[0].name}</b><br>"
                    "Not modelled — ERA5 coverage does not reach Bina"
                    "<extra></extra>"
                ),
                showlegend=False,
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=[gkm],
                y=[0],
                mode="markers+text",
                marker=dict(
                    symbol="square", size=15, color=PANEL_2, line=dict(color=MUTED, width=1.5)
                ),
                text=[f"{name}<br><i>awaiting data</i>"],
                textposition="bottom center",
                textfont=dict(size=9, color=MUTED),
                hoverinfo="skip",
                showlegend=False,
            ),
            row=1,
            col=1,
        )

    # ── Station nodes
    wp = df[df["waypoint_name"].notna()]
    labels = [n.split(" (")[0] for n in wp["waypoint_name"]]

    fig.add_trace(
        go.Scatter(
            x=wp["km"],
            y=np.zeros(len(wp)),
            mode="markers",
            marker=dict(symbol="diamond", size=17, color=PANEL_2, line=dict(color=GOLD, width=2)),
            hovertemplate="<b>%{customdata}</b><br>km %{x:.0f}<extra></extra>",
            customdata=labels,
            showlegend=False,
        ),
        row=1,
        col=1,
    )

    for x, lab, t in zip(wp["km"], labels, wp["T_C"]):
        fig.add_annotation(
            x=x,
            y=0.40,
            xref="x",
            yref="y",
            text=f"<b>{lab}</b><br><span style='color:{GOLD}'>{t:.1f} °C</span>",
            showarrow=False,
            font=dict(size=9.5, color=TEXT),
            align="center",
            row=1,
            col=1,
        )

    # ── Terrain profile
    fig.add_trace(
        go.Scatter(
            x=km,
            y=df["elevation_m"],
            mode="lines",
            line=dict(color="rgba(131,151,184,0.85)", width=1.6),
            fill="tozeroy",
            fillcolor="rgba(45,64,102,0.42)",
            name="Ground elevation",
            hovertemplate="km %{x:.0f}<br>elevation %{y:.0f} m<extra></extra>",
            showlegend=False,
        ),
        row=2,
        col=1,
    )

    fig.add_trace(
        go.Scatter(
            x=wp["km"],
            y=wp["elevation_m"],
            mode="markers",
            marker=dict(size=6, color=CYAN),
            hoverinfo="skip",
            showlegend=False,
        ),
        row=2,
        col=1,
    )

    fig.update_yaxes(
        range=[-1.1, 1.1], showticklabels=False, showgrid=False, zeroline=False, row=1, col=1
    )
    fig.update_yaxes(
        title_text="Elevation [m]",
        row=2,
        col=1,
        **GRID,
        title_font=dict(size=10, color=MUTED),
        tickfont=dict(size=9),
    )
    fig.update_xaxes(showgrid=False, zeroline=False, showticklabels=False, row=1, col=1)
    fig.update_xaxes(
        title_text="Chainage from dispatch [km]",
        row=2,
        col=1,
        **GRID,
        title_font=dict(size=10, color=MUTED),
        tickfont=dict(size=9),
    )

    layout = dict(PLOT_LAYOUT)
    layout.update(height=340, margin=dict(l=60, r=20, t=14, b=40), hovermode="closest")
    fig.update_layout(**layout)
    return fig
