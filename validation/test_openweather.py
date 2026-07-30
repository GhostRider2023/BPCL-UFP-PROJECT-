"""
Validation — real-time air temperature and the environment temperature
=======================================================================

Covers the two things that can quietly go wrong with a live data feed:

  1. The request is built wrong (wrong endpoint, wrong units, wrong field), so
     the number is real but means something else.
  2. A failure gets papered over with a plausible-looking value, so a fabricated
     temperature is indistinguishable from a measurement in the output table.

(2) is the one that matters. Several tests below exist purely to prove that a
failed station produces NO temperature — not a default, not the last known
value, not a seasonal estimate.

No test here touches the network. The HTTP layer is stubbed so the suite stays
deterministic and runnable without an API key.
"""

from __future__ import annotations

import math
import os
import sys

import pandas as pd
import pytest
import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import (
    AIR_STATION_TO_WAYPOINT,
    AIR_STATIONS,
    OPENWEATHER_UNITS,
    OPENWEATHER_URL,
)
from data import openweather
from data.openweather import (
    AirTemperatureSnapshot,
    OpenWeatherError,
    build_environment_profile,
    environment_temperature,
    fetch_all_station_air_temperatures,
    get_current_air_temperature,
)
from geo.route import Route
from model.kernel import SimulationInputs, simulate
from model.soil_profile import _load_soil_csv

# Route waypoints whose air temperature is allowed into the calculation.
# Air is queried at exactly these two terminals and nowhere else.
BLEND_WAYPOINTS = set(AIR_STATION_TO_WAYPOINT.values())


# ═══════════════════════════════════════════════════════════════════
# Stubs
# ═══════════════════════════════════════════════════════════════════


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("OPENWEATHER_API_KEY", "test-key-not-a-real-key")
    return "test-key-not-a-real-key"


@pytest.fixture
def route():
    return Route.from_csv()


@pytest.fixture
def soil_jan():
    df = _load_soil_csv()
    return (
        df[df["month"] == 1].rename(columns={"waypoint_km": "km"}).reset_index(drop=True)
    )


# ═══════════════════════════════════════════════════════════════════
# 1. The request is built to specification
# ═══════════════════════════════════════════════════════════════════


class TestRequestShape:
    def test_calls_current_weather_endpoint_with_metric_units(self, api_key, monkeypatch):
        """units=metric and the Current Weather endpoint, not forecast."""
        seen = {}

        def fake_get(url, params=None, timeout=None):
            seen["url"] = url
            seen["params"] = params
            seen["timeout"] = timeout
            return _Resp(payload={"main": {"temp": 31.2}})

        monkeypatch.setattr(requests, "get", fake_get)
        get_current_air_temperature(25.2138, 75.8648)

        assert seen["url"] == OPENWEATHER_URL
        assert seen["url"].endswith("/data/2.5/weather"), "must be the CURRENT weather endpoint"
        assert "forecast" not in seen["url"]
        assert seen["params"]["units"] == OPENWEATHER_UNITS == "metric"
        assert seen["params"]["lat"] == 25.2138
        assert seen["params"]["lon"] == 75.8648
        assert seen["params"]["appid"] == api_key

    def test_a_timeout_is_always_supplied(self, api_key, monkeypatch):
        """An un-timed-out request can hang the whole simulation."""
        seen = {}

        def fake_get(url, params=None, timeout=None):
            seen["timeout"] = timeout
            return _Resp(payload={"main": {"temp": 20.0}})

        monkeypatch.setattr(requests, "get", fake_get)
        get_current_air_temperature(25.0, 75.0)

        assert seen["timeout"] is not None
        assert 0 < seen["timeout"] <= 30

    def test_temperature_is_read_from_main_temp_verbatim(self, api_key, monkeypatch):
        """No unit conversion: units=metric already returns Celsius."""
        monkeypatch.setattr(
            requests,
            "get",
            lambda *a, **k: _Resp(payload={"main": {"temp": 31.2, "temp_min": 5.0}}),
        )
        assert get_current_air_temperature(25.0, 75.0) == 31.2

    def test_exact_station_coordinates_are_used(self, api_key, monkeypatch):
        """Each station is queried at its own specified coordinates."""
        calls = []

        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url, params=None, timeout=None):
                calls.append((params["lat"], params["lon"]))
                return _Resp(payload={"main": {"temp": 30.0}})

        monkeypatch.setattr(requests, "Session", FakeSession)
        fetch_all_station_air_temperatures()

        assert calls == list(AIR_STATIONS.values())
        assert (24.1866, 78.1919) in calls  # Bina
        assert (28.5336, 77.0567) in calls  # Bijwasan
        assert len(calls) == len(AIR_STATIONS) == 2

    def test_one_request_per_station_no_duplicates(self, api_key, monkeypatch):
        """Two terminals, two calls — a run must not re-query."""
        calls = []

        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url, params=None, timeout=None):
                calls.append((params["lat"], params["lon"]))
                return _Resp(payload={"main": {"temp": 30.0}})

        monkeypatch.setattr(requests, "Session", FakeSession)
        fetch_all_station_air_temperatures()

        assert len(calls) == len(set(calls)) == len(AIR_STATIONS)


