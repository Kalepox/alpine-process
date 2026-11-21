"""Providers (DB), registry, collector rounds, watchdog, track cache."""
from datetime import timedelta
from unittest import mock

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.geo import haversine_km
from core.models import AuditEvent, Position, RouteOption
from integrations import registry
from integrations.collectors import poll_once
from integrations.opensky import OpenSkyProvider
from integrations.replay import ReplayProvider, replay_key
from integrations.routes import RoutesProvider
from integrations.tracks import dump_tracks, load_tracks
from integrations.watchdog import run_watchdog

pytestmark = pytest.mark.django_db


class FakeResponse:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


# ------------------------------------------------------------------ replay
def test_replay_moves_at_compressed_cruise_speed(make_shipment, add_position):
    s = make_shipment()
    add_position(s, 5.9, 95.3, ago=timedelta(minutes=5))
    [dto] = ReplayProvider("SEA", speedup=60).fetch([replay_key(s)])
    expected_km = 23.15 * 60 * 300 / 3600  # 115.75 km
    assert haversine_km([5.9, 95.3], [dto.lat, dto.lon]) == pytest.approx(expected_km, rel=0.01)
    assert dto.source == "replay" and dto.speed == 12.5


def test_replay_air_runs_in_real_time(make_shipment, add_position):
    s = make_shipment(mode="AIR", origin=(25.25, 55.36), dest=(41.26, 28.74))
    add_position(s, 25.25, 55.36, ago=timedelta(minutes=1))
    [dto] = ReplayProvider("AIR").fetch([replay_key(s)])
    assert haversine_km([25.25, 55.36], [dto.lat, dto.lon]) == pytest.approx(889 / 60, rel=0.02)


def test_replay_caps_long_gaps_and_never_overshoots(make_shipment, add_position):
    s = make_shipment()
    add_position(s, 31.0, 32.5, ago=timedelta(days=3))  # just before Port Said, collector was down for days
    [dto] = ReplayProvider("SEA", speedup=1000).fetch([replay_key(s)])
    assert [dto.lat, dto.lon] == pytest.approx([36.78, 34.63]) and dto.speed == 0.0


def test_replay_follows_a_reroute(make_shipment, add_position, corridors):
    s = make_shipment()
    add_position(s, 5.9, 95.3)
    [before] = ReplayProvider("SEA").fetch([replay_key(s)])
    s.corridor = corridors["CAPE"]
    s.save()
    [after] = ReplayProvider("SEA").fetch([replay_key(s)])
    assert 270 < before.heading < 300  # west-north-west towards the Red Sea
    assert 225 < after.heading < 270  # south-west towards the Cape


# ---------------------------------------------------------------- registry
def test_registry_replay_mode():
    for mode in ("SEA", "AIR", "ROAD", "RAIL"):
        assert isinstance(registry.get_provider(mode), ReplayProvider)


def test_registry_live_falls_back_without_key_and_after_failures(settings):
    settings.DEMO_MODE, settings.AISSTREAM_API_KEY = "live", ""
    assert isinstance(registry.get_provider("SEA"), ReplayProvider)
    assert isinstance(registry.get_provider("ROAD"), RoutesProvider)
    assert isinstance(registry.get_provider("RAIL"), ReplayProvider)
    air = registry.get_provider("AIR")
    assert isinstance(air, OpenSkyProvider)
    for _ in range(air.max_failures):
        air.record_failure(RuntimeError("429"))
    assert isinstance(registry.get_provider("AIR"), ReplayProvider)


# ------------------------------------------------------------------ opensky
def test_opensky_oauth_then_states():
    session = mock.Mock()
    session.post.return_value = FakeResponse({"access_token": "tok", "expires_in": 1800})
    session.get.return_value = FakeResponse({"time": 1, "states": [
        ["4b1815", "SWR1", "CH", 1763719920, 1, 8.5, 47.4, 0, False, 100.0, 45.0]]})
    p = OpenSkyProvider("id", "secret", session=session)
    [dto] = p.fetch(["4B1815"])
    assert dto.tracker_id == "4b1815"
    assert session.get.call_args.kwargs["headers"] == {"Authorization": "Bearer tok"}
    assert session.get.call_args.kwargs["params"] == [("icao24", "4b1815")]
    p.fetch(["4b1815"])
    assert session.post.call_count == 1  # token cached


# ------------------------------------------------------------------ routes
def test_routes_fallback_without_key(make_shipment):
    s = make_shipment(ref="TRK", mode="ROAD", corridor="EU_ROAD", tracker_id="GX-1",
                      origin=(51.95, 4.05), dest=(45.46, 9.19), departed_days=0, eta_days=1)
    p = RoutesProvider("")
    [dto] = p.fetch(["GX-1"])
    assert dto.source == "routes" and [dto.lat, dto.lon] == pytest.approx([51.95, 4.05])
    eta = p.etas()["GX-1"]
    km = haversine_km([51.95, 4.05], [45.46, 9.19]) * 1.3
    assert (eta - timezone.now()).total_seconds() / 3600 == pytest.approx(km / 70, rel=0.02)


def test_routes_uses_google_when_available(make_shipment):
    make_shipment(ref="TRK", mode="ROAD", corridor="EU_ROAD", tracker_id="GX-1", origin=(38.5, -120.2),
                  dest=(43.252, -126.453), departed_days=0, eta_days=1)
    session = mock.Mock()
    session.post.return_value = FakeResponse({"routes": [{
        "duration": "36000s", "distanceMeters": 900000, "polyline": {"encodedPolyline": "_p~iF~ps|U_ulLnnqC_mqNvxq`@"}}]})
    p = RoutesProvider("KEY", session=session)
    p.fetch(["GX-1"])
    route = p._routes[next(iter(p._routes))][1]
    assert route.source == "google" and len(route.path) == 3 and route.kmh == pytest.approx(90)
    assert session.post.call_args.kwargs["headers"]["X-Goog-Api-Key"] == "KEY"


