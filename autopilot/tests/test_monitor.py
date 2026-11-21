"""route_monitor: detectors, dedupe/cooldown, and the snow-at-Brenner demo chain (no ORM)."""
import datetime as dt

import pytest

from autopilot.domain.risk import CorridorRisk, RiskOverlay
from autopilot.domain.schemas import Leg
from autopilot.domain.solver import RouteSolver
from autopilot.monitor.detectors import (
    MonitorMemory,
    Tracked,
    corridor_hits,
    distance_to_polyline_km,
    should_replan,
    tracking_hits,
    weather_hits,
    weather_points,
)
from autopilot.pipelines.monitor import scan, simulated_weather
from autopilot.tests.test_solver import RED_SEA_RISK, TODAY, corridors, request

NOW = dt.datetime(2026, 11, 21, 12, tzinfo=dt.timezone.utc)
MUNICH = (48.2, 11.6)


class FakeWeather:
    def __init__(self, snow=0.0, gusts=0.0, wave=1.0, fail=False):
        self.snow, self.gusts, self.wave, self.fail = snow, gusts, wave, fail
        self.land_points, self.sea_points = [], []

    def land(self, points):
        if self.fail:
            raise TimeoutError("open-meteo down")
        self.land_points += points
        return [{"snowfall_cm": self.snow, "gusts_kmh": self.gusts, "storm": False, "url": "https://api.open-meteo.com/x"}
                for _ in points]

    def marine(self, points):
        self.sea_points += points
        return [{"wave_m": self.wave, "url": "https://marine-api.open-meteo.com/x"} for _ in points]


@pytest.fixture(scope="module")
def truck():
    solver = RouteSolver()
    req = request("Rotterdam", "Milan", weight_t=20, value=200_000)
    route = next(o for o in solver.plan(req, RiskOverlay(), today=TODAY).options if "BRENNER" in corridors(o))
    return solver, req, Tracked(shipment_id=7, ref="TRUCK-7", legs=route.legs, position=MUNICH)


@pytest.fixture(scope="module")
def ship():
    solver = RouteSolver()
    req = request("Belawan", "Mersin", "LIQUID_BULK", 5000, 4_500_000)
    route = next(o for o in solver.plan(req, RED_SEA_RISK, today=TODAY).options if "BAB_EL_MANDEB" in corridors(o))
    return Tracked(shipment_id=3, ref="MT Aurelia Grace", legs=route.legs, position=(12.6, 50.0))


def test_corridor_hits():
    t = Tracked(1, "X", [Leg(from_node="A", to_node="B", mode="SEA", km=1, days=1, corridors=["SUEZ", "HORMUZ", "PANAMA"])])
    overlay = RiskOverlay([CorridorRisk("SUEZ", "CLOSED", 1.0, 14), CorridorRisk("HORMUZ", "RESTRICTED", 0.5, 10),
                           CorridorRisk("PANAMA", "RESTRICTED", 0.1, 5)])
    hits = {d.corridor: d for d in corridor_hits(t, overlay)}
    assert hits["SUEZ"].severity == "block" and hits["SUEZ"].needs_replan
    assert hits["HORMUZ"].alert_code == "CORRIDOR_AT_RISK" and hits["HORMUZ"].delay_days_est == 5.0
    assert "PANAMA" not in hits  # 0.5 expected days is not worth acting on


def test_off_route_and_eta_slip(ship):
    on_route = Tracked(3, "s", ship.legs, position=(ship.legs[0].geometry[5][1], ship.legs[0].geometry[5][0]))
    assert tracking_hits(on_route) == []
    far = Tracked(3, "s", ship.legs, position=(-30.0, 80.0), eta_planned=NOW, eta_current=NOW + dt.timedelta(days=3))
    codes = {d.alert_code: d for d in tracking_hits(far)}
    assert codes["OFF_ROUTE"].evidence["off_route_km"] > 50 and codes["ETA_SLIP"].delay_days_est == 3.0


def test_weather_checks_passes_ahead_and_the_current_voyage(truck, ship):
    land, sea = weather_points(truck[2])
    assert [p[3] for p in land] == ["BRENNER"] and sea == []
    land, sea = weather_points(ship)
    assert land == [] and 2 <= len(sea) <= 8  # ~3 days of voyage, every 300 km


def test_snow_and_storm_thresholds(truck, ship):
    hit = weather_hits(truck[2], FakeWeather(snow=30))[0]
    assert (hit.severity, hit.corridor, hit.alert_code) == ("block", "BRENNER", "WEATHER_RISK")
    assert hit.sources[0].url.startswith("https://api.open-meteo.com")
    assert weather_hits(truck[2], FakeWeather(gusts=85))[0].severity == "warn"
    assert weather_hits(truck[2], FakeWeather(snow=2, gusts=20)) == []
    storm = weather_hits(ship, FakeWeather(wave=8.5))[0]
    assert storm.severity == "block" and storm.evidence["wave_height_m"] == 8.5


def test_weather_errors_are_reported_and_other_detectors_still_run(truck):
    closed = RiskOverlay([CorridorRisk("BRENNER", "CLOSED", 1.0, 3)])
    found, errors = scan([truck[2]], closed, FakeWeather(fail=True))
    assert [d.alert_code for d in found] == ["CORRIDOR_CLOSED"] and "open-meteo down" in errors[0]


def test_memory_reports_only_new_problems(truck):
    mem, d = MonitorMemory(), weather_hits(truck[2], FakeWeather(gusts=85))[0]
    assert mem.filter_new([d], NOW) == [d]
    assert mem.filter_new([d], NOW + dt.timedelta(minutes=5)) == []                    # standing problem
    worse = weather_hits(truck[2], FakeWeather(snow=30))[0]
    assert mem.filter_new([worse], NOW + dt.timedelta(minutes=10)) == [worse]          # severity rose
    assert mem.filter_new([], NOW + dt.timedelta(minutes=15)) == []                   # cleared...
    assert mem.filter_new([worse], NOW + dt.timedelta(minutes=20)) == [worse]          # ...and back


def test_replan_cooldown(truck):
    warn = weather_hits(truck[2], FakeWeather(gusts=85))[0].model_copy(update={"delay_days_est": 1.0})
    block = weather_hits(truck[2], FakeWeather(snow=30))[0]
    assert should_replan(block, None, NOW)
    assert not should_replan(warn, (NOW - dt.timedelta(minutes=10), "warn"), NOW)
    assert should_replan(block, (NOW - dt.timedelta(minutes=10), "warn"), NOW)        # escalation
    assert should_replan(warn, (NOW - dt.timedelta(minutes=45), "block"), NOW)        # cooldown over
    assert not should_replan(weather_hits(truck[2], FakeWeather(gusts=85))[0], None, NOW)  # 0.3 d: alert only


def test_simulations(truck, ship):
    assert simulated_weather(truck[2], "snow").corridor == "BRENNER"
    assert simulated_weather(ship, "storm").evidence["wave_height_m"] == 9.0
    with pytest.raises(ValueError):
        simulated_weather(ship, "snow")


def test_demo_snow_at_brenner_reroutes_the_truck(truck):
    """monitor: blocking snow at Brenner -> corridor RESTRICTED (p 0.6) -> replan avoids Brenner."""
    solver, req, t = truck
    d = simulated_weather(t, "snow")
    overlay = RiskOverlay([CorridorRisk("BRENNER", "RESTRICTED", 0.6, 1)])
    assert d.needs_replan
    r = solver.replan(req, t.legs, overlay, position=t.position, today=TODAY)
    assert "BRENNER" not in corridors(r.options[0]) and r.meta["start"] == "road near Munich"
