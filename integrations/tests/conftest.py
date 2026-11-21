from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from core.geo import feature_collection
from core.models import Corridor, Position, RouteOption, Shipment
from integrations import collectors, registry

# Indian Ocean -> Bab-el-Mandeb -> Suez -> Mersin, and a Cape alternative
RED_SEA_PATH = [[5.9, 95.3], [12.6, 43.4], [27.9, 33.6], [31.3, 32.3], [36.78, 34.63]]
CAPE_PATH = [[5.9, 95.3], [-34.8, 20.0], [14.9, -17.5], [36.0, -6.0], [36.78, 34.63]]
ROAD_PATH = [[51.95, 4.05], [47.56, 7.59], [45.46, 9.19]]


@pytest.fixture(autouse=True)
def _fresh_registry(settings):
    settings.DEMO_MODE = "replay"
    settings.REPLAY_SPEED = 60
    registry.reset()
    collectors._last_error_audit.clear()
    yield
    registry.reset()


@pytest.fixture
def corridors(db):
    return {
        "SUEZ": Corridor.objects.create(code="SUEZ", name="Suez Canal", mode="SEA"),
        "CAPE": Corridor.objects.create(code="CAPE", name="Cape of Good Hope", mode="SEA"),
    }


def make_route(shipment, path, codes=(), *, label="route", delay_days=0, chosen=True):
    """Test fixture shortcut: an approved route without going through the services."""
    leg = {"from": shipment.origin, "to": shipment.destination, "mode": shipment.mode, "corridors": list(codes)}
    option = RouteOption.objects.create(
        shipment=shipment, label=label, legs=[leg], geometry=feature_collection([path], [leg]),
        transit_days=Decimal("10"), delay_days=Decimal(delay_days), chosen=chosen,
    )
    if chosen:
        Shipment.objects.filter(pk=shipment.pk).update(route=option)
        shipment.route = option
    return option


@pytest.fixture
def make_shipment(corridors):
    def _make(ref="MV Test", mode="SEA", path=None, tracker_id="", departed_days=-5, eta_days=10,
              status="ON_TRACK", **extra):
        path = path or (ROAD_PATH if mode == "ROAD" else RED_SEA_PATH)
        now = timezone.now()
        s = Shipment.objects.create(
            ref=ref, mode=mode, tracker_id=tracker_id, cargo="Test cargo", quantity=Decimal("1"),
            cargo_value=Decimal("1000"), origin="A", destination="B", status=status,
            origin_latlon=list(path[0]), dest_latlon=list(path[-1]),
            departed_at=now + timedelta(days=departed_days),
            eta_planned=now + timedelta(days=eta_days), eta_current=now + timedelta(days=eta_days), **extra,
        )
        if status != "PLANNING":
            make_route(s, path, ["SUEZ"] if path is RED_SEA_PATH else [])
        return s

    return _make


@pytest.fixture
def add_position():
    def _add(shipment, lat, lon, ago=timedelta(minutes=5), source="replay", speed=None):
        return Position.objects.create(shipment=shipment, lat=lat, lon=lon, ts=timezone.now() - ago,
                                       source=source, speed=speed)

    return _add
