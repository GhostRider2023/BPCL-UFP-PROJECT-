"""
OpenWeather — current air temperature at the two terminals
===========================================================

Fetches the **current observed** air temperature at Bina and Bijwasan — the
only two points on the line where the pipe is above ground or at a terminal,
and so the only two where air enters the boundary condition (see
`config.AIR_STATIONS`). It combines that reading with the existing ERA5-Land
soil temperature into the environment temperature the pipeline actually
exchanges heat with:

    T_environment = (T_air_current + T_soil) / 2

What this module will NOT do
----------------------------
It will not forecast, interpolate in time, climatologically infill, or
otherwise manufacture an air temperature. Every value it returns came out of
`main.temp` on a live response. If a station's request fails, that station is
reported as failed — with the HTTP status or exception that caused it — and the
caller decides what to do. A quietly substituted "reasonable" 30 °C would look
exactly like a measurement in the station table, and that is the one failure
mode this module exists to prevent.

    >>> snap = fetch_all_station_air_temperatures()   # doctest: +SKIP
    >>> snap.temperatures["Bina"]                     # doctest: +SKIP
    31.2
    >>> snap.failures                                 # doctest: +SKIP
    {}

API key configuration
---------------------
The key is never stored in source. It is resolved, in order:

  1. ``OPENWEATHER_API_KEY`` environment variable
  2. a ``.env`` file at the project root, line ``OPENWEATHER_API_KEY=...``
  3. Streamlit secrets — ``.streamlit/secrets.toml``, key ``OPENWEATHER_API_KEY``
  4. a plain-text file ``openweather_key.txt`` at the project root

(4) mirrors the convention `data/era5_fetch.py` already uses for the CDS key.
All four are gitignored.

Units
-----
`units=metric` is requested, so `main.temp` is already °C. No conversion is
applied here — applying one would double-correct.

Source: https://openweathermap.org/current
"""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import (
    AIR_STATION_TO_WAYPOINT,
    AIR_STATIONS,
    AIR_TIMEOUT_S,
    OPENWEATHER_API_KEY_ENV,
    OPENWEATHER_UNITS,
    OPENWEATHER_URL,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Re-exported under the name the specification uses, so callers can write
# `from data.openweather import STATIONS`.
STATIONS: Dict[str, Tuple[float, float]] = AIR_STATIONS


class OpenWeatherError(RuntimeError):
    """A station's current temperature could not be obtained.

    Raised rather than returning a default, so that no code path can mistake a
    failure for a measurement.
    """


class MissingAPIKeyError(OpenWeatherError):
    """No OpenWeather API key is configured anywhere this module looks."""


# ═══════════════════════════════════════════════════════════════════
# API key resolution — never hardcoded
# ═══════════════════════════════════════════════════════════════════


def _key_from_dotenv() -> Optional[str]:
    """Read OPENWEATHER_API_KEY from a project-root .env, if present."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return None
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if name.strip() == OPENWEATHER_API_KEY_ENV:
                return value.strip().strip("'\"") or None
    except OSError as exc:
        logger.warning("Could not read %s: %s", env_path, exc)
    return None


def _key_from_streamlit_secrets() -> Optional[str]:
    """Read the key from Streamlit secrets, when running under Streamlit.

    Importing streamlit outside a Streamlit process is harmless, but touching
    `st.secrets` with no secrets.toml raises — hence the broad guard.
    """
    try:
        import streamlit as st  # noqa: PLC0415 — optional, runtime-only dependency

        value = st.secrets.get(OPENWEATHER_API_KEY_ENV)
        return str(value) if value else None
    except Exception:
        return None


def _key_from_file() -> Optional[str]:
    """Read the key from openweather_key.txt at the project root."""
    key_path = PROJECT_ROOT / "openweather_key.txt"
    if not key_path.is_file():
        return None
    try:
        token = key_path.read_text(encoding="utf-8").strip()
        return token or None
    except OSError as exc:
        logger.warning("Could not read %s: %s", key_path, exc)
    return None


def get_api_key() -> str:
    """Resolve the OpenWeather API key, or raise.

    Returns
    -------
    str
        The API key.

    Raises
    ------
    MissingAPIKeyError
        If no key is configured. The message lists every location searched, so
        the fix is obvious from the error alone.
    """
    for source in (
        lambda: os.environ.get(OPENWEATHER_API_KEY_ENV),
        _key_from_dotenv,
        _key_from_streamlit_secrets,
        _key_from_file,
    ):
        key = source()
        if key:
            return key.strip()

    raise MissingAPIKeyError(
        f"No OpenWeather API key found. Set it in one of:\n"
        f"  1. environment variable {OPENWEATHER_API_KEY_ENV}\n"
        f"  2. {PROJECT_ROOT / '.env'}  ->  {OPENWEATHER_API_KEY_ENV}=<key>\n"
        f"  3. {PROJECT_ROOT / '.streamlit' / 'secrets.toml'}  ->  "
        f'{OPENWEATHER_API_KEY_ENV} = "<key>"\n'
        f"  4. {PROJECT_ROOT / 'openweather_key.txt'}  (key on a single line)\n"
        f"Get a free key at https://openweathermap.org/api. The key must never "
        f"be committed to source."
    )


def api_key_is_configured() -> bool:
    """True if a key can be resolved. Never raises — for UI status checks."""
    try:
        get_api_key()
        return True
    except MissingAPIKeyError:
        return False


# ═══════════════════════════════════════════════════════════════════
# The single-station call
# ═══════════════════════════════════════════════════════════════════


def get_current_air_temperature(
    lat: float,
    lon: float,
    api_key: Optional[str] = None,
    timeout_s: float = AIR_TIMEOUT_S,
    session: Optional[requests.Session] = None,
) -> float:
    """Current air temperature [°C] at (lat, lon) from OpenWeather.

    Parameters
    ----------
    lat, lon : float
        WGS-84 coordinates of the station.
    api_key : str, optional
        Resolved from the environment / .env / secrets / key file if omitted.
    timeout_s : float
        HTTP timeout, applied to both connect and read.
    session : requests.Session, optional
        Reused across the stations so one TCP connection serves them all.

    Returns
    -------
    float
        Current temperature in °C, taken verbatim from ``main.temp``.
        `units=metric` is requested, so no unit conversion is applied.

    Raises
    ------
    OpenWeatherError
        On timeout, connection failure, non-200 status, malformed JSON, or a
        response with no ``main.temp``. Never returns a fallback value.
    """
    key = api_key if api_key is not None else get_api_key()

    params = {
        "lat": lat,
        "lon": lon,
        "appid": key,
        "units": OPENWEATHER_UNITS,  # → main.temp is already °C
    }

    getter = session.get if session is not None else requests.get
    try:
        resp = getter(OPENWEATHER_URL, params=params, timeout=timeout_s)
    except requests.Timeout as exc:
        raise OpenWeatherError(
            f"OpenWeather timed out after {timeout_s:g} s for ({lat}, {lon})"
        ) from exc
    except requests.RequestException as exc:
        raise OpenWeatherError(f"OpenWeather request failed for ({lat}, {lon}): {exc}") from exc

    if resp.status_code != 200:
        # 401 = bad/unactivated key, 429 = rate limited. Surface both verbatim;
        # a new key takes ~10 minutes to activate and that is worth saying.
        detail = ""
        try:
            detail = str(resp.json().get("message", ""))
        except ValueError:
            detail = resp.text[:200]
        raise OpenWeatherError(
            f"OpenWeather returned HTTP {resp.status_code} for ({lat}, {lon})"
            f"{': ' + detail if detail else ''}"
        )

    try:
        payload = resp.json()
    except ValueError as exc:
        raise OpenWeatherError(f"OpenWeather returned non-JSON for ({lat}, {lon})") from exc

    try:
        temp_c = float(payload["main"]["temp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise OpenWeatherError(
            f"OpenWeather response for ({lat}, {lon}) has no numeric main.temp: "
            f"{str(payload)[:200]}"
        ) from exc

    return temp_c


# ═══════════════════════════════════════════════════════════════════
# Both stations, once per run
# ═══════════════════════════════════════════════════════════════════


@dataclass
class AirTemperatureSnapshot:
    """One run's worth of current air temperatures.

    Attributes
    ----------
    temperatures : dict
        station name → current air temperature [°C]. Successful stations only.
    failures : dict
        station name → human-readable reason. Stations absent from
        `temperatures`. No temperature is ever fabricated for these.
    fetched_at : float
        Unix time of the fetch, so the UI can show the data's age.
    provider : str
        Which source produced these readings — "openweather" or "open-meteo".
        Carried through to the UI so the provenance of every number on screen
        is visible rather than assumed.
    observed_at : str
        Provider's own timestamp for the observation, when it reports one.
    """

    temperatures: Dict[str, float] = field(default_factory=dict)
    failures: Dict[str, str] = field(default_factory=dict)
    fetched_at: float = field(default_factory=time.time)
    provider: str = "openweather"
    observed_at: str = ""

    @property
    def complete(self) -> bool:
        """True when every configured station returned a temperature."""
        return not self.failures and len(self.temperatures) == len(STATIONS)

    @property
    def any_data(self) -> bool:
        return bool(self.temperatures)

    def by_waypoint(self) -> Dict[str, float]:
        """Same temperatures, re-keyed onto route waypoint names."""
        return {
            AIR_STATION_TO_WAYPOINT[station]: temp
            for station, temp in self.temperatures.items()
            if station in AIR_STATION_TO_WAYPOINT
        }

    def age_s(self) -> float:
        return time.time() - self.fetched_at


def fetch_all_station_air_temperatures(
    stations: Optional[Dict[str, Tuple[float, float]]] = None,
    timeout_s: float = AIR_TIMEOUT_S,
) -> AirTemperatureSnapshot:
    """Fetch the current air temperature at every station, once.

    One request per station, on a shared session, with per-station failure
    isolation: one dead station does not cost the other seven their real data.

    Parameters
    ----------
    stations : dict, optional
        station name → (lat, lon). Defaults to `config.AIR_STATIONS`.
    timeout_s : float
        Per-request HTTP timeout.

    Returns
    -------
    AirTemperatureSnapshot
        Successful readings and, separately, the stations that failed and why.

    Notes
    -----
    A missing API key fails *all* stations with the same message rather than
    raising, so the dashboard can render a single clear "not configured" state
    instead of a traceback.
    """
    stations = stations if stations is not None else STATIONS
    snapshot = AirTemperatureSnapshot()

    try:
        key = get_api_key()
    except MissingAPIKeyError as exc:
        logger.error("%s", exc)
        snapshot.failures = {name: "API key not configured" for name in stations}
        return snapshot

    # One session, one connection, one request per station — no duplicates.
    with requests.Session() as session:
        for name, (lat, lon) in stations.items():
            try:
                temp_c = get_current_air_temperature(
                    lat, lon, api_key=key, timeout_s=timeout_s, session=session
                )
            except OpenWeatherError as exc:
                logger.error("Air temperature unavailable for %s: %s", name, exc)
                snapshot.failures[name] = str(exc)
                continue
            snapshot.temperatures[name] = temp_c
            logger.info("%s: current air temperature %.2f °C", name, temp_c)

    if snapshot.failures:
        logger.warning(
            "OpenWeather: %d of %d stations failed: %s",
            len(snapshot.failures),
            len(stations),
            ", ".join(sorted(snapshot.failures)),
        )

    return snapshot


# ═══════════════════════════════════════════════════════════════════
# Air + soil → environment
# ═══════════════════════════════════════════════════════════════════


def environment_temperature(T_air_C: float, T_soil_C: float) -> float:
    """Mean environmental temperature [°C] seen by the pipe.

        T_environment = (T_air_current + T_soil) / 2

    Both arguments are temperatures of the *surroundings*. The product
    temperature never enters this average — averaging the product with the soil
    would feed the fluid's own state back in as its own boundary condition.

    Examples
    --------
    >>> round(environment_temperature(31.2, 24.93), 4)
    28.065
    """
    return (T_air_C + T_soil_C) / 2.0


def build_environment_profile(
    soil_profile,
    snapshot: AirTemperatureSnapshot,
    blend_stations: Optional[list] = None,
    T_fuel_C: Optional[float] = None,
):
    """Attach `T_air_C` and `T_env_C` to a soil profile frame.

    The environment temperature the energy equation drives toward:

        Bina      (above ground):  T_env = (T_air + T_fuel_dispatch) / 2
        Bijwasan  (buried):        T_env = (T_air + T_soil) / 2
        everywhere else:           T_env = T_soil

    Air is deliberately confined to the two terminals. Between them the pipe is
    buried at 1.2 m for hundreds of kilometres, where the daily air signal does
    not reach and the ERA5 deep-soil temperature is the only correct boundary
    condition. See config.AIR_STATIONS.

    Bina is different again: the line leaves the refinery on SURFACE
    pipework, so the soil is not in contact with it at all. Its environment is
    the open air and the product it carries — hence the fuel temperature rather
    than the soil temperature. See config.AIR_FUEL_BLEND_STATIONS.

    Parameters
    ----------
    soil_profile : pd.DataFrame
        One month's soil profile, with columns `km`, `waypoint_name`,
        `T_soil_C`, `k_soil_WmK`.
    snapshot : AirTemperatureSnapshot
        Current air temperatures, keyed by weather-station name.
    blend_stations : list of str, optional
        Station names whose air temperature may enter the calculation.
        Defaults to `config.AIR_STATIONS` (Bina and Bijwasan).
    T_fuel_C : float, optional
        Temperature the product leaves the refinery at, as entered by the
        operator. Used at the above-ground stations. Without it those rows fall
        back to (air + soil)/2 rather than inventing a fuel temperature.

    Returns
    -------
    pd.DataFrame
        A copy with three extra columns:
          `T_air_C`    — measured current air temperature at the blend
                         stations; NaN everywhere else, including at a blend
                         station whose request failed. Never filled in.
          `T_env_C`    — the boundary temperature actually used.
          `T_env_basis`— provenance: "air+soil", "air+fuel", "soil", "none".

    Why the fuel temperature is admissible at Bina
    ----------------------------------------------
        T_env(Bina) = (T_air + T_fuel) / 2        [basis "air+fuel"]

    Averaging a product temperature into a boundary condition normally feeds
    the fluid's own state back in as its own environment, which is circular.
    It is defensible at exactly one point on this route: the dispatch terminal,
    where T_fuel is an independent MEASUREMENT at the refinery meter rather
    than a quantity the solver produced. Everywhere downstream the product
    temperature is solved, and using it would close that loop — so this is
    restricted to config.AIR_FUEL_BLEND_STATIONS and applied nowhere else.

    Notes
    -----
    The frame carries one row per *station*. Between stations the kernel
    interpolates `T_env_C` linearly along the chainage — the same treatment
    `T_soil_C` has always had, and unavoidable for a continuous ODE boundary
    condition. The station rows themselves are never interpolated: each holds
    exactly what the weather API returned for that station's own coordinates.
    """
    import numpy as np  # noqa: PLC0415 — keeps this module importable without pandas/numpy

    # noqa: PLC0415 — deferred to avoid an import cycle
    from config import AIR_FUEL_BLEND_STATIONS  # noqa: PLC0415

    blend = list(AIR_STATIONS if blend_stations is None else blend_stations)

    df = soil_profile.copy()

    name_col = "waypoint_name" if "waypoint_name" in df.columns else None
    if name_col is None:
        raise ValueError(
            "Soil profile has no waypoint_name column; cannot match stations to "
            f"weather readings. Columns present: {list(df.columns)}"
        )

    # Only the blend stations may contribute an air temperature.
    blend_waypoints = {
        AIR_STATION_TO_WAYPOINT[s]: s for s in blend if s in AIR_STATION_TO_WAYPOINT
    }
    usable = {
        wp: snapshot.temperatures[station]
        for wp, station in blend_waypoints.items()
        if station in snapshot.temperatures
    }

    df["T_air_C"] = df[name_col].map(usable).astype(float)

    soil = df["T_soil_C"]
    has_air = df["T_air_C"].notna()
    has_soil = soil.notna()

    # Default: soil alone, which is the whole buried run.
    T_env = soil.astype(float).copy()
    basis = np.where(has_soil, "soil", "none")

    # Buried blend stations (Bijwasan): the air/soil mean.
    above_ground_wps = {
        AIR_STATION_TO_WAYPOINT[s]
        for s in AIR_FUEL_BLEND_STATIONS
        if s in AIR_STATION_TO_WAYPOINT
    }
    is_above_ground = df[name_col].isin(above_ground_wps)

    buried_blend = has_air & has_soil & ~is_above_ground
    T_env = T_env.mask(buried_blend, (df["T_air_C"] + soil) / 2.0)
    basis = np.where(buried_blend, "air+soil", basis)

    # Above-ground stations (Bina): the pipe is on surface pipework leaving the
    # terminal, so its environment is the air and the product it carries — the
    # soil is simply not in contact with it.
    if T_fuel_C is not None:
        surface = has_air & is_above_ground
        if surface.any():
            T_env = T_env.mask(surface, (df["T_air_C"] + float(T_fuel_C)) / 2.0)
            basis = np.where(surface, "air+fuel", basis)
    else:
        # No fuel temperature supplied: fall back to air+soil where possible
        # rather than inventing one.
        surface_fallback = has_air & has_soil & is_above_ground
        T_env = T_env.mask(surface_fallback, (df["T_air_C"] + soil) / 2.0)
        basis = np.where(surface_fallback, "air+soil", basis)

    # Air but no soil and no fuel: air alone is still a measurement — but say
    # so rather than calling it something it is not.
    air_only = has_air & ~has_soil & (basis == "none")
    T_env = T_env.mask(air_only, df["T_air_C"])
    basis = np.where(air_only, "air", basis)

    df["T_env_C"] = T_env
    df["T_env_basis"] = basis
    return df


# ─── Manual check: python data/openweather.py ────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    print("OpenWeather current air temperature — Bina and Bijwasan")
    print("=" * 66)
    print(f"key configured: {api_key_is_configured()}")
    print()

    snap = fetch_all_station_air_temperatures()

    print(f"  {'station':<18} {'lat':>9} {'lon':>9} {'T_air [C]':>11}")
    for station, (la, lo) in STATIONS.items():
        if station in snap.temperatures:
            print(f"  {station:<18} {la:9.4f} {lo:9.4f} {snap.temperatures[station]:11.2f}")
        else:
            print(f"  {station:<18} {la:9.4f} {lo:9.4f} {'FAILED':>11}")

    if snap.failures:
        print("\n  Failures — NO temperature substituted for these stations:")
        for station, reason in snap.failures.items():
            print(f"    {station:<18} {reason}")

    print(f"\n  complete: {snap.complete}")
