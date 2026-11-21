"""RouteScorer: per-criterion normalization, presets, Pareto front."""
from decimal import Decimal

import pytest

from autopilot.domain.schemas import CargoSpec, Preset, RoutingRequest, Weights
from autopilot.domain.scoring import PRESETS, RouteMetrics, RouteScorer, pareto_front, weights_for


def m(days, cost, rel=0.9, co2=100.0, friction=1.0):
    return RouteMetrics(days, days, days, days, 0.0, cost, co2, rel, 1 - rel, friction)


def test_presets_sum_to_one():
    assert all(sum(w.values()) == pytest.approx(1.0) for w in PRESETS.values())


def test_best_in_set_scores_one_per_criterion():
    fast, cheap = m(10, 5000), m(30, 1000)
    s_fast, s_cheap = RouteScorer(PRESETS[Preset.BEST]).score([fast, cheap])
    assert (s_fast["time"], s_fast["cost"], s_cheap["cost"]) == (1.0, 0.2, 1.0)  # ratio to best
    assert s_cheap["time"] == pytest.approx(1 / 3, abs=1e-3)
    assert s_fast["total"] > s_cheap["total"]  # BEST weighs time over cost
    s_fast, s_cheap = RouteScorer(PRESETS[Preset.CHEAPEST]).score([fast, cheap])
    assert s_cheap["total"] > s_fast["total"]


def test_with_deadline_only_safety_counts():
    """Both make a 60-day deadline comfortably: being 20 days faster is not worth 5x the cost."""
    fast, cheap = m(10, 5000), m(30, 1000)
    s_fast, s_cheap = RouteScorer(PRESETS[Preset.BEST], slack_days=60, speed_matters=False).score([fast, cheap])
    assert s_cheap["time"] > 0.85 and s_cheap["total"] > s_fast["total"]


def test_pareto_drops_dominated():
    assert pareto_front([m(10, 1000), m(20, 2000), m(5, 3000)]) == [0, 2]


def test_explicit_weights_override_preset():
    req = RoutingRequest(origin="A", destination="B", preset="CHEAPEST",
                         weights=Weights(reliability=0, time=1, cost=0, compliance=0, co2=0),
                         cargo=CargoSpec(description="x", weight_t=1, value_eur=Decimal(1)))
    assert weights_for(req)["time"] == pytest.approx(1.0)
