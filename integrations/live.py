"""Live feed for the map (DEV_PLAN v2 A.3): GET /api/live/?since=<ISO ts | epoch>

Cheap enough to poll every 2-5 s. Shape (a superset of what web/static/web/map.js reads):

  {
    "server_time": ISO,                       # pass back as ?since= on the next poll
    "status":    {"mode": "live"|"replay", "vessels": n_live_fixes_last_30min, "connected": bool},
    "shipments": [{id, ref, mode, status, pos: [lat, lon], heading, speed, source, ts,
                   live, estimated, vessel, mmsi, progress_pct, current_leg_index, eta_current}],
    "positions": [{shipment_id, lat, lon, ts, speed, heading, source}],   # since ?since=, for trails
    "events":    [{seq, ts, actor_type, actor_name, action, shipment_id, document_id, detail}],
    "alerts":    [{id, shipment_id, code, severity, message, created_at}],  # open
    "traffic":   []                           # other vessels: not collected by A (yet)
  }
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_tz

from django.conf import settings
from django.utils import timezone

from core.models import Alert, AuditEvent, Position, Shipment
from core.services.shipments import LIVE_SOURCES, current_position

MAX_POSITIONS = 2000
MAX_EVENTS = 50
DEFAULT_EVENTS = 20


def parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=dt_tz.utc)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace(" ", "+"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else timezone.make_aware(parsed, dt_tz.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def live_feed(since: datetime | None = None) -> dict:
    now = timezone.now()
    ships = list(Shipment.objects.exclude(status=Shipment.Status.PLANNING).order_by("pk"))
    shipments_out = []
    for s in ships:
        p = current_position(s)
        if p is None:
            continue
        live = p.source in LIVE_SOURCES
        shipments_out.append({
            "id": s.pk, "ref": s.ref, "mode": s.mode, "status": s.status,
            "pos": [p.lat, p.lon], "heading": p.heading, "speed": p.speed, "source": p.source, "ts": _iso(p.ts),
            "live": live, "estimated": p.source == Position.Source.INTERPOLATED,
            "vessel": s.tracker_id if live else "", "mmsi": s.tracker_id if (live and s.mode == "SEA") else None,
            "progress_pct": float(s.progress_pct), "current_leg_index": s.current_leg_index,
            "eta_current": _iso(s.eta_current),
        })

    positions = []
    if since is not None:
        for p in Position.objects.filter(ts__gt=since).order_by("ts")[:MAX_POSITIONS]:
            positions.append({"shipment_id": p.shipment_id, "lat": p.lat, "lon": p.lon, "ts": _iso(p.ts),
                              "speed": p.speed, "heading": p.heading, "source": p.source})

    events_qs = AuditEvent.objects.order_by("-seq")
    events_qs = events_qs.filter(ts__gt=since)[:MAX_EVENTS] if since is not None else events_qs[:DEFAULT_EVENTS]
    events = [{"seq": e.seq, "ts": _iso(e.ts), "actor_type": e.actor_type, "actor_name": e.actor_name,
               "action": e.action, "shipment_id": e.shipment_id, "document_id": e.document_id,
               "detail": e.detail[:300]} for e in reversed(list(events_qs))]

    alerts = [{"id": a.pk, "shipment_id": a.shipment_id, "code": a.code, "severity": a.severity,
               "message": a.message, "created_at": _iso(a.created_at)}
              for a in Alert.objects.filter(resolved=False).order_by("-created_at")]

    recent_live = Position.objects.filter(source__in=LIVE_SOURCES, ts__gte=now - timedelta(minutes=30))
    vessels = recent_live.values("shipment_id").distinct().count()
    return {
        "server_time": _iso(now),
        "status": {"mode": settings.DEMO_MODE, "vessels": vessels, "connected": vessels > 0},
        "shipments": shipments_out,
        "positions": positions,
        "events": events,
        "alerts": alerts,
        "traffic": [],
    }
