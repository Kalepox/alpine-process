"""RouteSolver.plan scenario tests (PLAN_B §B2.9 H3-5 invariants)."""
import datetime as dt
from decimal import Decimal

import pytest

from autopilot.domain.risk import CorridorRisk, RiskOverlay
from autopilot.domain.schemas import CargoSpec, Constraints, RoutingRequest
from autopilot.domain.solver import NoRouteFound, RouteSolver

TODAY = dt.date(2026, 11, 21)
RED_SEA_RISK = RiskOverlay([CorridorRisk("SUEZ", "RESTRICTED", 0.18, 14, 0.2),
                            CorridorRisk("BAB_EL_MANDEB", "RESTRICTED", 0.35, 21, 0.7)])


@pytest.fixture(scope="module")
def solver():
    return RouteSolver()


def request(origin, destination, cargo_class="CONTAINER_GENERAL", weight_t=40, value=400_000, **kw):
    cargo_kw = {k: kw.pop(k) for k in ("max_transit_days", "dg_class") if k in kw}
    cargo = CargoSpec(description="x", cargo_class=cargo_class, weight_t=weight_t, value_eur=Decimal(value), **cargo_kw)
    return RoutingRequest(origin=origin, destination=destination, cargo=cargo, **kw)


def corridors(option):
    return {c for leg in option.legs for c in leg.corridors}


def test_contract_of_a_plan(solver):
    r = solver.plan(request("Antofagasta", "Milan plant"), RED_SEA_RISK, today=TODAY)
    keys = [o.key for o in r.options]
    assert 3 <= len(r.options) <= 5 and keys == [f"R{i}" for i in range(1, len(keys) + 1)]
    assert [o.recommended for o in r.options].count(True) == 1 and r.options[0].recommended
    assert all(o.contingency_key in {c.key for c in r.contingencies} for o in r.options if o.contingency_key)
    assert all(o.legs[0].from_node == "ANTOFAGASTA" and o.legs[-1].to_node == "MILAN" for o in r.options)
    assert all(o.days_p10 <= o.transit_days <= o.days_p90 for o in r.options)
    assert r.meta["graph_version"] and r.meta["overlay_hash"] == RED_SEA_RISK.hash()


def test_suez_closed_no_option_uses_it(solver):
    closed = RED_SEA_RISK.with_status("SUEZ", "CLOSED", 30)
    r = solver.plan(request("Belawan", "Mersin", "LIQUID_BULK", 5000, 4_500_000), closed, today=TODAY)
    assert all("SUEZ" not in corridors(o) for o in r.options + r.contingencies)
    assert any("CAPE" in corridors(o) for o in r.options)


def test_red_sea_risk_offers_cape_plan_b(solver):
    r = solver.plan(request("Belawan", "Mersin", "LIQUID_BULK", 5000, 4_500_000), RED_SEA_RISK, today=TODAY)
    via_red_sea = next(o for o in r.options if "BAB_EL_MANDEB" in corridors(o))
    assert via_red_sea.possible_disruptions[0].corridor == "BAB_EL_MANDEB"
    plan_b = next(c for c in r.contingencies if c.key == via_red_sea.contingency_key)
    assert "CAPE" in corridors(plan_b)


def test_gold_flies_with_at_most_one_transfer(solver):
    r = solver.plan(request("Dubai", "Istanbul", "HIGH_VALUE", 0.5, 30_000_000), RiskOverlay(), today=TODAY)
    assert all(len(o.legs) - 1 <= 1 for o in r.options)
    assert r.options[0].legs[0].mode == "AIR"


def test_reefer_respects_shelf_life(solver):
    r = solver.plan(request("Mumbai", "Milan", "PERISHABLE_REEFER", 20, 300_000, max_transit_days=30),
                    RED_SEA_RISK, today=TODAY)
    assert all(o.days_p90 <= 30 for o in r.options)


def test_greenest_never_flies(solver):
    r = solver.plan(request("Dubai", "Milan", preset="GREENEST"), RiskOverlay(), today=TODAY)
    assert r.options[0].legs and all(leg.mode != "AIR" for leg in r.options[0].legs)


def test_tight_deadline_means_air_or_road(solver):
    r = solver.plan(request("Dubai", "Milan", deadline=TODAY + dt.timedelta(days=5)), RiskOverlay(), today=TODAY)
    assert all(leg.mode in {"AIR", "ROAD"} for o in r.options for leg in o.legs)


def test_dangerous_goods_avoid_road_tunnels(solver):
    r = solver.plan(request("Rotterdam", "Milan", "DANGEROUS_GOODS", 20, dg_class="3"), RiskOverlay(), today=TODAY)
    for o in r.options:
        for leg in o.legs:
            assert not (leg.mode == "ROAD" and {"GOTTHARD", "MONT_BLANC"} & set(leg.corridors))


def test_copper_with_far_deadline_ships_by_sea(solver):
    """40 t of copper, 100+ days of slack: flying is fast but buys nothing."""
    r = solver.plan(request("Antofagasta", "Milan plant", value=380_000, deadline=dt.date(2027, 3, 3)),
                    RED_SEA_RISK, today=TODAY)
    assert r.options[0].legs[0].mode == "SEA"


def test_deterministic(solver):
    a = solver.plan(request("Antofagasta", "Milan"), RED_SEA_RISK, today=TODAY)
    b = solver.plan(request("Antofagasta", "Milan"), RED_SEA_RISK, today=TODAY)
    assert [o.model_dump() for o in a.options] == [o.model_dump() for o in b.options] and a.meta == b.meta


def test_no_route(solver):
    with pytest.raises(NoRouteFound):
        solver.plan(request("Antofagasta", "Milan", constraints=Constraints(banned_modes=["SEA", "AIR"])),
                    RiskOverlay(), today=TODAY)
