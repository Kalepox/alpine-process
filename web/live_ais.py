"""
Live vessel positions from aisstream.io for the map.

UI-side stand-in for A's `integrations/aisstream.py` collector (DEV_PLAN A.3).
A background thread keeps one WebSocket open and holds the latest position of every
vessel it hears in memory. After merge, A's collector writes `Position` rows and
`web/data.py` reads those instead; this module can then be deleted.

Our cargo is fictional, so each SEA shipment is bound to a real vessel:
  1. explicit binding from settings.AIS_BINDINGS {shipment_id: mmsi}, else
  2. the nearest moving real vessel of a matching type near the shipment's planned position.
The API key never reaches the browser.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import threading
import time
from datetime import datetime, timezone as dt_tz

from django.conf import settings

log = logging.getLogger(__name__)

URL = "wss://stream.aisstream.io/v0/stream"
STALE_S = 30 * 60          # forget vessels not heard for 30 min
WARMUP_S = 15              # collect a bit before binding shipments
REFINE_S = 5 * 60          # for 5 min after binding, switch if a much closer vessel appears
MAX_VESSELS = 40000


class AisFeed:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.lock = threading.Lock()
        self.vessels: dict[int, dict] = {}      # mmsi -> latest state
        self.static: dict[int, dict] = {}       # mmsi -> name, type, destination
        self.bindings: dict[int, int] = {}      # shipment_id -> mmsi
        self._bound_at: dict[int, float] = {}   # shipment_id -> when auto-bound
        self._checked_at: dict[int, float] = {}
        self.connected = False
        self.started_at = time.time()
        self.messages = 0
        self.last_error = ""
        self._thread = threading.Thread(target=self._run, name="aisstream", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------ stream
    def _run(self):
        backoff = 2
        while True:
            try:
                asyncio.run(self._consume())
                backoff = 2
            except Exception as e:  # never crash the web process
                self.connected = False
                self.last_error = str(e)[:200]
                log.warning("aisstream: %s (retry in %ss)", e, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _consume(self):
        import websockets
        async with websockets.connect(URL, ping_interval=20, open_timeout=30, max_size=2 ** 20) as ws:
            await ws.send(json.dumps({
                "APIKey": self.api_key,
                "BoundingBoxes": [[[-90, -180], [90, 180]]],
                "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
            }))
            self.connected = True
            self.last_error = ""
            async for raw in ws:
                msg = json.loads(raw)
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                self._handle(msg)

    def _handle(self, msg):
        mt = msg.get("MessageType")
        md = msg.get("MetaData", {})
        mmsi = md.get("MMSI")
        if not mmsi:
            return
        self.messages += 1
        if mt == "PositionReport":
            p = msg["Message"]["PositionReport"]
            lat, lon = p.get("Latitude"), p.get("Longitude")
            if lat is None or abs(lat) > 90 or abs(lon) > 180 or (lat == 0 and lon == 0):
                return
            hdg = p.get("TrueHeading")
            with self.lock:
                self.vessels[mmsi] = dict(
                    mmsi=mmsi, lat=round(lat, 5), lon=round(lon, 5), sog=p.get("Sog") or 0, cog=p.get("Cog") or 0,
                    heading=hdg if hdg is not None and hdg < 360 else (p.get("Cog") or 0),
                    name=(md.get("ShipName") or "").strip(), t=time.time(),
                )
                if len(self.vessels) > MAX_VESSELS:
                    self._prune()
        elif mt == "ShipStaticData":
            s = msg["Message"]["ShipStaticData"]
            with self.lock:
                self.static[mmsi] = dict(name=(s.get("Name") or "").strip(), type=s.get("Type") or 0,
                                         destination=(s.get("Destination") or "").strip())

    def _prune(self):
        cutoff = time.time() - STALE_S
        for k in [k for k, v in self.vessels.items() if v["t"] < cutoff]:
            del self.vessels[k]

    # ------------------------------------------------------------ queries
    def status(self):
        with self.lock:
            fresh = sum(1 for v in self.vessels.values() if time.time() - v["t"] < STALE_S)
        return dict(enabled=True, connected=self.connected, vessels=fresh, messages=self.messages, error=self.last_error)

    def _vessel(self, mmsi):
        v = self.vessels.get(mmsi)
        if not v or time.time() - v["t"] > STALE_S:
            return None
        st = self.static.get(mmsi, {})
        return dict(v, name=st.get("name") or v["name"], type=st.get("type", 0), destination=st.get("destination", ""))

    def traffic(self, bbox=None, limit=4000):
        """[[mmsi, lat, lon, cog, sog, name], ...] inside bbox (w, s, e, n)."""
        now = time.time()
        out = []
        with self.lock:
            for v in self.vessels.values():
                if now - v["t"] > STALE_S:
                    continue
                if bbox and not (bbox[1] <= v["lat"] <= bbox[3] and _lon_in(v["lon"], bbox[0], bbox[2])):
                    continue
                out.append([v["mmsi"], v["lat"], v["lon"], round(v["cog"]), round(v["sog"], 1),
                            self.static.get(v["mmsi"], {}).get("name") or v["name"]])
                if len(out) >= limit:
                    break
        return out

    def live_for(self, shipment) -> dict | None:
        """Live position for one of our SEA shipments, binding it to a real vessel if needed."""
        if shipment.get("mode") != "SEA":
            return None
        sid = shipment["id"]
        with self.lock:
            explicit = (getattr(settings, "AIS_BINDINGS", {}) or {}).get(sid)
            mmsi = explicit or self.bindings.get(sid)
            v = self._vessel(mmsi) if mmsi else None
            now = time.time()
            if explicit or now - self.started_at < WARMUP_S:
                return v
            # (re)bind: when unbound/stale, or during the first minutes if a much closer vessel shows up
            refine = v is not None and now - self._bound_at.get(sid, 0) < REFINE_S and now - self._checked_at.get(sid, 0) > 20
            if v is None or refine:
                self._checked_at[sid] = now
                cand, d = self._pick(shipment, exclude=mmsi)
                cur_d = self._score(shipment, mmsi) if v else None
                if cand and (cur_d is None or d < cur_d * 0.7):
                    self.bindings[sid] = cand
                    self._bound_at.setdefault(sid, now)
                    v = self._vessel(cand)
            return v

    def _score(self, s, mmsi):
        """How well a real vessel stands in for our shipment (lower is better):
        distance to the shipment's route + a bit of distance to where it should be now,
        tripled when the vessel type doesn't fit the cargo."""
        v = self.vessels.get(mmsi)
        if not v:
            return None
        lat0, lon0 = s["_plan_pos"]
        to_point = _dist_km(lat0, lon0, v["lat"], v["lon"])
        route = s.get("_route_pts")
        to_route = min(_dist_km(a, b, v["lat"], v["lon"]) for a, b in route) if route else to_point
        want_tanker = "oil" in s.get("cargo", "").lower()
        typ = self.static.get(mmsi, {}).get("type", 0)
        kind_ok = (80 <= typ <= 89) if want_tanker else (70 <= typ <= 79)
        return (to_route + 0.5 * to_point) * (1 if kind_ok else 3)

    def _pick(self, s, exclude=None):
        """Best moving real vessel for this shipment -> (mmsi, score)."""
        taken = set(self.bindings.values()) - {exclude}
        best, best_d = None, None
        now = time.time()
        box = s.get("_route_box")
        for mmsi, v in self.vessels.items():
            if mmsi in taken or mmsi == exclude or now - v["t"] > 15 * 60 or v["sog"] > 30:
                continue
            if box and not (box[0] <= v["lat"] <= box[2] and box[1] <= v["lon"] <= box[3]):
                continue
            moving = v["sog"] >= 3 if s.get("status") != "ARRIVED" else v["sog"] < 1
            if not moving:
                continue
            d = self._score(s, mmsi)
            if best_d is None or d < best_d:
                best, best_d = mmsi, d
        return best, best_d


