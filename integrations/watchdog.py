"""Staleness watchdog (A.3), run every WATCHDOG_SECONDS by run_collectors.

  AIS_SIGNAL_LOST  live-tracked SEA shipment with no live position for > 6 h,
                   AIR for > 30 min (only in DEMO_MODE=live, only with a tracker_id;
                   replayed positions don't count as signal)
  ETA_SLIP         eta_current later than eta_planned by > 48 h, beyond the delay
                   of an already approved (chosen) reroute

The watchdog only raises alerts; a fresh live position clears AIS_SIGNAL_LOST
(see shipments.record_position). ETA_SLIP stays until a human deals with it.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from core.models import Alert, Severity, Shipment
from core.services import shipments as shipment_svc

STALE_AFTER = {"SEA": timedelta(hours=6), "AIR": timedelta(minutes=30)}
ETA_SLIP_AFTER = timedelta(hours=48)
ACTOR = "collector.watchdog"


def _hours(td: timedelta) -> str:
    return f"{td.total_seconds() / 3600:.1f} h"


def run_watchdog(now: datetime | None = None) -> list[Alert]:
    now = now or timezone.now()
    raised = []
    ships = (
        Shipment.objects.exclude(status=Shipment.Status.ARRIVED)
        .filter(departed_at__lte=now)
        .prefetch_related("route_options")
    )
    for s in ships:
        # ---- signal lost
        limit = STALE_AFTER.get(s.mode)
        if limit and s.tracker_id and settings.DEMO_MODE == "live":
            latest = s.positions.exclude(source="replay").order_by("-ts").first()
            last_seen = latest.ts if latest else s.departed_at
            if last_seen is not None and now - last_seen > limit:
                where = f" (last at {latest.lat:.2f}, {latest.lon:.2f})" if latest else ""
                signal = "AIS" if s.mode == "SEA" else "ADS-B"
                raised.append(shipment_svc.raise_alert(
                    s, Alert.Code.AIS_SIGNAL_LOST, Severity.WARN,
                    f"No {signal} position for {_hours(now - last_seen)}{where}.", actor_name=ACTOR,
                ))

        # ---- ETA slip
        if s.eta_planned and s.eta_current:
            approved_delay = max((ro.delay_days for ro in s.route_options.all() if ro.chosen), default=0)
            slip = s.eta_current - s.eta_planned - timedelta(days=approved_delay)
            if slip > ETA_SLIP_AFTER:
                raised.append(shipment_svc.raise_alert(
                    s, Alert.Code.ETA_SLIP, Severity.WARN,
                    f"ETA slipped {_hours(slip)} beyond plan"
                    + (f" (+{approved_delay} d approved reroute)" if approved_delay else "") + ".",
                    actor_name=ACTOR,
                ))
    return raised
