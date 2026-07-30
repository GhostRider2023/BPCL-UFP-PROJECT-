"""
Validation — provider selection and the Open-Meteo fallback
============================================================

The fallback exists so that losing one vendor does not cost the model its
real-time air temperature. It must not become a way for a *fabricated*
temperature to enter: a second source is acceptable, an invented number is not.

These tests pin both halves of that:

  - the right provider is chosen, and its identity is reported, and
  - when every provider fails, the result is empty rather than filled in.

No test here touches the network.
"""

from __future__ import annotations

import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import AIR_STATIONS
from data import air_temperature, open_meteo, openweather
from data.air_temperature import describe_provider, fetch_current_air_temperatures
from data.openweather import OpenWeatherError


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _om_payload(n=None, temp=30.0, time="2026-07-27T10:45"):
    """Open-Meteo array response for the first `n` stations (both, by default)."""
    n = len(AIR_STATIONS) if n is None else n
    return [
        {
            "latitude": lat,
            "longitude": lon,
            "current": {"time": time, "temperature_2m": temp + i},
            "current_units": {"temperature_2m": "°C"},
        }
        for i, (lat, lon) in enumerate(list(AIR_STATIONS.values())[:n])
    ]


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv("OPENWEATHER_API_KEY", raising=False)
    monkeypatch.delenv("AIR_TEMPERATURE_PROVIDER", raising=False)
    monkeypatch.setattr(openweather, "_key_from_dotenv", lambda: None)
    monkeypatch.setattr(openweather, "_key_from_streamlit_secrets", lambda: None)
    monkeypatch.setattr(openweather, "_key_from_file", lambda: None)


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("OPENWEATHER_API_KEY", "test-key")
    monkeypatch.delenv("AIR_TEMPERATURE_PROVIDER", raising=False)


# ═══════════════════════════════════════════════════════════════════
# 1. Open-Meteo client
# ═══════════════════════════════════════════════════════════════════


class TestOpenMeteoClient:
    def test_one_batched_request_for_both_stations(self, monkeypatch):
        calls = []

        def fake_get(url, params=None, timeout=None):
            calls.append((url, params, timeout))
            return _Resp(payload=_om_payload())

        monkeypatch.setattr(requests, "get", fake_get)
        snap = open_meteo.fetch_all_station_air_temperatures()

        assert len(calls) == 1, "should be ONE request, not one per station"
        url, params, timeout = calls[0]
        assert url == open_meteo.OPEN_METEO_URL
        assert params["current"] == "temperature_2m"
        assert timeout and timeout > 0
        assert len(snap.temperatures) == len(AIR_STATIONS)
        assert snap.provider == "open-meteo"
        assert snap.complete

    def test_exact_station_coordinates_are_sent(self, monkeypatch):
        seen = {}

        def fake_get(url, params=None, timeout=None):
            seen.update(params)
            return _Resp(payload=_om_payload())

        monkeypatch.setattr(requests, "get", fake_get)
        open_meteo.fetch_all_station_air_temperatures()

        lats = [float(v) for v in seen["latitude"].split(",")]
        lons = [float(v) for v in seen["longitude"].split(",")]
        assert list(zip(lats, lons)) == list(AIR_STATIONS.values())
        assert (24.1866, 78.1919) in list(zip(lats, lons))  # Bina
        assert (28.5336, 77.0567) in list(zip(lats, lons))  # Bijwasan

    def test_temperature_is_celsius_verbatim(self, monkeypatch):
        monkeypatch.setattr(
            requests,
            "get",
            lambda *a, **k: _Resp(
                payload=[
                    {
                        "latitude": 25.2,
                        "longitude": 75.9,
                        "current": {"time": "t", "temperature_2m": 31.2},
                    }
                ]
            ),
        )
        assert open_meteo.get_current_air_temperature(25.2138, 75.8648) == 31.2

    def test_records_the_observation_timestamp(self, monkeypatch):
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(payload=_om_payload()))
        snap = open_meteo.fetch_all_station_air_temperatures()
        assert snap.observed_at == "2026-07-27T10:45"

    @pytest.mark.parametrize(
        "resp",
        [
            _Resp(status_code=429, payload={"reason": "rate limited"}),
            _Resp(status_code=500, text="boom"),
            _Resp(status_code=200, payload=None),
        ],
    )
    def test_failures_never_produce_a_temperature(self, monkeypatch, resp):
        monkeypatch.setattr(requests, "get", lambda *a, **k: resp)
        snap = open_meteo.fetch_all_station_air_temperatures()
        assert snap.temperatures == {}
        assert len(snap.failures) == len(AIR_STATIONS)

    def test_timeout_produces_no_temperature(self, monkeypatch):
        def boom(*a, **k):
            raise requests.Timeout("slow")

        monkeypatch.setattr(requests, "get", boom)
        snap = open_meteo.fetch_all_station_air_temperatures()
        assert snap.temperatures == {}

    def test_length_mismatch_is_rejected_rather_than_guessed(self, monkeypatch):
        """Fewer entries than stations must not be zipped onto the wrong ones."""
        short = len(AIR_STATIONS) - 1
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(payload=_om_payload(short)))
        snap = open_meteo.fetch_all_station_air_temperatures()
        assert snap.temperatures == {}, "readings must not be mapped to guessed stations"
        assert all("cannot map" in r for r in snap.failures.values())

    def test_single_point_helper_raises_on_empty(self, monkeypatch):
        monkeypatch.setattr(
            requests,
            "get",
            lambda *a, **k: _Resp(payload=[{"latitude": 1, "longitude": 2, "current": {}}]),
        )
        with pytest.raises(OpenWeatherError):
            open_meteo.get_current_air_temperature(25.0, 75.0)


