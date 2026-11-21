"""OpenSkyProvider: live aircraft positions from the OpenSky Network REST API.

Auth: OAuth2 client credentials (create an API client in your OpenSky account).
Without credentials it falls back to anonymous access (lower limits).
Poll every ~60 s at most: OpenSky meters requests in credits.

  GET https://opensky-network.org/api/states/all?icao24=abc123&icao24=...
  state vector: [icao24, callsign, origin_country, time_position, last_contact,
                 longitude, latitude, baro_altitude, on_ground, velocity (m/s),
                 true_track, vertical_rate, sensors, geo_altitude, squawk, spi,
                 position_source, (category)]
"""
from __future__ import annotations

import time
from datetime import datetime, timezone as dt_tz

import requests

from integrations.base import PositionDTO, TrackingProvider

API_URL = "https://opensky-network.org/api/states/all"
TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
MS_TO_KNOTS = 1.943844


def parse_states(payload: dict) -> list[PositionDTO]:
    out = []
    for s in payload.get("states") or []:
        if len(s) < 11 or s[5] is None or s[6] is None:
            continue
        epoch = s[3] or s[4] or payload.get("time") or time.time()
        out.append(PositionDTO(
            tracker_id=str(s[0]).lower(),
            lat=float(s[6]), lon=float(s[5]),
            ts=datetime.fromtimestamp(epoch, tz=dt_tz.utc),
            speed=round(s[9] * MS_TO_KNOTS, 1) if s[9] is not None else None,
            heading=s[10],
            source="opensky",
        ))
    return out


def parse_flights(payload: dict) -> list[dict]:
    """State vectors -> [{icao24, callsign, country, lat, lon, alt_m, speed_kn, track, on_ground, ts}]."""
    out = []
    for s in payload.get("states") or []:
        if len(s) < 11 or s[5] is None or s[6] is None:
            continue
        out.append({
            "icao24": str(s[0]).lower(), "callsign": (s[1] or "").strip(), "country": s[2],
            "lat": float(s[6]), "lon": float(s[5]), "alt_m": s[13] if len(s) > 13 and s[13] is not None else s[7],
            "speed_kn": round(s[9] * MS_TO_KNOTS) if s[9] is not None else None, "track": s[10],
            "on_ground": bool(s[8]), "ts": datetime.fromtimestamp(s[3] or s[4] or time.time(), tz=dt_tz.utc),
        })
    return out


class OpenSkyProvider(TrackingProvider):
    name = "opensky"
    mode = "AIR"

    def __init__(self, client_id: str = "", client_secret: str = "", session: requests.Session | None = None) -> None:
        super().__init__()
        self.client_id = client_id
        self.client_secret = client_secret
        self.session = session or requests.Session()
        self._token = ""
        self._token_expires = 0.0

    def _auth_headers(self) -> dict:
        if not (self.client_id and self.client_secret):
            return {}
        if not self._token or time.time() > self._token_expires - 60:
            resp = self.session.post(
                TOKEN_URL,
                data={"grant_type": "client_credentials", "client_id": self.client_id,
                      "client_secret": self.client_secret},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            self._token = data["access_token"]
            self._token_expires = time.time() + int(data.get("expires_in", 1800))
        return {"Authorization": f"Bearer {self._token}"}

    def find_flights(self, *, callsign_prefix: str = "", bbox: tuple[float, float, float, float] | None = None,
                     icao24: str = "") -> list[dict]:
        """Live flights, optionally by callsign prefix ("UAE", "UAE9845"), bbox (lamin, lomin, lamax, lomax)
        or exact ICAO24. Used to pick an aircraft to bind a shipment to (manage.py track_flight)."""
        params: list[tuple[str, str]] = []
        if icao24:
            params.append(("icao24", icao24.lower()))
        if bbox:
            params += list(zip(("lamin", "lomin", "lamax", "lomax"), (str(v) for v in bbox)))
        resp = self.session.get(API_URL, params=params, headers=self._auth_headers(), timeout=30)
        resp.raise_for_status()
        prefix = callsign_prefix.strip().upper()
        return [f for f in parse_flights(resp.json()) if f["callsign"].upper().startswith(prefix)]

    def fetch(self, tracker_ids: list[str]) -> list[PositionDTO]:
        ids = [t.lower() for t in tracker_ids if t]
        if not ids:
            return []
        resp = self.session.get(API_URL, params=[("icao24", i) for i in ids], headers=self._auth_headers(), timeout=20)
        if resp.status_code == 401:
            self._token = ""  # force a token refresh next time
        resp.raise_for_status()
        self.record_success()
        return parse_states(resp.json())
