"""
Open-Meteo — current air temperature, no API key required
==========================================================

A drop-in second source for the current air temperature at Bina and Bijwasan
(`config.AIR_STATIONS`), used when OpenWeather is unavailable — most often
because no `OPENWEATHER_API_KEY` is configured, but equally when the key is
rate-limited (HTTP 429) or not yet activated (HTTP 401, for the first ~10
minutes after a free key is issued).

Why this exists
---------------
The requirement is *real-time measured air temperature at these coordinates*.
That requirement is about the DATA, not about the vendor. Losing the vendor
should not silently downgrade the model to a climatology or, worse, to an
invented number — so when OpenWeather cannot answer, the honest move is to ask
a different real-time source, and to say on screen which one answered.

    >>> snap = fetch_all_station_air_temperatures()   # doctest: +SKIP
    >>> snap.provider                                 # doctest: +SKIP
    'open-meteo'

Why Open-Meteo specifically
---------------------------
  - No API key, no account, no activation delay. Free for non-commercial use.
  - One request serves both stations: `latitude` and `longitude` accept
    comma-separated lists and the response is an array in the same order — one
    round trip instead of one per station.
  - Returns °C by default, so — as with `units=metric` — no conversion is
    applied here.

Provenance caveat, stated plainly
---------------------------------
Open-Meteo's `current` block is the current hour of a numerical weather
analysis (ECMWF/DWD/GFS, whichever covers the point best), interpolated to the
requested coordinate. OpenWeather's `main.temp` is closer to a station
observation. Both are *current* values for *now* — neither is a forecast of a
future time, which is what the specification rules out — but they are not the
same kind of measurement, and the dashboard labels which one it used.

Grid snapping: the response echoes back the model grid cell actually used,
which sits within ~0.05° of the requested point. Those echoed coordinates are
recorded so the offset is auditable rather than hidden.

Source: https://open-meteo.com/en/docs
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Dict, Optional, Tuple

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import (
    AIR_STATIONS,
    AIR_TIMEOUT_S,
)
from data.openweather import AirTemperatureSnapshot, OpenWeatherError

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# The field carrying 2-metre air temperature in the `current` block.
CURRENT_VAR = "temperature_2m"

PROVIDER_NAME = "open-meteo"


def get_current_air_temperature(
    lat: float,
    lon: float,
    timeout_s: float = AIR_TIMEOUT_S,
    session: Optional[requests.Session] = None,
) -> float:
    """Current air temperature [°C] at (lat, lon) from Open-Meteo.

    Signature-compatible with `data.openweather.get_current_air_temperature`,
    minus the API key, so the two providers are interchangeable at the call
    site.

    Raises
    ------
    OpenWeatherError
        On timeout, connection failure, non-200 status, malformed JSON, or a
        response with no numeric current temperature. Never returns a fallback.
    """
    temps, _ = _request({"Point": (lat, lon)}, timeout_s=timeout_s, session=session)
    if "Point" not in temps:
        raise OpenWeatherError(f"Open-Meteo returned no current temperature for ({lat}, {lon})")
    return temps["Point"]


def _request(
    stations: Dict[str, Tuple[float, float]],
    timeout_s: float,
    session: Optional[requests.Session] = None,
) -> Tuple[Dict[str, float], str]:
    """One batched request for every station. Returns (temps, observed_at)."""
    names = list(stations)
    params = {
        "latitude": ",".join(f"{stations[n][0]}" for n in names),
        "longitude": ",".join(f"{stations[n][1]}" for n in names),
        "current": CURRENT_VAR,
        "timezone": "UTC",
    }

    getter = session.get if session is not None else requests.get
    try:
        resp = getter(OPEN_METEO_URL, params=params, timeout=timeout_s)
    except requests.Timeout as exc:
        raise OpenWeatherError(f"Open-Meteo timed out after {timeout_s:g} s") from exc
    except requests.RequestException as exc:
        raise OpenWeatherError(f"Open-Meteo request failed: {exc}") from exc

    if resp.status_code != 200:
        detail = ""
        try:
            detail = str(resp.json().get("reason", ""))
        except ValueError:
            detail = resp.text[:200]
        raise OpenWeatherError(
            f"Open-Meteo returned HTTP {resp.status_code}{': ' + detail if detail else ''}"
        )

    try:
        payload = resp.json()
    except ValueError as exc:
        raise OpenWeatherError("Open-Meteo returned non-JSON") from exc

    # A single coordinate yields an object; several yield an array. Normalise.
    entries = payload if isinstance(payload, list) else [payload]
    if len(entries) != len(names):
        raise OpenWeatherError(
            f"Open-Meteo returned {len(entries)} entries for {len(names)} stations — "
            f"cannot map readings to stations without guessing, so none are used."
        )

    temps: Dict[str, float] = {}
    observed_at = ""
    for name, entry in zip(names, entries):
        current = entry.get("current") or {}
        value = current.get(CURRENT_VAR)
        if value is None:
            logger.error("Open-Meteo: no %s for %s", CURRENT_VAR, name)
            continue
        try:
            temps[name] = float(value)
        except (TypeError, ValueError):
            logger.error("Open-Meteo: non-numeric %s for %s: %r", CURRENT_VAR, name, value)
            continue

        observed_at = observed_at or str(current.get("time", ""))

        # Record how far the model grid cell sits from the requested point.
        req_lat, req_lon = stations[name]
        got_lat, got_lon = entry.get("latitude"), entry.get("longitude")
        if got_lat is not None and got_lon is not None:
            drift = max(abs(got_lat - req_lat), abs(got_lon - req_lon))
            if drift > 0.15:
                logger.warning(
                    "Open-Meteo grid cell for %s is %.2f deg from the requested point "
                    "(%.4f,%.4f -> %.4f,%.4f)",
                    name,
                    drift,
                    req_lat,
                    req_lon,
                    got_lat,
                    got_lon,
                )

    return temps, observed_at


def fetch_all_station_air_temperatures(
    stations: Optional[Dict[str, Tuple[float, float]]] = None,
    timeout_s: float = AIR_TIMEOUT_S,
) -> AirTemperatureSnapshot:
    """Current air temperature at every station, in one request.

    Returns the same `AirTemperatureSnapshot` the OpenWeather path returns, so
    the kernel and the dashboard cannot tell the two apart except by reading
    `snapshot.provider` — which is exactly what they display.

    A total failure (timeout, outage) marks every station as failed and returns
    no temperatures. Nothing is estimated.
    """
    stations = stations if stations is not None else AIR_STATIONS
    snapshot = AirTemperatureSnapshot(provider=PROVIDER_NAME)

    try:
        temps, observed_at = _request(stations, timeout_s=timeout_s)
    except OpenWeatherError as exc:
        logger.error("Open-Meteo unavailable: %s", exc)
        snapshot.failures = {name: str(exc) for name in stations}
        return snapshot

    snapshot.temperatures = temps
    snapshot.observed_at = observed_at
    snapshot.failures = {
        name: "Open-Meteo returned no current temperature for this point"
        for name in stations
        if name not in temps
    }

    for name, temp in temps.items():
        logger.info("%s: current air temperature %.2f °C (open-meteo)", name, temp)
    if snapshot.failures:
        logger.warning(
            "Open-Meteo: %d of %d stations returned nothing: %s",
            len(snapshot.failures),
            len(stations),
            ", ".join(sorted(snapshot.failures)),
        )

    return snapshot


# ─── Manual check: python data/open_meteo.py ─────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    snap = fetch_all_station_air_temperatures()
    print("\nOpen-Meteo current air temperature — Bina and Bijwasan (no API key)")
    print("=" * 68)
    print(f"  {'station':<18} {'lat':>9} {'lon':>9} {'T_air [C]':>11}")
    for station, (la, lo) in AIR_STATIONS.items():
        t = snap.temperatures.get(station)
        print(f"  {station:<18} {la:9.4f} {lo:9.4f} " + (f"{t:11.2f}" if t is not None else f"{'FAILED':>11}"))
    print(f"\n  observed at: {snap.observed_at}Z   complete: {snap.complete}")
