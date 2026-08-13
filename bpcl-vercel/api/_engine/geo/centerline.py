"""
Geodesic Pipeline Centerline Sampling
======================================

Generates a continuous set of sample points along the Kota–Bijwasan
pipeline centerline using geodesic (great-circle) interpolation
between successive waypoints.

Waypoints define the piecewise-linear route. Between each pair of
consecutive waypoints, intermediate points are generated at the
configured spatial resolution (default 1 km). This yields ~360
sample points for the full 360 km route.

Each sample point carries:
  - km          : cumulative distance from Kota [km]
  - lat, lon    : geographic coordinates [degrees]
  - segment_idx : index of the pipe segment it belongs to
  - D_outer_m   : local outer diameter [m]
  - D_inner_m   : local inner diameter [m]

Source: geographiclib library (Karney, 2013) for WGS-84 geodesic.
"""

import os
import sys
from dataclasses import dataclass
from typing import List

from geographiclib.geodesic import Geodesic

# Allow imports from parent package
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import (
    CENTERLINE_RESOLUTION_KM,
    WAYPOINTS,
    get_pipe_segment,
)


@dataclass
class CenterlinePoint:
    """A single sample point on the pipeline centerline.

    Attributes
    ----------
    km : float
        Cumulative distance from Kota (dispatch) [km].
    lat : float
        Latitude [degrees North].
    lon : float
        Longitude [degrees East].
    D_outer_m : float
        Local pipe outer diameter [m].
    D_inner_m : float
        Local pipe inner diameter [m].
    waypoint_name : str or None
        Name if this point coincides with a waypoint, else None.
    """

    km: float
    lat: float
    lon: float
    D_outer_m: float
    D_inner_m: float
    waypoint_name: str = None


def generate_centerline(
    resolution_km: float = CENTERLINE_RESOLUTION_KM,
) -> List[CenterlinePoint]:
    """Generate pipeline centerline sample points.

    Interpolates between successive waypoints using WGS-84 geodesic
    lines at the given spatial resolution.

    Parameters
    ----------
    resolution_km : float, optional
        Distance between successive sample points [km].
        Default: ``CENTERLINE_RESOLUTION_KM`` from config (1.0 km).

    Returns
    -------
    list of CenterlinePoint
        Ordered list of sample points from Kota (km=0) to
        Bijwasan (km≈360).
    """
    geod = Geodesic.WGS84
    points: List[CenterlinePoint] = []

    # Each segment emits its start waypoint and every interior sample, but NOT
    # its end waypoint — that is the next segment's start. The terminal
    # waypoint is appended once, after the loop.
    #
    # The previous implementation emitted j in range(n_samples + 1), i.e. it
    # included the end point, and its de-duplication guard
    #     if km_rounded in seen_kms and j > 0: continue
    # exempted j == 0 — precisely the point that was already emitted as the
    # previous segment's endpoint. Every interior waypoint therefore appeared
    # twice (km 40, 110, 210, 250, 330, 340), giving 367 points for a 360 km
    # route. Harmless to interp1d, but it corrupts any FIFO or line-fill index
    # built on centerline position.
    for i in range(len(WAYPOINTS) - 1):
        wp_start = WAYPOINTS[i]
        wp_end = WAYPOINTS[i + 1]

        inv = geod.Inverse(wp_start.lat, wp_start.lon, wp_end.lat, wp_end.lon)
        geodesic_dist_m = inv["s12"]  # [m]
        segment_km = wp_end.km - wp_start.km  # route km (may differ from geodesic)

        line = geod.InverseLine(wp_start.lat, wp_start.lon, wp_end.lat, wp_end.lon)

        n_samples = max(1, int(round(segment_km / resolution_km)))

        for j in range(n_samples):  # half-open: [start, end)
            frac = j / n_samples
            km_val = wp_start.km + frac * segment_km

            pos = line.Position(frac * geodesic_dist_m)
            pipe_seg = get_pipe_segment(km_val)

            points.append(
                CenterlinePoint(
                    km=round(km_val, 3),
                    lat=round(pos["lat2"], 6),
                    lon=round(pos["lon2"], 6),
                    D_outer_m=pipe_seg.outer_diameter_m,
                    D_inner_m=pipe_seg.inner_diameter_m,
                    waypoint_name=wp_start.name if j == 0 else None,
                )
            )

    # Terminal waypoint, emitted exactly once.
    wp_last = WAYPOINTS[-1]
    pipe_seg = get_pipe_segment(wp_last.km)
    points.append(
        CenterlinePoint(
            km=round(wp_last.km, 3),
            lat=round(wp_last.lat, 6),
            lon=round(wp_last.lon, 6),
            D_outer_m=pipe_seg.outer_diameter_m,
            D_inner_m=pipe_seg.inner_diameter_m,
            waypoint_name=wp_last.name,
        )
    )

    points.sort(key=lambda p: p.km)

    kms = [p.km for p in points]
    if len(set(kms)) != len(kms):
        dupes = sorted({k for k in kms if kms.count(k) > 1})
        raise AssertionError(f"centerline emitted duplicate chainage: {dupes}")

    return points


def centerline_to_dict_list(points: List[CenterlinePoint]) -> list:
    """Convert centerline points to a list of dicts (for DataFrame).

    Parameters
    ----------
    points : list of CenterlinePoint
        Centerline sample points.

    Returns
    -------
    list of dict
        Each dict has keys: km, lat, lon, D_outer_m, D_inner_m, waypoint_name.
    """
    return [
        {
            "km": p.km,
            "lat": p.lat,
            "lon": p.lon,
            "D_outer_m": p.D_outer_m,
            "D_inner_m": p.D_inner_m,
            "waypoint_name": p.waypoint_name,
        }
        for p in points
    ]


# ─── Quick validation when run directly ─────────────────────────
if __name__ == "__main__":
    pts = generate_centerline()
    print(f"Generated {len(pts)} centerline points")
    print(f"  First: km={pts[0].km}, ({pts[0].lat}, {pts[0].lon}) — {pts[0].waypoint_name}")
    print(f"  Last:  km={pts[-1].km}, ({pts[-1].lat}, {pts[-1].lon}) — {pts[-1].waypoint_name}")
    print(f"  Total km span: {pts[-1].km - pts[0].km:.1f} km")

    # Show waypoints
    wps = [p for p in pts if p.waypoint_name]
    print(f"\n  Waypoints found: {len(wps)}")
    for wp in wps:
        print(f"    km {wp.km:6.1f}: {wp.waypoint_name} ({wp.lat:.4f}°N, {wp.lon:.4f}°E)")