# ═══════════════════════════════════════════════════════════════════
# 2. Provider selection
# ═══════════════════════════════════════════════════════════════════


class TestProviderSelection:
    def test_no_key_falls_back_to_open_meteo(self, no_key, monkeypatch):
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(payload=_om_payload()))
        snap = fetch_current_air_temperatures()
        assert snap.provider == "open-meteo"
        assert len(snap.temperatures) == len(AIR_STATIONS)

    def test_key_present_uses_openweather(self, with_key, monkeypatch):
        class S:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url, params=None, timeout=None):
                return _Resp(payload={"main": {"temp": 31.2}})

        monkeypatch.setattr(requests, "Session", S)
        snap = fetch_current_air_temperatures()
        assert snap.provider == "openweather"
        assert len(snap.temperatures) == len(AIR_STATIONS)

    def test_dead_openweather_key_falls_back_to_open_meteo(self, with_key, monkeypatch):
        """A 401 (unactivated key) must not cost the run its air temperature."""

        class S:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url, params=None, timeout=None):
                return _Resp(status_code=401, payload={"message": "Invalid API key"})

        monkeypatch.setattr(requests, "Session", S)
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(payload=_om_payload()))

        snap = fetch_current_air_temperatures()
        assert snap.provider == "open-meteo"
        assert len(snap.temperatures) == len(AIR_STATIONS)

    def test_partial_openweather_result_is_kept_not_replaced(self, with_key, monkeypatch):
        """A real OpenWeather reading beats swapping the whole set out."""

        class S:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url, params=None, timeout=None):
                if params["lat"] == AIR_STATIONS["Bina"][0]:
                    return _Resp(status_code=500, text="down")
                return _Resp(payload={"main": {"temp": 31.2}})

        monkeypatch.setattr(requests, "Session", S)
        snap = fetch_current_air_temperatures()
        assert snap.provider == "openweather"
        assert len(snap.temperatures) == len(AIR_STATIONS) - 1
        assert "Bina" not in snap.temperatures

    def test_provider_can_be_pinned_to_openweather(self, no_key, monkeypatch):
        """Pinned: report the failure rather than silently using another source."""
        monkeypatch.setenv("AIR_TEMPERATURE_PROVIDER", "openweather")
        snap = fetch_current_air_temperatures()
        assert snap.provider == "openweather"
        assert snap.temperatures == {}

    def test_provider_can_be_pinned_to_open_meteo(self, with_key, monkeypatch):
        monkeypatch.setenv("AIR_TEMPERATURE_PROVIDER", "open-meteo")
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(payload=_om_payload()))
        snap = fetch_current_air_temperatures()
        assert snap.provider == "open-meteo"

    def test_all_providers_down_yields_nothing_invented(self, no_key, monkeypatch):
        def boom(*a, **k):
            raise requests.ConnectionError("no network")

        monkeypatch.setattr(requests, "get", boom)
        snap = fetch_current_air_temperatures()
        assert snap.temperatures == {}, "no air temperature may be invented"
        assert len(snap.failures) == len(AIR_STATIONS)
        assert snap.any_data is False


# ═══════════════════════════════════════════════════════════════════
# 3. Provenance is reported, never assumed
# ═══════════════════════════════════════════════════════════════════


class TestProvenance:
    def test_describe_provider_names_the_source(self, monkeypatch):
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(payload=_om_payload()))
        snap = open_meteo.fetch_all_station_air_temperatures()
        text = describe_provider(snap)
        assert "Open-Meteo" in text
        assert "2026-07-27T10:45" in text

    def test_openweather_description_states_the_contract(self):
        snap = openweather.AirTemperatureSnapshot(provider="openweather")
        text = describe_provider(snap)
        assert "units=metric" in text and "main.temp" in text

    def test_snapshot_defaults_to_openweather_provider(self):
        """Back-compat: the OpenWeather path must keep its identity."""
        assert openweather.AirTemperatureSnapshot().provider == "openweather"

    def test_air_temperature_module_reexports_the_shared_vocabulary(self):
        for name in (
            "AirTemperatureSnapshot",
            "build_environment_profile",
            "environment_temperature",
        ):
            assert hasattr(air_temperature, name)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
