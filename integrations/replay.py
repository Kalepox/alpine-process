"""ReplayProvider: positions without network (DEMO_MODE=replay, or fallback).

Stateless dead-reckoning on a compressed clock: every fetch takes the
shipment's latest stored Position (live, cached from tracks.json, or the seed
start point) and moves it along the *current* planned path
(origin -> corridor waypoints -> destination) at cruise speed x speedup
(DEMO_REPLAY_SPEEDUP for SEA/RAIL; AIR/ROAD replay in real time).

  * restart-safe: the state is the Position table, so no jumps
  * follows a reroute immediately (the path is recomputed from the corridor)
  * never overshoots the destination
"""
from __future__ import annotations

from django.conf import settings
from django.utils import timezone

from core.geo import remaining_path, shipment_path, walk
from core.models import Shipment
from integrations.base import CRUISE, PositionDTO, TrackingProvider

MAX_STEP_SECONDS = 600  # cap per step, so a long collector downtime doesn't teleport ships
KEY_PREFIX = "shipment:"
# Only slow modes are time-compressed: a ship at 12 kn barely moves on a world
# map, but a 6 h flight at 60x would land within one poll.
COMPRESSED_MODES = {"SEA", "RAIL"}


def replay_key(shipment: Shipment) -> str:
    return f"{KEY_PREFIX}{shipment.pk}"


class ReplayProvider(TrackingProvider):
    name = "replay"

    def __init__(self, mode: str, speedup: float | None = None) -> None:
        super().__init__()
        self.mode = mode
        if speedup is None:
            speedup = settings.DEMO_REPLAY_SPEEDUP if mode in COMPRESSED_MODES else 1.0
        self.speedup = speedup

    def _resolve(self, keys: list[str]) -> dict[str, Shipment]:
        pks = [int(k[len(KEY_PREFIX):]) for k in keys if k.startswith(KEY_PREFIX)]
        trackers = [k for k in keys if not k.startswith(KEY_PREFIX)]
        found = {}
        for s in Shipment.objects.filter(pk__in=pks).select_related("corridor"):
            found[replay_key(s)] = s
        for s in Shipment.objects.filter(tracker_id__in=trackers).select_related("corridor"):
            found[s.tracker_id] = s
        return found

    def fetch(self, tracker_ids: list[str]) -> list[PositionDTO]:
        now = timezone.now()
        out = []
        for key, shipment in self._resolve(tracker_ids).items():
            path = shipment_path(shipment)
            if not path:
                continue
            latest = shipment.positions.order_by("-ts").first()
            if latest is None:
                anchor, step_s = path[0], 0.0
            else:
                anchor = [latest.lat, latest.lon]
                step_s = min(max((now - latest.ts).total_seconds(), 0.0), MAX_STEP_SECONDS)
            cruise = CRUISE.get(shipment.mode, CRUISE["SEA"])
            km = cruise["kmh"] * self.speedup * step_s / 3600
            (lat, lon), heading, arrived = walk(remaining_path(path, anchor), km)
            out.append(PositionDTO(
                tracker_id=key, lat=lat, lon=lon, ts=now,
                speed=0.0 if arrived else cruise["native"], heading=round(heading, 1), source="replay",
            ))
        return out