def test_routes_google_error_falls_back(make_shipment):
    make_shipment(ref="TRK", mode="ROAD", corridor="EU_ROAD", tracker_id="GX-1", departed_days=0, eta_days=1)
    session = mock.Mock()
    session.post.return_value = FakeResponse({}, status=403)
    p = RoutesProvider("BADKEY", session=session)
    assert len(p.fetch(["GX-1"])) == 1 and p.failures == 1


# --------------------------------------------------------------- collectors
def test_poll_once_replay_moves_active_shipments_only(make_shipment):
    moving = make_shipment(ref="Moving")
    make_shipment(ref="Not yet departed", departed_days=2)
    make_shipment(ref="Arrived", status="ARRIVED")
    assert poll_once("SEA") == 1
    assert Position.objects.get().shipment == moving


def test_poll_once_live_failure_is_audited_and_replay_keeps_map_alive(settings, make_shipment):
    settings.DEMO_MODE = "live"
    tracked = make_shipment(ref="Tracked", mode="AIR", tracker_id="4b1815", corridor="RED_SEA_SUEZ")
    untracked = make_shipment(ref="Untracked", mode="AIR", corridor="RED_SEA_SUEZ")
    air = registry.get_provider("AIR")
    with mock.patch.object(air, "fetch", side_effect=RuntimeError("OpenSky 503")):
        assert poll_once("AIR") == 1  # untracked one still moved by replay
        poll_once("AIR")
    assert untracked.positions.count() == 2 and not tracked.positions.exists()
    errors = AuditEvent.objects.filter(action="collector.error")
    assert errors.count() == 1 and "OpenSky 503" in errors.get().detail  # rate-limited
    assert air.failures == 2


def test_poll_once_road_updates_eta(settings, make_shipment):
    settings.DEMO_MODE = "live"
    s = make_shipment(ref="TRK", mode="ROAD", corridor="EU_ROAD", tracker_id="GX-1",
                      origin=(51.95, 4.05), dest=(45.46, 9.19), departed_days=0, eta_days=5)
    poll_once("ROAD")
    s.refresh_from_db()
    assert s.positions.get().source == "routes"
    assert (s.eta_current - timezone.now()) < timedelta(days=1)
    assert AuditEvent.objects.filter(action="shipment.eta_updated", shipment=s).exists()


def test_run_collectors_once_command(make_shipment, capsys):
    make_shipment()
    call_command("run_collectors", "--once")
    out = capsys.readouterr().out
    assert "SEA: replay" in out and "SEA: stored 1 position(s)" in out


# ---------------------------------------------------------------- watchdog
def test_watchdog_signal_lost_only_for_live_tracked(settings, make_shipment, add_position):
    s = make_shipment(tracker_id="259000420")
    add_position(s, 10, 60, ago=timedelta(hours=7), source="aisstream")
    add_position(s, 11, 61, ago=timedelta(minutes=1), source="replay")  # replay is not a signal
    assert run_watchdog() == []  # replay mode: no signal alerts
    settings.DEMO_MODE = "live"
    [alert] = run_watchdog()
    assert alert.code == "AIS_SIGNAL_LOST" and "7.0 h" in alert.message
    s.refresh_from_db()
    assert s.status == "ATTENTION"
    from core.services.shipments import record_position

    record_position(s, 12, 62, timezone.now(), "aisstream")
    s.refresh_from_db()
    assert s.status == "ON_TRACK"


def test_watchdog_eta_slip_respects_approved_reroute(make_shipment):
    s = make_shipment()
    s.eta_current = s.eta_planned + timedelta(days=12)
    s.save()
    RouteOption.objects.create(shipment=s, label="Cape", mode="SEA", transit_days=38, delay_days=12, chosen=True)
    assert run_watchdog() == []
    s.eta_current = s.eta_planned + timedelta(days=15)
    s.save()
    [alert] = run_watchdog()
    assert alert.code == "ETA_SLIP" and "+12 d approved reroute" in alert.message


# ------------------------------------------------------------------ tracks
def test_tracks_roundtrip_is_time_shifted(tmp_path, make_shipment, add_position):
    s = make_shipment(ref="MV Atacama Star")
    for i, ago in enumerate([timedelta(days=2, hours=2), timedelta(days=2, hours=1), timedelta(days=2)]):
        add_position(s, 10 + i, 60 + i, ago=ago, source="aisstream")
    add_position(s, 0, 0, ago=timedelta(0), source="replay")
    out = tmp_path / "tracks.json"
    assert dump_tracks(out) == {"MV Atacama Star": 3}  # replay excluded

    Position.objects.all().delete()
    add_position(s, 5.9, 95.3, ago=timedelta(0), source="replay")  # fresh seed start point
    end = timezone.now()
    assert load_tracks(out, end_at=end) == {"MV Atacama Star": 3}
    latest = s.positions.order_by("-ts").first()
    assert latest.ts == end and latest.source == "aisstream" and latest.lat == 12
    assert not s.positions.filter(source="replay").exists()
    assert load_tracks(out, end_at=end) == {"MV Atacama Star": 0}  # idempotent
