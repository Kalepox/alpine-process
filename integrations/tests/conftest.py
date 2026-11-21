from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from core.models import Corridor, Position, Shipment
from integrations import collectors, registry


@pytest.fixture(autouse=True)
def _fresh_registry(settings):
    settings.DEMO_MODE = "replay"
    settings.DEMO_REPLAY_SPEEDUP = 60
    registry.reset()
    collectors._last_error_audit.clear()
    yield
    registry.reset()


@pytest.fixture
def corridors(db):
    red = Corridor.objects.create(code="RED_SEA_SUEZ", name="Red Sea / Suez", mode="SEA",
                                  waypoints=[[12.6, 43.4], [27.9, 33.6], [31.3, 32.3]])
    cape = Corridor.objects.create(code="CAPE", name="Cape of Good Hope", mode="SEA",
                                   waypoints=[[-34.8, 20.0], [14.9, -17.5], [36.0, -6.0]])
    road = Corridor.objects.create(code="EU_ROAD", name="Road", mode="ROAD", waypoints=[[47.56, 7.59]])
    return {"RED_SEA_SUEZ": red, "CAPE": cape, "EU_ROAD": road}


@pytest.fixture
def make_shipment(corridors):
    def _make(ref="MV Test", mode="SEA", corridor="RED_SEA_SUEZ", tracker_id="", origin=(5.9, 95.3),
              dest=(36.78, 34.63), departed_days=-5, eta_days=10, **extra):
        now = timezone.now()
        return Shipment.objects.create(
            ref=ref, mode=mode, tracker_id=tracker_id, cargo="Test cargo", quantity=Decimal("1"),
            cargo_value=Decimal("1000"), origin="A", destination="B",
            origin_latlon=list(origin), dest_latlon=list(dest), corridor=corridors[corridor],
            departed_at=now + timedelta(days=departed_days),
            eta_planned=now + timedelta(days=eta_days), eta_current=now + timedelta(days=eta_days), **extra,
        )

    return _make


@pytest.fixture
def add_position():
    def _add(shipment, lat, lon, ago=timedelta(minutes=5), source="replay"):
        return Position.objects.create(shipment=shipment, lat=lat, lon=lon, ts=timezone.now() - ago, source=source)

    return _add
