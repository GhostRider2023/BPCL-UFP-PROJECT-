"""
Air temperature — provider selection
=====================================

One entry point the rest of the project calls, and one rule about what it may
return.

    fetch_current_air_temperatures()  ->  AirTemperatureSnapshot

It reports the current air temperature at the two terminals — **Bina** and
**Bijwasan**. Those are the only stations whose air enters the physics, so they
are the only ones queried. See `config.AIR_STATIONS`.

Provider order
--------------
  1. **OpenWeather** (`data/openweather.py`) — used whenever an API key is
     configured. This is the specified source: Current Weather API,
     `units=metric`, `main.temp`, one request per station.

  2. **Open-Meteo** (`data/open_meteo.py`) — used when OpenWeather cannot
     answer: no key configured, key not yet activated (HTTP 401 for ~10 min
     after issue), rate limited (429), or the service is down. No API key
     required, one batched request for both stations.

The fallback is deliberately a *second real-time source*, not a model, not a
climatology, and not a remembered value. If both providers fail, the run
proceeds on the ERA5 soil temperature alone and says so — an air temperature is
never invented, which is the one guarantee this whole subsystem exists to make.

`snapshot.provider` records which source answered, and the dashboard prints it
next to the temperatures, so no number on screen has unstated provenance.

Set `AIR_TEMPERATURE_PROVIDER` to pin a provider explicitly:

    AIR_TEMPERATURE_PROVIDER=openweather   # fail rather than fall back
    AIR_TEMPERATURE_PROVIDER=open-meteo    # skip OpenWeather entirely
    AIR_TEMPERATURE_PROVIDER=auto          # default: OpenWeather, else Open-Meteo
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Dict, Optional, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import AIR_STATIONS, AIR_TIMEOUT_S
from data import open_meteo, openweather
from data.openweather import (  # re-exported: the shared vocabulary
    AirTemperatureSnapshot,
    OpenWeatherError,
    build_environment_profile,
    environment_temperature,
)

logger = logging.getLogger(__name__)

PROVIDER_ENV = "AIR_TEMPERATURE_PROVIDER"

__all__ = [
    "AirTemperatureSnapshot",
    "OpenWeatherError",
    "build_environment_profile",
    "environment_temperature",
    "fetch_current_air_temperatures",
    "describe_provider",
]


def _requested_provider() -> str:
    return (os.environ.get(PROVIDER_ENV) or "auto").strip().lower()


def fetch_current_air_temperatures(
    stations: Optional[Dict[str, Tuple[float, float]]] = None,
    timeout_s: float = AIR_TIMEOUT_S,
    provider: Optional[str] = None,
) -> AirTemperatureSnapshot:
    """Current air temperature at every station, from the best available source.

    Parameters
    ----------
    stations : dict, optional
        station name → (lat, lon). Defaults to `config.AIR_STATIONS`
        (Bina and Bijwasan).
    timeout_s : float
        Per-request HTTP timeout.
    provider : {"auto", "openweather", "open-meteo"}, optional
        Overrides the `AIR_TEMPERATURE_PROVIDER` environment variable.

    Returns
    -------
    AirTemperatureSnapshot
        `.provider` names the source that answered. If every provider failed,
        `.temperatures` is empty and `.failures` explains why, per station —
        no estimated value is ever placed in `.temperatures`.
    """
    stations = stations if stations is not None else AIR_STATIONS
    choice = (provider or _requested_provider()).lower()

    if choice == "open-meteo":
        return open_meteo.fetch_all_station_air_temperatures(stations, timeout_s=timeout_s)

    if choice == "openweather":
        # Pinned: report the failure rather than quietly using another source.
        return openweather.fetch_all_station_air_temperatures(stations, timeout_s=timeout_s)

    # auto ─────────────────────────────────────────────────────────
    if openweather.api_key_is_configured():
        snap = openweather.fetch_all_station_air_temperatures(stations, timeout_s=timeout_s)
        if snap.any_data:
            return snap
        logger.warning(
            "OpenWeather returned nothing for any station; falling back to Open-Meteo. "
            "Original failures: %s",
            "; ".join(f"{k}: {v}" for k, v in list(snap.failures.items())[:2]),
        )
    else:
        logger.info(
            "No %s configured — using Open-Meteo, which needs no API key.",
            openweather.OPENWEATHER_API_KEY_ENV
            if hasattr(openweather, "OPENWEATHER_API_KEY_ENV")
            else "OPENWEATHER_API_KEY",
        )

    fallback = open_meteo.fetch_all_station_air_temperatures(stations, timeout_s=timeout_s)
    return fallback


def describe_provider(snapshot: AirTemperatureSnapshot) -> str:
    """One-line, human-readable provenance string for the UI."""
    if snapshot.provider == "open-meteo":
        base = "Open-Meteo current analysis · no API key required"
    else:
        base = "OpenWeather Current Weather · units=metric · main.temp"
    if snapshot.observed_at:
        base += f" · observed {snapshot.observed_at}Z"
    return base


# ─── Manual check: python data/air_temperature.py ────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    snap = fetch_current_air_temperatures()
    print(f"\nprovider: {snap.provider}   ({describe_provider(snap)})")
    print("=" * 70)
    for station, (la, lo) in AIR_STATIONS.items():
        t = snap.temperatures.get(station)
        print(
            f"  {station:<18} {la:9.4f} {lo:9.4f} "
            + (f"{t:8.2f} C" if t is not None else f"{'FAILED':>10}")
        )
    if snap.failures:
        print("\n  failures (no temperature substituted):")
        for k, v in snap.failures.items():
            print(f"    {k:<18} {v}")
    print(f"\n  complete: {snap.complete}")
