"""Positions without a live fix: dead reckoning along the approved route.

Both providers take the shipment's latest stored Position and move it along
the *current* route path (Shipment.route geometry; straight line before a
route exists), then store a new Position. Stateless and restart-safe: the
state is the Position table; a reroute is followed immediately; never
overshoots the destination.

  ReplayProvider        DEMO_MODE=replay. Cruise speed x REPLAY_SPEED (SEA/RAIL;
                        AIR/ROAD replay in real time). source="replay"
  InterpolatingProvider DEMO_MODE=live, no fix for > 1 h (or no tracker / live provider
                        down). Last known speed, real time. source="interpolated"
                        (UI: dashed trail, "estimated")
"""
from __future__ import annotations

from django.conf import settings
from django.utils import timezone

from core.geo import remaining_path, shipment_path, walk
from core.models import Position, Shipment
from core.services.shipments import current_position, speed_kmh
from integrations.base import CRUISE, PositionDTO, TrackingProvider

MAX_STEP_SECONDS = 600  # cap per step, so a long collector downtime doesn't teleport ships
KEY_PREFIX = "shipment:"
# Only slow modes are time-compressed: a ship at 12 kn barely moves on a world
# map, but a 6 h flight at 1000x would land within one poll.
COMPRESSED_MODES = {"SEA", "RAIL"}


def replay_key(shipment: Shipment) -> str:
    return f"{KEY_PREFIX}{shipment.pk}"


class _DeadReckoningProvider(TrackingProvider):
    source = "replay"

    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode

    def _resolve(self, keys: list[str]) -> dict[str, Shipment]:
        pks = [int(k[len(KEY_PREFIX):]) for k in keys if k.startswith(KEY_PREFIX)]
        trackers = [k for k in keys if not k.startswith(KEY_PREFIX)]
        found = {}
        for s in Shipment.objects.filter(pk__in=pks).select_related("route"):
            found[replay_key(s)] = s
        for s in Shipment.objects.filter(tracker_id__in=trackers).select_related("route"):
            found[s.tracker_id] = s
        return found

    def _kmh(self, shipment: Shipment, latest: Position | None) -> tuple[float, float]:
        """(km/h used for the step, speed to store in native units)."""
        raise NotImplementedError

    def fetch(self, tracker_ids: list[str]) -> list[PositionDTO]:
        now = timezone.now()
        out = []
        for key, shipment in self._resolve(tracker_ids).items():
            path = shipment_path(shipment)
            if not path:
                continue
            latest = current_position(shipment)
            if latest is None:
                anchor, step_s = path[0], 0.0
            else:
                anchor = [latest.lat, latest.lon]
                step_s = min(max((now - latest.ts).total_seconds(), 0.0), MAX_STEP_SECONDS)
            kmh, native = self._kmh(shipment, latest)
            (lat, lon), heading, arrived = walk(remaining_path(path, anchor), kmh * step_s / 3600)
            out.append(PositionDTO(
                tracker_id=key, lat=lat, lon=lon, ts=now,
                speed=0.0 if arrived else native, heading=round(heading, 1), source=self.source,
            ))
        return out


class ReplayProvider(_DeadReckoningProvider):
    name = "replay"
    source = "replay"

    def __init__(self, mode: str, speedup: float | None = None) -> None:
        super().__init__(mode)
        default = settings.REPLAY_SPEED if mode in COMPRESSED_MODES else 1.0
        self.speedup: float = float(speedup if speedup is not None else default)

    def _kmh(self, shipment, latest):
        cruise = CRUISE.get(shipment.mode, CRUISE["SEA"])
        return cruise["kmh"] * self.speedup, cruise["native"]


class InterpolatingProvider(_DeadReckoningProvider):
    name = "interpolated"
    source = "interpolated"

    def _kmh(self, shipment, latest):
        cruise = CRUISE.get(shipment.mode, CRUISE["SEA"])
        last = latest.speed if latest is not None and latest.speed else None
        kmh = speed_kmh(last, shipment.mode) if last else None
        if not kmh or kmh < 1:
            return cruise["kmh"], cruise["native"]
        return kmh, float(last)  # type: ignore[arg-type]
