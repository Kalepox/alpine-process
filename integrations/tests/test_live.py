"""GET /api/live/: the feed C's map polls."""
from datetime import timedelta

import pytest
from django.utils import timezone

from core.services import audit, shipments

pytestmark = pytest.mark.django_db


def test_live_requires_login(client):
    assert client.get("/api/live/").status_code == 401


def test_live_payload(client, django_user_model, make_shipment, add_position):
    user = django_user_model.objects.create_user("viewer", password="x")
    client.force_login(user)
    s = make_shipment(tracker_id="259000420")
    make_shipment(ref="Planning only", status="PLANNING")
    add_position(s, 5.9, 95.3, ago=timedelta(minutes=10), source="aisstream")
    first = client.get("/api/live/").json()

    assert set(first) >= {"server_time", "status", "shipments", "positions", "events", "alerts", "traffic"}
    [ship] = first["shipments"]  # PLANNING shipments have nothing to show
    assert ship["id"] == s.pk and ship["pos"] == [5.9, 95.3] and ship["live"] and ship["mmsi"] == "259000420"
    assert first["status"] == {"mode": "replay", "vessels": 1, "connected": True}

    since = first["server_time"]
    shipments.record_position(s, 6.0, 94.0, timezone.now(), "interpolated", speed=12.5)
    audit.log("test.after", actor_type="system", actor_name="t")
    nxt = client.get("/api/live/", {"since": since}).json()
    assert [p["source"] for p in nxt["positions"]] == ["interpolated"]
    assert [e["action"] for e in nxt["events"]] == ["test.after"]
    assert nxt["shipments"][0]["estimated"] is True