# ═══════════════════════════════════════════════════════════════════
# 2. Failures are never replaced by an invented temperature
# ═══════════════════════════════════════════════════════════════════


class TestFailuresAreNotInvented:
    @pytest.mark.parametrize(
        "resp",
        [
            _Resp(status_code=401, payload={"message": "Invalid API key"}),
            _Resp(status_code=429, payload={"message": "rate limit"}),
            _Resp(status_code=500, text="upstream boom"),
            _Resp(status_code=200, payload={"weather": []}),  # no main.temp
            _Resp(status_code=200, payload={"main": {"temp": "warm"}}),  # not numeric
            _Resp(status_code=200, payload=None),  # not JSON
        ],
    )
    def test_bad_response_raises_rather_than_defaults(self, api_key, monkeypatch, resp):
        monkeypatch.setattr(requests, "get", lambda *a, **k: resp)
        with pytest.raises(OpenWeatherError):
            get_current_air_temperature(25.0, 75.0)

    def test_timeout_raises_rather_than_defaults(self, api_key, monkeypatch):
        def boom(*a, **k):
            raise requests.Timeout("too slow")

        monkeypatch.setattr(requests, "get", boom)
        with pytest.raises(OpenWeatherError, match="timed out"):
            get_current_air_temperature(25.0, 75.0)

    def test_one_dead_station_does_not_cost_the_others_their_data(self, api_key, monkeypatch):
        """Per-station isolation: Bijwasan's real reading survives Bina's failure."""

        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url, params=None, timeout=None):
                if params["lat"] == AIR_STATIONS["Bina"][0]:
                    return _Resp(status_code=500, text="down")
                return _Resp(payload={"main": {"temp": 30.0}})

        monkeypatch.setattr(requests, "Session", FakeSession)
        snap = fetch_all_station_air_temperatures()

        assert "Bina" in snap.failures
        assert "Bina" not in snap.temperatures, "a failed station must have NO temperature"
        assert snap.temperatures["Bijwasan"] == 30.0
        assert len(snap.temperatures) == len(AIR_STATIONS) - 1
        assert snap.complete is False

    def test_missing_api_key_fails_every_station_without_inventing(self, monkeypatch):
        monkeypatch.delenv("OPENWEATHER_API_KEY", raising=False)
        monkeypatch.setattr(openweather, "_key_from_dotenv", lambda: None)
        monkeypatch.setattr(openweather, "_key_from_streamlit_secrets", lambda: None)
        monkeypatch.setattr(openweather, "_key_from_file", lambda: None)

        snap = fetch_all_station_air_temperatures()

        assert snap.temperatures == {}
        assert len(snap.failures) == len(AIR_STATIONS)

    def test_api_key_is_not_hardcoded_in_source(self):
        """The key must come from the environment, never from the repo."""
        src = (openweather.PROJECT_ROOT / "data" / "openweather.py").read_text(encoding="utf-8")
        assert "appid" in src
        # No 32-hex-character literal (the shape of an OpenWeather key).
        import re

        assert not re.search(r"['\"][0-9a-f]{32}['\"]", src), "a literal API key is in the source"