def route_points(waypoints, step_km=150):
    """Densify a [[lat, lon], ...] polyline so point distance approximates distance to the line."""
    pts = []
    for (a, b), (c, d) in zip(waypoints, waypoints[1:]):
        n = max(1, int(_dist_km(a, b, c, d) // step_km))
        pts += [(a + (c - a) * i / n, b + (d - b) * i / n) for i in range(n)]
    if waypoints:
        pts.append(tuple(waypoints[-1]))
    return pts


def route_box(waypoints, pad=4):
    lats = [p[0] for p in waypoints]
    lons = [p[1] for p in waypoints]
    return (min(lats) - pad, min(lons) - pad, max(lats) + pad, max(lons) + pad)


def _lon_in(lon, w, e):
    return w <= lon <= e if w <= e else (lon >= w or lon <= e)


def _dist_km(a, b, c, d):
    x = math.radians(d - b) * math.cos(math.radians((a + c) / 2))
    y = math.radians(c - a)
    return 6371 * math.hypot(x, y)


_feed: AisFeed | None = None
_feed_lock = threading.Lock()


def feed() -> AisFeed | None:
    """Started lazily on first use, so only the process that serves requests connects."""
    global _feed
    key = getattr(settings, "AISSTREAM_API_KEY", "")
    if not key or getattr(settings, "DEMO_MODE", "live") == "replay":
        return None
    with _feed_lock:
        if _feed is None:
            _feed = AisFeed(key)
    return _feed


def status():
    f = feed()
    return f.status() if f else dict(enabled=False, connected=False, vessels=0, messages=0, error="")


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, dt_tz.utc).isoformat()
