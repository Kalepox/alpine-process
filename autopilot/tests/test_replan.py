"""RouteSolver.replan + replan_request: from where the cargo is, reroutes vs WAIT."""
import pytest

from autopilot.domain.risk import CorridorRisk, RiskOverlay
from autopilot.pipelines.replan import replan_request
from autopilot.tests.fakes import FakeAudit
from autopilot.tests.test_solver import RED_SEA_RISK, TODAY, corridors, request

GULF_OF_ADEN = (12.6, 50.0)


@pytest.fixture(scope="module")
def palm():
    from autopilot.domain.solver import RouteSolver
    solver = RouteSolver()
    req = request("Belawan", "Mersin", "LIQUID_BULK", 5000, 4_500_000)
    approved = next(o for o in solver.plan(req, RED_SEA_RISK, today=TODAY).options if "BAB_EL_MANDEB" in corridors(o))
    return solver, req, approved


def test_ship_at_sea_gets_wait_and_cape(palm):
    solver, req, approved = palm
    closed = RED_SEA_RISK.with_status("BAB_EL_MANDEB", "CLOSED", 14)
    r = solver.replan(req, approved.legs, closed, position=GULF_OF_ADEN, today=TODAY, elapsed_days=10,
                      planned_remaining_days=20, demurrage_per_day=25_000)
    assert r.meta["start"].startswith("at sea") and r.meta["blocking"] == ["BAB_EL_MANDEB"]
    wait = next(o for o in r.options if o.kind == "WAIT")
    routes = [o for o in r.options if o.kind == "ROUTE"]
    assert all("BAB_EL_MANDEB" not in corridors(o) for o in routes)
    assert any("CAPE" in corridors(o) for o in routes)
    assert routes[0].legs[0].from_node == "CURRENT_POSITION"
    assert wait.cost_breakdown["DEMURRAGE"] == 14 * 25_000 and wait.break_even_days == routes[0].break_even_days
    assert all(o.break_even_days is not None and o.delay_days > 0 for o in routes)
    assert [o.recommended for o in r.options].count(True) == 1


def test_longer_closure_flips_wait_to_reroute(palm):
    """Break-even is the decision rule: a 60-day closure is past it, so a reroute wins."""
    solver, req, approved = palm
    closed = RED_SEA_RISK.with_status("BAB_EL_MANDEB", "CLOSED", 60)
    r = solver.replan(req, approved.legs, closed, position=GULF_OF_ADEN, today=TODAY, demurrage_per_day=25_000)
    assert r.options[0].kind == "ROUTE" and r.options[0].break_even_days < 60


def test_wait_infeasible_for_perishables():
    from autopilot.domain.solver import RouteSolver
    solver = RouteSolver()
    req = request("Mumbai", "Mersin", "PERISHABLE_REEFER", 20, 300_000, max_transit_days=40)
    approved = next(o for o in solver.plan(req, RED_SEA_RISK, today=TODAY).options if "BAB_EL_MANDEB" in corridors(o))
    closed = RED_SEA_RISK.with_status("BAB_EL_MANDEB", "CLOSED", 30)
    r = solver.replan(req, approved.legs, closed, position=(14.0, 60.0), today=TODAY, elapsed_days=5)
    wait = next(o for o in r.options if o.kind == "WAIT")
    assert wait.infeasible_reason and not wait.recommended and r.options[-1] is wait


def test_truck_on_the_road_takes_another_pass():
    from autopilot.domain.solver import RouteSolver
    solver = RouteSolver()
    req = request("Rotterdam", "Milan", weight_t=20, value=200_000)
    approved = next(o for o in solver.plan(req, RiskOverlay(), today=TODAY).options if "BRENNER" in corridors(o))
    closed = RiskOverlay([CorridorRisk("BRENNER", "CLOSED", 1.0, 3)])
    r = solver.replan(req, approved.legs, closed, position=(48.2, 11.6), today=TODAY)  # near Munich
    assert r.meta["start"] == "road near Munich"
    assert all("BRENNER" not in corridors(o) for o in r.options if o.kind == "ROUTE")
    assert any(o.kind == "WAIT" for o in r.options)


def test_not_departed_starts_at_leg_origin(palm):
    solver, req, approved = palm
    r = solver.replan(req, approved.legs, RED_SEA_RISK.with_status("SUEZ", "CLOSED", 10), today=TODAY)
    assert r.meta["start"] == "BELAWAN"


def test_replan_request_trail_and_extra_spend(monkeypatch, palm):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    fake = FakeAudit().install(monkeypatch)
    _, req, approved = palm
    run = fake.start_run("replan")
    out = replan_request(req, approved.legs, run=run, overlay=RED_SEA_RISK.with_status("BAB_EL_MANDEB", "CLOSED", 60),
                         today=TODAY, position=GULF_OF_ADEN, demurrage_per_day=25_000)
    assert fake.actions() == ["solver.run", "advisor.verdict"]
    assert out.verdict.reroute_or_wait == "REROUTE" and out.recommended.kind == "ROUTE"
    extra, categories = out.extra_spend()
    assert extra > 0 and "FREIGHT" in categories
