"""Open-Meteo weather (free, no key): forecast for land points, marine for sea points.

One request per batch of points, cached 30 min. Returns the WORST value over the
next `days` (conservative: we do not know the exact hour the cargo passes).
"""
from __future__ import annotations

import time

import requests

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
CACHE_SECONDS = 30 * 60
TIMEOUT = 10

_cache: dict[tuple, tuple[float, list[dict]]] = {}


class WeatherClient:
    def __init__(self, days: int = 3, session: requests.Session | None = None):
        self.days = days
        self.http = session or requests.Session()

    def land(self, points: list[tuple[float, float]]) -> list[dict]:
        """Per point: {"snowfall_cm", "gusts_kmh", "storm", "url"} (worst over the window)."""
        rows, url = self._get(FORECAST_URL, points, "snowfall_sum,wind_gusts_10m_max,weather_code")
        return [{
            "snowfall_cm": _max(r["daily"].get("snowfall_sum")),
            "gusts_kmh": _max(r["daily"].get("wind_gusts_10m_max")),
            "storm": any(c is not None and c >= 95 for c in r["daily"].get("weather_code") or []),  # WMO thunderstorm
            "url": url,
        } for r in rows]

    def marine(self, points: list[tuple[float, float]]) -> list[dict]:
        """Per point: {"wave_m", "url"} (worst over the window; None over land)."""
        rows, url = self._get(MARINE_URL, points, "wave_height_max")
        return [{"wave_m": _max(r["daily"].get("wave_height_max")), "url": url} for r in rows]

    def _get(self, base: str, points: list[tuple[float, float]], daily: str) -> tuple[list[dict], str]:
        if not points:
            return [], base
        pts = tuple((round(lat, 2), round(lon, 2)) for lat, lon in points)
        params = {
            "latitude": ",".join(str(p[0]) for p in pts), "longitude": ",".join(str(p[1]) for p in pts),
            "daily": daily, "forecast_days": self.days, "timezone": "UTC",
        }
        url = requests.Request("GET", base, params=params).prepare().url
        hit = _cache.get((base, pts, daily))
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1], url
        resp = self.http.get(base, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        rows = data if isinstance(data, list) else [data]  # one point -> object, several -> list
        _cache[(base, pts, daily)] = (time.time(), rows)
        return rows, url


def _max(values) -> float | None:
    vals = [v for v in values or [] if v is not None]
    return max(vals) if vals else None
