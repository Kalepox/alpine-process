"""Material type -> constraints and weight nudges."""
from decimal import Decimal

import pytest

from autopilot.domain.cargo import effective_constraints, nudge_weights
from autopilot.domain.schemas import CargoClass, CargoSpec, Constraints, RoutingRequest

BEST = {"reliability": 0.35, "time": 0.30, "cost": 0.20, "compliance": 0.10, "co2": 0.05}


def req(cargo_class, dg_class=None, **constraints) -> RoutingRequest:
    cargo = CargoSpec(description="x", cargo_class=cargo_class, weight_t=10, value_eur=Decimal(1), dg_class=dg_class)
    return RoutingRequest(origin="A", destination="B", cargo=cargo, constraints=Constraints(**constraints))


@pytest.mark.parametrize("dg_class,air_banned", [(None, True), ("1", True), ("7", True), ("8", False), ("9", False)])
def test_dangerous_goods_air_and_tunnels(dg_class, air_banned):
    c = effective_constraints(req(CargoClass.DANGEROUS_GOODS, dg_class))
    assert ("AIR" in c.banned_modes) is air_banned
    assert c.adr_tunnels_banned and c.max_transfers == 2


def test_strictest_transfer_limit_wins():
    assert effective_constraints(req(CargoClass.HIGH_VALUE, max_transfers=3)).max_transfers == 1
    assert effective_constraints(req(CargoClass.CONTAINER_GENERAL, max_transfers=3)).max_transfers == 3
    assert effective_constraints(req(CargoClass.CONTAINER_GENERAL)).max_transfers is None


def test_request_and_cargo_bans_merge():
    c = effective_constraints(req(CargoClass.OVERSIZE, banned_modes=["ROAD"], avoid_corridors=["SUEZ"]))
    assert c.banned_modes == {"AIR", "RAIL", "ROAD"} and c.avoid_corridors == {"SUEZ"}


def test_nudges_renormalize():
    w = nudge_weights(BEST, CargoClass.PERISHABLE_REEFER)
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["time"] > BEST["time"] and w["cost"] < BEST["cost"]
    assert nudge_weights(BEST, CargoClass.CONTAINER_GENERAL) == pytest.approx(BEST)