# ═══════════════════════════════════════════════════════════════════
# 3. T_environment = (T_air + T_soil) / 2
# ═══════════════════════════════════════════════════════════════════


class TestEnvironmentTemperature:
    def test_worked_example_from_the_specification(self):
        assert environment_temperature(31.2, 24.93) == pytest.approx(28.065)

    def test_is_the_arithmetic_mean(self):
        for air, soil in ((10.0, 40.0), (-5.0, 5.0), (30.0, 30.0)):
            assert environment_temperature(air, soil) == pytest.approx((air + soil) / 2)

    def test_air_is_blended_only_at_the_terminal_stations(self, soil_jan):
        """Bina and Bijwasan blend; the buried run in between does not."""
        snap = AirTemperatureSnapshot(
            temperatures={name: 30.0 + i for i, name in enumerate(AIR_STATIONS)}
        )
        env = build_environment_profile(soil_jan, snap)

        for _, r in env.iterrows():
            if r["waypoint_name"] in BLEND_WAYPOINTS:
                assert r["T_env_C"] == pytest.approx((r["T_air_C"] + r["T_soil_C"]) / 2)
                assert r["T_env_basis"] == "air+soil"
            else:
                assert math.isnan(r["T_air_C"]), (
                    f"{r['waypoint_name']} is not a blend station but carries an "
                    f"air temperature — air must not leak into the buried run"
                )
                assert r["T_env_C"] == pytest.approx(r["T_soil_C"])
                assert r["T_env_basis"] == "soil"

    def test_intermediate_stations_stay_soil_only_with_a_full_fetch(self, soil_jan):
        """A complete fetch must change only the two terminal rows."""
        hot = AirTemperatureSnapshot(temperatures=dict.fromkeys(AIR_STATIONS, 45.0))
        env = build_environment_profile(soil_jan, hot)

        mid = env[~env["waypoint_name"].isin(BLEND_WAYPOINTS)]
        assert (mid["T_env_C"] == mid["T_soil_C"]).all()
        assert env["T_air_C"].notna().sum() == len(AIR_STATIONS)

    def test_bina_falls_back_to_air_plus_fuel_when_soil_is_missing(self, soil_jan):
        """Documented exception, at the dispatch terminal only."""
        s = soil_jan.copy()
        bina = AIR_STATION_TO_WAYPOINT["Bina"]
        s.loc[s["waypoint_name"] == bina, "T_soil_C"] = float("nan")

        snap = AirTemperatureSnapshot(temperatures=dict.fromkeys(AIR_STATIONS, 31.2))
        env = build_environment_profile(s, snap, T_fuel_C=40.0)

        row = env[env["waypoint_name"] == bina].iloc[0]
        assert row["T_env_C"] == pytest.approx((31.2 + 40.0) / 2)
        assert row["T_env_basis"] == "air+fuel"

    def test_air_plus_fuel_never_applies_away_from_bina(self, soil_jan):
        """Bijwasan must not average the product temperature into its boundary."""
        s = soil_jan.copy()
        s["T_soil_C"] = float("nan")
        snap = AirTemperatureSnapshot(temperatures=dict.fromkeys(AIR_STATIONS, 31.2))
        env = build_environment_profile(s, snap, T_fuel_C=40.0)

        bij = env[env["waypoint_name"] == AIR_STATION_TO_WAYPOINT["Bijwasan"]].iloc[0]
        assert bij["T_env_basis"] == "air"
        assert bij["T_env_C"] == pytest.approx(31.2), "no soil -> air alone, not air+fuel"

    def test_fuel_temperature_is_used_at_bina_even_when_soil_exists(self, soil_jan):
        """Bina is above ground: the soil is simply not its environment.

        The pipe leaves the dispatch terminal on surface pipework, so even
        though an ERA5 soil temperature exists at km 0 it does not describe
        what this length of pipe is in contact with.
        """
        snap = AirTemperatureSnapshot(temperatures=dict.fromkeys(AIR_STATIONS, 31.2))
        env = build_environment_profile(soil_jan, snap, T_fuel_C=40.0)

        bina = env[env["waypoint_name"] == AIR_STATION_TO_WAYPOINT["Bina"]].iloc[0]
        assert bina["T_env_basis"] == "air+fuel"
        assert bina["T_env_C"] == pytest.approx((31.2 + 40.0) / 2)
        # ...and it is NOT the air/soil mean, which the soil reading would give.
        assert bina["T_env_C"] != pytest.approx((31.2 + bina["T_soil_C"]) / 2)

    def test_fuel_temperature_changes_nothing_away_from_bina(self, soil_jan):
        """Only the above-ground station may see the fuel temperature."""
        snap = AirTemperatureSnapshot(temperatures=dict.fromkeys(AIR_STATIONS, 31.2))
        with_fuel = build_environment_profile(soil_jan, snap, T_fuel_C=40.0)
        without = build_environment_profile(soil_jan, snap)

        bina_wp = AIR_STATION_TO_WAYPOINT["Bina"]
        a = with_fuel[with_fuel["waypoint_name"] != bina_wp].reset_index(drop=True)
        b = without[without["waypoint_name"] != bina_wp].reset_index(drop=True)
        pd.testing.assert_series_equal(a["T_env_C"], b["T_env_C"])

    def test_without_a_fuel_temperature_bina_reverts_to_air_plus_soil(self, soil_jan):
        """No fuel temperature supplied -> use what exists, invent nothing."""
        snap = AirTemperatureSnapshot(temperatures=dict.fromkeys(AIR_STATIONS, 31.2))
        env = build_environment_profile(soil_jan, snap)

        bina = env[env["waypoint_name"] == AIR_STATION_TO_WAYPOINT["Bina"]].iloc[0]
        assert bina["T_env_basis"] == "air+soil"
        assert bina["T_env_C"] == pytest.approx((31.2 + bina["T_soil_C"]) / 2)

    def test_env_never_averages_the_product_temperature(self, route, soil_jan):
        """The average is (air + soil)/2 — the fluid's own state is not in it."""
        snap = AirTemperatureSnapshot(
            temperatures=dict.fromkeys(AIR_STATIONS, 31.2)
        )
        env = build_environment_profile(soil_jan, snap)
        df = simulate(
            SimulationInputs(
                product="petrol",
                T_dispatch_C=40.0,
                V_dispatch_KL=1000.0,
                flow_rate_m3hr=70.0,
                month=1,
                P_dispatch_bar=70.0,
            ),
            route,
            env,
        )
        wp = df[df["waypoint_name"].isin(BLEND_WAYPOINTS)]
        assert len(wp) == len(AIR_STATIONS)
        for _, r in wp.iterrows():
            assert r["T_env_C"] == pytest.approx((r["T_air_C"] + r["T_soil_C"]) / 2, abs=5e-3)

    def test_failed_terminal_stays_blank_and_falls_back_to_soil(self, soil_jan):
        """No interpolated stand-in appears at a terminal with no reading."""
        temps = {n: 31.0 for n in AIR_STATIONS if n != "Bijwasan"}
        env = build_environment_profile(soil_jan, AirTemperatureSnapshot(temperatures=temps))

        bij = env[env["waypoint_name"] == AIR_STATION_TO_WAYPOINT["Bijwasan"]].iloc[0]
        assert math.isnan(bij["T_air_C"]), "a failed station must not be given an air value"
        assert bij["T_env_C"] == pytest.approx(bij["T_soil_C"]), "falls back to soil alone"
        assert bij["T_env_basis"] == "soil"

    def test_kernel_never_interpolates_air_along_the_route(self, route, soil_jan):
        """Air exists at the terminals and nowhere else in the output table."""
        temps = {n: 31.0 for n in AIR_STATIONS if n != "Bijwasan"}
        env = build_environment_profile(soil_jan, AirTemperatureSnapshot(temperatures=temps))
        df = simulate(
            SimulationInputs(
                product="petrol",
                T_dispatch_C=40.0,
                V_dispatch_KL=1000.0,
                flow_rate_m3hr=70.0,
                month=1,
                P_dispatch_bar=70.0,
            ),
            route,
            env,
        )
        row = df[df["waypoint_name"] == AIR_STATION_TO_WAYPOINT["Bijwasan"]].iloc[0]
        assert math.isnan(row["T_air_C"]), (
            "Bijwasan had no reading, yet the table shows an air temperature — "
            "it has been interpolated from its neighbours."
        )
        # Bina is the only terminal left carrying air, and no intermediate
        # chainage may have acquired any.
        assert df["T_air_C"].notna().sum() == 1
        assert df.attrs["n_stations_with_air"] == 1


