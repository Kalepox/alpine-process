"""Detectors: pure functions from one shipment's situation to Disruptions. No ORM, no LLM.

- corridor: a corridor on the remaining route is CLOSED, or RESTRICTED with real expected delay.
  (Road/border news from risk_scout lands here too: it sets corridor status.)
- tracking: the device is off the planned route, or the ETA has slipped.
- weather:  Open-Meteo at the alpine passes ahead (road legs) and along the current voyage (sea).
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

from autopilot.domain.network import haversine_km
from autopilot.domain.risk import RiskOverlay
from autopilot.domain.schemas import Disruption, Leg, SourceRef
from autopilot.domain.tables import CORRIDOR_POINTS, CORRIDORS, MODES, MONITOR, ROAD, SEA, MonitorThresholds

RANK = {"info": 0, "warn": 1, "block": 2}


@dataclass
class Tracked:
    """What the monitor knows about one in-transit shipment."""

    shipment_id: int
    ref: str
    legs: list[Leg]                       # remaining legs; legs[0] is the current one
    leg_offset: int = 0                   # index of legs[0] in the approved route
    position: tuple[float, float] | None = None  # (lat, lon) of the last fix
    eta_planned: dt.datetime | None = None
    eta_current: dt.datetime | None = None


# ---------------------------------------------------------------- corridors
def corridor_hits(t: Tracked, overlay: RiskOverlay, th: MonitorThresholds = MONITOR) -> list[Disruption]:
    out, seen = [], set()
    for i, leg in enumerate(t.legs):
        for code in leg.corridors:
            if code in seen:
                continue
            seen.add(code)
            r = overlay.get(code)
            if r.status == "CLOSED":
                out.append(Disruption(kind="CORRIDOR", alert_code="CORRIDOR_CLOSED", severity="block",
                                      shipment_id=t.shipment_id, leg_index=t.leg_offset + i, corridor=code,
                                      delay_days_est=r.delay_if_closed, evidence={"status": "CLOSED"}))
            elif r.status == "RESTRICTED" and r.p_close_30d * r.delay_if_closed > th.restricted_delay_days:
                out.append(Disruption(kind="CORRIDOR", alert_code="CORRIDOR_AT_RISK", severity="warn",
                                      shipment_id=t.shipment_id, leg_index=t.leg_offset + i, corridor=code,
                                      delay_days_est=round(r.p_close_30d * r.delay_if_closed, 1),
                                      evidence={"status": "RESTRICTED", "p_close_30d": r.p_close_30d}))
    return out


# ---------------------------------------------------------------- tracking
def tracking_hits(t: Tracked, th: MonitorThresholds = MONITOR) -> list[Disruption]:
    out = []
    leg = t.legs[0] if t.legs else None
    if t.position and leg and len(leg.geometry) >= 2:
        off = distance_to_polyline_km(*t.position, leg.geometry)
        limit = dict(th.off_route_km)[leg.mode]
        if off > limit:
            out.append(Disruption(kind="TRACKING", alert_code="OFF_ROUTE", severity="warn", shipment_id=t.shipment_id,
                                  leg_index=t.leg_offset, evidence={"off_route_km": round(off, 1), "limit_km": limit,
                                                                    "at": [t.position[1], t.position[0]]}))
    if t.eta_planned and t.eta_current:
        slip = (t.eta_current - t.eta_planned).total_seconds() / 86400
        if slip > th.eta_slip_days:
            out.append(Disruption(kind="TRACKING", alert_code="ETA_SLIP", severity="warn", shipment_id=t.shipment_id,
                                  leg_index=t.leg_offset, delay_days_est=round(slip, 1),
                                  evidence={"eta_planned": t.eta_planned.isoformat(), "eta_current": t.eta_current.isoformat()}))
    return out


# ---------------------------------------------------------------- weather
def weather_points(t: Tracked, th: MonitorThresholds = MONITOR) -> tuple[list[tuple], list[tuple]]:
    """(land points, sea points) to check, each (lat, lon, leg_index, corridor | None)."""
    land, sea = [], []
    start_day = 0.0
    for i, leg in enumerate(t.legs):
        if start_day > th.horizon_days:
            break
        if leg.mode == ROAD:  # alpine passes close for trucks; rail mostly runs through tunnels
            land += [(*CORRIDOR_POINTS[c], t.leg_offset + i, c) for c in leg.corridors if c in CORRIDOR_POINTS]
        if leg.mode == SEA and i == 0 and t.position:  # the voyage under way
            horizon_km = th.horizon_days * MODES[SEA].km_per_day
            sea += [(lat, lon, t.leg_offset, None)
                    for lat, lon in sample_ahead(*t.position, leg.geometry, th.sea_sample_km, horizon_km)]
        start_day += leg.days
    return land, sea


def weather_hits(t: Tracked, client, th: MonitorThresholds = MONITOR) -> list[Disruption]:
    """Worst forecast per leg over the horizon. client: signals.weather.WeatherClient (or a fake)."""
    land, sea = weather_points(t, th)
    out = []
    for (lat, lon, leg_i, code), w in zip(land, client.land([(p[0], p[1]) for p in land])):
        snow, gust = w["snowfall_cm"] or 0, w["gusts_kmh"] or 0
        if snow >= th.snow_block_cm or gust >= th.gust_block_kmh:
            sev, delay = "block", 1.0
        elif snow >= th.snow_warn_cm or gust >= th.gust_warn_kmh:
            sev, delay = "warn", 0.3
        else:
            continue
        out.append(Disruption(kind="WEATHER", alert_code="WEATHER_RISK", severity=sev, shipment_id=t.shipment_id,
                              leg_index=leg_i, corridor=code, delay_days_est=delay,
                              evidence={"snowfall_cm": snow, "gusts_kmh": gust, "at": [lon, lat], "window_days": th.horizon_days},
                              sources=[SourceRef(title=f"Open-Meteo forecast at {CORRIDORS[code][0]}", url=w["url"])]))
    worst = None
    for (lat, lon, leg_i, _), w in zip(sea, client.marine([(p[0], p[1]) for p in sea])):
        if w["wave_m"] is not None and (worst is None or w["wave_m"] > worst[0]):
            worst = (w["wave_m"], lat, lon, leg_i, w["url"])
    if worst and worst[0] >= th.wave_warn_m:
        wave, lat, lon, leg_i, url = worst
        sev, delay = ("block", 2.0) if wave >= th.wave_block_m else ("warn", 0.5)
        out.append(Disruption(kind="WEATHER", alert_code="WEATHER_RISK", severity=sev, shipment_id=t.shipment_id,
                              leg_index=leg_i, delay_days_est=delay,
                              evidence={"wave_height_m": wave, "at": [lon, lat], "window_days": th.horizon_days},
                              sources=[SourceRef(title="Open-Meteo marine forecast", url=url)]))
    return out


# ---------------------------------------------------------------- what to act on
class MonitorMemory:
    """Remembers what was already reported, so a standing problem is not re-alerted every tick."""

    def __init__(self):
        self._seen: dict[tuple, tuple[int, dt.datetime]] = {}

    @staticmethod
    def fingerprint(d: Disruption) -> tuple:
        return d.shipment_id, d.kind, d.alert_code, d.leg_index, d.corridor

    def filter_new(self, found: list[Disruption], now: dt.datetime, th: MonitorThresholds = MONITOR) -> list[Disruption]:
        """New = never seen, more severe than before, or still there after realert_hours.
        Problems that disappeared are forgotten, so they count as new if they come back."""
        current = {self.fingerprint(d) for d in found}
        self._seen = {fp: v for fp, v in self._seen.items() if fp in current}
        new = []
        for d in found:
            fp, prev = self.fingerprint(d), self._seen.get(self.fingerprint(d))
            if prev is None or RANK[d.severity] > prev[0] or now - prev[1] > dt.timedelta(hours=th.realert_hours):
                self._seen[fp] = (RANK[d.severity], now)
                new.append(d)
        return new


def should_replan(d: Disruption, last_replan: tuple[dt.datetime, str] | None, now: dt.datetime,
                  th: MonitorThresholds = MONITOR) -> bool:
    """Re-plan if the problem needs it, unless a replan ran recently for something at least as severe."""
    if not d.needs_replan:
        return False
    if last_replan is None:
        return True
    when, severity = last_replan
    return RANK[d.severity] > RANK.get(severity, 0) or now - when > dt.timedelta(minutes=th.replan_cooldown_min)


# ---------------------------------------------------------------- geometry
def distance_to_polyline_km(lat: float, lon: float, coords: list[list[float]]) -> float:
    """Shortest distance from a point to a [[lon, lat], ...] line (local flat-earth approximation)."""
    kx, ky = 111.32 * math.cos(math.radians(lat)), 110.57
    pts = [((x - lon) * kx, (y - lat) * ky) for x, y in coords]
    if len(pts) == 1:
        return math.hypot(*pts[0])
    best = math.inf
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        dx, dy = bx - ax, by - ay
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best


def sample_ahead(lat: float, lon: float, coords: list[list[float]], every_km: float, horizon_km: float) -> list[tuple[float, float]]:
    """(lat, lon) points every `every_km` along the line, from the vertex nearest the position, up to horizon_km."""
    if not coords:
        return []
    i = min(range(len(coords)), key=lambda k: haversine_km(lat, lon, coords[k][1], coords[k][0]))
    out, travelled, since = [(lat, lon)], 0.0, 0.0
    for (x1, y1), (x2, y2) in zip(coords[i:], coords[i + 1:]):
        step = haversine_km(y1, x1, y2, x2)
        travelled += step
        since += step
        if travelled > horizon_km:
            break
        if since >= every_km:
            out.append((y2, x2))
            since = 0.0
    return out
