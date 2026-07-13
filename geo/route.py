"""
Route — the pipeline as data
=============================

Loads `data/route/waypoints.csv` and exposes the pipeline geometry as a set of
continuous functions of chainage x:

    T_soil is NOT here (that is environment/); this module is pure geometry.

    elevation(x)   [m]     ground elevation      -> rho*g*dz in the momentum eq.
    D_inner(x)     [m]     internal diameter     -> Darcy-Weisbach, area
    D_outer(x)     [m]     external diameter     -> soil heat transfer
    area(x)        [m2]    internal cross-section
    roughness(x)   [m]     absolute roughness    -> friction factor
    burial(x)      [m]     depth to pipe centre  -> soil thermal resistance

Why this module exists
----------------------
The route used to be a hardcoded Python list in config.py. That made "extend the
model to Bina" a code change, and "model the whole of MMBL" a rewrite. With the
route as data, both are edits to a CSV.

Interpolation convention
------------------------
Geographic position is interpolated along WGS-84 geodesics between waypoints
(Karney 2013). Scalar attributes that describe the *pipe* (diameter, wall,
roughness, burial) are piecewise-constant, held from each waypoint forward —
a pipe does not smoothly change diameter. Elevation, which describes the
*ground*, is linearly interpolated, because terrain does vary continuously.

Source: geographiclib (Karney, 2013) for WGS-84 geodesic interpolation.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd
from geographiclib.geodesic import Geodesic

INCH_TO_M = 0.0254
MM_TO_M = 1.0e-3

DEFAULT_ROUTE_CSV = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "route",
    "waypoints.csv",
)


@dataclass(frozen=True)
class Waypoint:
    """A named point on the route, carrying the pipe spec from here forward."""

    waypoint_id: int
    name: str
    lat: float
    lon: float
    chainage_km: float
    elevation_m: float
    od_inch: float
    wall_thickness_mm: float
    roughness_mm: float
    burial_depth_m: float
    station_type: str
    source: str

    @property
    def d_outer_m(self) -> float:
        return self.od_inch * INCH_TO_M

    @property
    def d_inner_m(self) -> float:
        return self.d_outer_m - 2.0 * self.wall_thickness_mm * MM_TO_M

    @property
    def roughness_m(self) -> float:
        return self.roughness_mm * MM_TO_M


@dataclass
class RoutePoint:
    """A sample point on the dense centreline."""

    km: float
    lat: float
    lon: float
    elevation_m: float
    d_outer_m: float
    d_inner_m: float
    area_m2: float
    roughness_m: float
    burial_depth_m: float
    waypoint_name: Optional[str] = None


class Route:
    """The pipeline geometry, as continuous functions of chainage."""

    def __init__(self, waypoints: List[Waypoint], resolution_km: float = 1.0):
        if len(waypoints) < 2:
            raise ValueError("A route needs at least two waypoints")

        self.waypoints = sorted(waypoints, key=lambda w: w.chainage_km)
        self.resolution_km = resolution_km

        km = np.array([w.chainage_km for w in self.waypoints])
        if not np.all(np.diff(km) > 0):
            raise ValueError(f"chainage_km must be strictly increasing; got {list(km)}")

        self._wp_km = km
        self._wp_elev = np.array([w.elevation_m for w in self.waypoints])

        self.points: List[RoutePoint] = self._build_centerline()

    # ── construction ────────────────────────────────────────────────

    @classmethod
    def from_csv(cls, path: str = DEFAULT_ROUTE_CSV, resolution_km: float = 1.0) -> "Route":
        df = pd.read_csv(path)

        required = {
            "waypoint_id",
            "name",
            "lat",
            "lon",
            "chainage_km",
            "elevation_m",
            "od_inch",
            "wall_thickness_mm",
            "roughness_mm",
            "burial_depth_m",
            "station_type",
            "source",
        }
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"{path} is missing columns: {sorted(missing)}")

        if df.isna().any().any():
            bad = df[df.isna().any(axis=1)]["name"].tolist()
            raise ValueError(f"{path} has blank cells for waypoints: {bad}")

        wps = [Waypoint(**row) for row in df.to_dict("records")]
        return cls(wps, resolution_km=resolution_km)

    def _segment_index(self, km: float) -> int:
        """Index of the waypoint whose pipe spec governs this chainage.

        Pipe attributes are held from a waypoint forward, so this is the last
        waypoint at or before `km`.
        """
        idx = int(np.searchsorted(self._wp_km, km, side="right") - 1)
        return min(max(idx, 0), len(self.waypoints) - 1)

    def _build_centerline(self) -> List[RoutePoint]:
        geod = Geodesic.WGS84
        points: List[RoutePoint] = []

        # Half-open sampling [start, end) per segment, then the terminal
        # waypoint once — so no chainage is ever emitted twice.
        for i in range(len(self.waypoints) - 1):
            a, b = self.waypoints[i], self.waypoints[i + 1]
            seg_km = b.chainage_km - a.chainage_km

            line = geod.InverseLine(a.lat, a.lon, b.lat, b.lon)
            geo_len_m = line.s13

            n = max(1, int(round(seg_km / self.resolution_km)))
            for j in range(n):
                frac = j / n
                km = a.chainage_km + frac * seg_km
                pos = line.Position(frac * geo_len_m)

                points.append(
                    RoutePoint(
                        km=round(km, 4),
                        lat=round(pos["lat2"], 6),
                        lon=round(pos["lon2"], 6),
                        elevation_m=float(np.interp(km, self._wp_km, self._wp_elev)),
                        d_outer_m=a.d_outer_m,
                        d_inner_m=a.d_inner_m,
                        area_m2=math.pi * (a.d_inner_m / 2.0) ** 2,
                        roughness_m=a.roughness_m,
                        burial_depth_m=a.burial_depth_m,
                        waypoint_name=a.name if j == 0 else None,
                    )
                )

        last = self.waypoints[-1]
        points.append(
            RoutePoint(
                km=round(last.chainage_km, 4),
                lat=round(last.lat, 6),
                lon=round(last.lon, 6),
                elevation_m=last.elevation_m,
                d_outer_m=last.d_outer_m,
                d_inner_m=last.d_inner_m,
                area_m2=math.pi * (last.d_inner_m / 2.0) ** 2,
                roughness_m=last.roughness_m,
                burial_depth_m=last.burial_depth_m,
                waypoint_name=last.name,
            )
        )

        kms = [p.km for p in points]
        if len(set(kms)) != len(kms):
            dupes = sorted({k for k in kms if kms.count(k) > 1})
            raise AssertionError(f"duplicate chainage on centreline: {dupes}")

        return points

    # ── continuous geometry, as functions of x ──────────────────────

    @property
    def length_km(self) -> float:
        return float(self._wp_km[-1])

    def elevation(self, km: float) -> float:
        """Ground elevation [m]. Linearly interpolated — terrain is continuous."""
        return float(np.interp(km, self._wp_km, self._wp_elev))

    def elevation_gradient(self, km: float, dx_km: float = 0.5) -> float:
        """dz/dx [m/m] — the slope that drives the rho*g*dz pressure term."""
        lo = max(0.0, km - dx_km)
        hi = min(self.length_km, km + dx_km)
        if hi <= lo:
            return 0.0
        return (self.elevation(hi) - self.elevation(lo)) / ((hi - lo) * 1000.0)

    def d_inner(self, km: float) -> float:
        return self.waypoints[self._segment_index(km)].d_inner_m

    def d_outer(self, km: float) -> float:
        return self.waypoints[self._segment_index(km)].d_outer_m

    def area(self, km: float) -> float:
        """Internal cross-sectional area [m2]."""
        return math.pi * (self.d_inner(km) / 2.0) ** 2

    def roughness(self, km: float) -> float:
        return self.waypoints[self._segment_index(km)].roughness_m

    def burial_depth(self, km: float) -> float:
        return self.waypoints[self._segment_index(km)].burial_depth_m

    # ── convenience ─────────────────────────────────────────────────

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([p.__dict__ for p in self.points])

    def waypoint_frame(self) -> pd.DataFrame:
        return pd.DataFrame([w.__dict__ for w in self.waypoints])

    def linefill_m3(self) -> float:
        """Geometric line fill [m3] — total internal volume of the pipe."""
        df = self.to_frame()
        # Trapezoidal integration of A(x) dx.
        return float(np.trapezoid(df["area_m2"].values, df["km"].values * 1000.0))

    def __repr__(self) -> str:
        return (
            f"Route({self.waypoints[0].name} -> {self.waypoints[-1].name}, "
            f"{self.length_km:.0f} km, {len(self.points)} points)"
        )


if __name__ == "__main__":
    route = Route.from_csv()
    print(route)
    print(f"  line fill      : {route.linefill_m3():,.0f} m3")
    print(
        f"  elevation span : {min(w.elevation_m for w in route.waypoints):.0f} – "
        f"{max(w.elevation_m for w in route.waypoints):.0f} m"
    )
    print()
    print(f"  {'km':>7}  {'name':<22} {'elev':>6} {'OD in':>6} {'ID m':>7}")
    for w in route.waypoints:
        print(
            f"  {w.chainage_km:7.1f}  {w.name:<22} {w.elevation_m:6.0f} "
            f"{w.od_inch:6.1f} {w.d_inner_m:7.4f}"
        )