# ═══════════════════════════════════════════════════════════════════
# 4. T_env is what the heat-transfer equation actually uses
# ═══════════════════════════════════════════════════════════════════


class TestEnvironmentDrivesHeatTransfer:
    def _run(self, route, soil, Q=70.0, T=40.0):
        return simulate(
            SimulationInputs(
                product="petrol",
                T_dispatch_C=T,
                V_dispatch_KL=1000.0,
                flow_rate_m3hr=Q,
                month=1,
                P_dispatch_bar=70.0,
            ),
            route,
            soil,
        )

    def test_warmer_air_gives_a_warmer_receipt_temperature(self, route, soil_jan):
        """If T_env did not reach the ODE, this could not move."""
        cold = build_environment_profile(
            soil_jan, AirTemperatureSnapshot(temperatures=dict.fromkeys(AIR_STATIONS, 10.0))
        )
        hot = build_environment_profile(
            soil_jan, AirTemperatureSnapshot(temperatures=dict.fromkeys(AIR_STATIONS, 45.0))
        )
        T_cold = self._run(route, cold)["T_C"].iloc[-1]
        T_hot = self._run(route, hot)["T_C"].iloc[-1]

        assert T_hot > T_cold + 5.0, (
            f"air temperature barely moved the receipt temperature "
            f"({T_cold:.2f} -> {T_hot:.2f} C); T_env is probably not in the ODE"
        )

    def test_bijwasan_air_lifts_the_receipt_temperature_off_the_soil_value(
        self, route, soil_jan
    ):
        """The receipt terminal's air must move the receipt temperature.

        It does not fully reach T_env: the blend applies at km 360, the
        previous station is at km 340, and with L* ~ 7 km the product has only
        ~3 relaxation lengths of that warmer boundary to respond to. So the
        correct assertion is that it moves substantially TOWARD the blend
        relative to a soil-only run — not that it arrives at it.
        """
        hot = build_environment_profile(
            soil_jan, AirTemperatureSnapshot(temperatures=dict.fromkeys(AIR_STATIONS, 41.0))
        )
        with_air = self._run(route, hot)
        soil_only = self._run(route, soil_jan)

        T_air_run = with_air["T_C"].iloc[-1]
        T_soil_run = soil_only["T_C"].iloc[-1]
        T_env_end = with_air["T_env_C"].iloc[-1]

        assert T_env_end > T_soil_run + 5.0, "test setup: the blend should be well above soil"
        assert T_air_run > T_soil_run + 1.0, (
            f"receipt temperature barely moved when Bijwasan's air was applied "
            f"({T_soil_run:.2f} -> {T_air_run:.2f} C against a boundary of "
            f"{T_env_end:.2f} C) — T_env is not reaching the ODE."
        )

    def test_terminal_air_does_not_disturb_the_buried_middle(self, route, soil_jan):
        """Air at the terminals must stay a local effect, not a route-wide one."""
        hot = build_environment_profile(
            soil_jan, AirTemperatureSnapshot(temperatures=dict.fromkeys(AIR_STATIONS, 45.0))
        )
        with_air = self._run(route, hot).set_index("km")
        soil_only = self._run(route, soil_jan).set_index("km")

        # Mid-route, far from both terminals, the two runs must agree closely:
        # the Bina blend has decayed away and Bijwasan's has not arrived.
        mid = with_air.loc[350.0:550.0, "T_C"] - soil_only.loc[350.0:550.0, "T_C"]
        assert mid.abs().max() < 0.5, (
            f"terminal air shifted the buried mid-route temperature by up to "
            f"{mid.abs().max():.2f} C — it is leaking along the whole line"
        )

    def test_soil_only_when_no_air_is_available(self, route, soil_jan):
        """Absent OpenWeather, behaviour is exactly the pre-existing model."""
        df = self._run(route, soil_jan)
        assert df.attrs["air_temperature_used"] is False
        assert df.attrs["n_stations_with_air"] == 0
        assert (df["T_env_C"] == df["T_soil_C"]).all()

    def test_soil_temperature_model_is_untouched(self, soil_jan):
        """The ERA5 soil column must survive the air-temperature feature."""
        snap = AirTemperatureSnapshot(
            temperatures=dict.fromkeys(AIR_STATIONS, 31.2)
        )
        env = build_environment_profile(soil_jan, snap)
        pd.testing.assert_series_equal(env["T_soil_C"], soil_jan["T_soil_C"])
        pd.testing.assert_series_equal(env["k_soil_WmK"], soil_jan["k_soil_WmK"])

    def test_k_soil_is_not_averaged_with_anything(self, route, soil_jan):
        """Only the sink TEMPERATURE is averaged; the conduction path is soil."""
        snap = AirTemperatureSnapshot(
            temperatures=dict.fromkeys(AIR_STATIONS, 45.0)
        )
        env = build_environment_profile(soil_jan, snap)
        with_air = self._run(route, env)
        without = self._run(route, soil_jan)
        pd.testing.assert_series_equal(with_air["k_soil_WmK"], without["k_soil_WmK"])
        pd.testing.assert_series_equal(with_air["U_Wm2K"], without["U_Wm2K"])


