"""Track cache: Position table <-> sample_data/tracks.json (so the demo never needs Wi-Fi).

  manage.py snapshot_tracks         -> dump recorded (non-replay) positions
  manage.py seed_demo --with-tracks -> load them back, time-shifted so each
                                       track ends "now"
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from django.utils import timezone

from core.models import Position, Shipment

FORMAT_VERSION = 1


def dump_tracks(path: Path, *, include_replay: bool = False, limit: int = 1000) -> dict[str, int]:
    """Write the latest `limit` positions per shipment to `path`. Returns {ref: count}."""
    shipments_out, counts = [], {}
    for s in Shipment.objects.order_by("ref"):
        qs = s.positions.all() if include_replay else s.positions.exclude(source=Position.Source.REPLAY)
        rows = list(qs.order_by("-ts")[:limit])[::-1]
        if not rows:
            continue
        counts[s.ref] = len(rows)
        shipments_out.append({
            "ref": s.ref, "tracker_id": s.tracker_id, "mode": s.mode,
            "positions": [
                {"ts": p.ts.isoformat(), "lat": p.lat, "lon": p.lon, "speed": p.speed,
                 "heading": p.heading, "source": p.source}
                for p in rows
            ],
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": FORMAT_VERSION,
        "generated_at": timezone.now().isoformat(),
        "note": "Real recorded positions (aisstream/OpenSky/Routes). Cargo and financials are sample data.",
        "shipments": shipments_out,
    }, indent=1), encoding="utf-8")
    return counts


def load_tracks(path: Path, *, end_at: datetime | None = None) -> dict[str, int]:
    """Load tracks for shipments that exist (matched by ref). Each track is
    shifted so its last point is at `end_at` (default now); the synthetic
    replay start point of that shipment is dropped. Returns {ref: count}."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    end_at = end_at or timezone.now()
    counts = {}
    for track in data.get("shipments", []):
        shipment = Shipment.objects.filter(ref=track["ref"]).first()
        rows = track.get("positions") or []
        if shipment is None or not rows:
            continue
        stamps = [datetime.fromisoformat(r["ts"]) for r in rows]
        shift: timedelta = end_at - max(stamps)
        shipment.positions.filter(source=Position.Source.REPLAY).delete()
        existing = set(shipment.positions.values_list("ts", "source"))
        new = [
            Position(shipment=shipment, lat=r["lat"], lon=r["lon"], speed=r.get("speed"),
                     heading=r.get("heading"), ts=ts + shift, source=r.get("source", "replay"))
            for r, ts in zip(rows, stamps)
            if (ts + shift, r.get("source", "replay")) not in existing
        ]
        Position.objects.bulk_create(new)
        counts[shipment.ref] = len(new)
    return counts