# ═══════════════════════════════════════════════════════════════════
# 5. Geometry — the diameter change
# ═══════════════════════════════════════════════════════════════════


class TestMMBPLGeometry:
    """The documented 18"/8" split: 18" mainline, 8" tail from Piyala.

    Sources describe the Manmad->Piyala->Bijwasan section as 18"/8" — an
    18-inch mainline with an 8-inch tail, not 8 inches throughout. Applying the
    tail's bore to the whole mainline overstates the friction gradient by
    ~2.5x, which is what made a 3 m/s design velocity look impossible.
    """

    NPS18_OD_M = 18.0 * 0.0254  # 0.45720 m — NPS 14+ : nominal == actual OD
    NPS8_OD_M = 8.625 * 0.0254  # 0.219075 m — NPS 8 is NOT 8.000 in
    TAIL_START_KM = 599.0

    def test_mainline_is_18_inch(self, route):
        for km in (0.0, 259.0, 299.0, 369.0, 469.0, 509.0, 589.0, 598.9):
            assert route.d_outer(km) == pytest.approx(self.NPS18_OD_M, abs=1e-12), f"at km {km}"

    def test_tail_is_8_inch_from_piyala(self, route):
        for km in (self.TAIL_START_KM, 610.0, 618.9, 619.0):
            assert route.d_outer(km) == pytest.approx(self.NPS8_OD_M, abs=1e-12), f"at km {km}"

    def test_nominal_size_is_not_the_outer_diameter(self):
        """NPS 8 is 8.625 in OD; NPS 18 is 18.000 in. The classic sizing trap."""
        assert self.NPS8_OD_M == pytest.approx(0.219075, abs=1e-6)
        assert self.NPS8_OD_M != pytest.approx(8.0 * 0.0254, abs=1e-4)
        assert self.NPS18_OD_M == pytest.approx(0.4572, abs=1e-6)  # nominal == actual

    def test_the_tail_is_the_hydraulic_bottleneck(self, route):
        """4.5x less area, so the same flow runs 4.5x faster in the tail."""
        ratio = route.area(0.0) / route.area(610.0)
        assert ratio == pytest.approx(4.47, abs=0.05)
        assert route.area(0.0) == pytest.approx(0.144316, abs=1e-5)
        assert route.area(610.0) == pytest.approx(0.032275, abs=1e-5)

    def test_no_stale_uniform_diameter_remains(self, route):
        """None of 16", 10" or uniform-8" survives anywhere on the route."""
        for w in route.waypoints:
            assert w.od_inch in (18.0, 8.625), f"{w.name} has od_inch={w.od_inch}"
            for stale in (0.4064, 0.254):
                assert w.d_outer_m != pytest.approx(stale, abs=1e-6)

    def test_flow_is_turbulent_at_realistic_velocities(self, route):
        """Refined products cannot run laminar in either bore.

        Laminar needs Re < ~2300. Pinned because the design brief for this line
        described the flow at 3 m/s as laminar; at 3 m/s the Reynolds number is
        1e5 to 2e6 depending on product and bore. Fully turbulent is not a
        modelling choice, it is a consequence of the viscosity of MS/HSD/ATF —
        and it is perfectly normal: product pipelines are designed to operate
        there, where the friction factor is nearly Reynolds-independent.
        """
        from config import PRODUCTS  # noqa: PLC0415

        for D_i in (route.d_inner(0.0), route.d_inner(610.0)):
            for name, p in PRODUCTS.items():
                mu = p.viscosity_at_T(30.0)
                Re = p.density_ref_kgm3 * 3.0 * D_i / mu
                assert Re > 1e5, f"{name} at D_i={D_i:.3f}: Re={Re:,.0f} implausibly low"
                assert Re > 2300, f"{name}: Re={Re:,.0f} would be laminar/transitional"

    def test_hydraulics_respond_to_the_local_bore(self, route, soil_jan):
        """Velocity and Re must be COMPUTED from each segment's own bore.

        Checked at both ends: the 18" mainline at dispatch and the 8" tail at
        receipt. The same volumetric flow must come out ~4.5x faster in the
        tail — if it does not, the diameter is only being displayed, not used.
        """
        Q = 300.0
        df = simulate(
            SimulationInputs(
                product="petrol",
                T_dispatch_C=40.0,
                V_dispatch_KL=1000.0,
                flow_rate_m3hr=Q,
                month=1,
                P_dispatch_bar=70.0,
            ),
            route,
            soil_jan,
        )

        # Continuity: u = m_dot(x) / (rho(x) * A(x)). rho varies because the
        # product cools, and A because the bore steps at Piyala. Q/A with the
        # dispatch values would be wrong on both counts at the receipt end.
        for idx, km in ((0, 0.0), (-1, 619.0)):
            row = df.iloc[idx]
            u_expected = row["m_dot_kgs"] / (row["rho_kgm3"] * route.area(km))
            assert row["velocity_ms"] == pytest.approx(u_expected, rel=5e-3), f"at km {km}"

            Re_expected = (
                row["rho_kgm3"] * row["velocity_ms"] * route.d_inner(km) / (row["mu_cP"] / 1000.0)
            )
            assert row["Re"] == pytest.approx(Re_expected, rel=1e-3), f"at km {km}"

        # The batch runs Bina -> Bijwasan intact, so the same mass flow passes
        # through both bores and the tail runs at the full area ratio.
        speedup = df["velocity_ms"].iloc[-1] / df["velocity_ms"].iloc[0]
        assert speedup == pytest.approx(4.47, abs=0.15), (
            f"tail velocity is only {speedup:.2f}x the mainline — the 8-inch "
            f"tail is not being applied"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
