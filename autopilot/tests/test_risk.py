"""RiskOverlay: LLM risk validated by code, closure events -> delay distribution."""
import pytest

from autopilot.domain.risk import CorridorRisk, RiskOverlay, quantile
from autopilot.domain.schemas import RiskAssessment, SourceRef


def test_unverified_closure_is_downgraded():
    ov = RiskOverlay.from_assessments([
        RiskAssessment(corridor="SUEZ", status="CLOSED", risk_score=0.9, p_close_30d=0.9),
        RiskAssessment(corridor="BAB_EL_MANDEB", status="CLOSED", risk_score=0.9, p_close_30d=0.9,
                       sources=[SourceRef(url="https://example.org/news")]),
    ])
    assert ov.closed == {"BAB_EL_MANDEB"}
    assert ov.get("SUEZ").status == "RESTRICTED" and not ov.get("SUEZ").verified


def test_delay_distribution_is_exact():
    ov = RiskOverlay([CorridorRisk("A", p_close_30d=0.5, expected_duration_days=10),
                      CorridorRisk("B", p_close_30d=0.2, expected_duration_days=4)])
    dist = dict(ov.delay_distribution(["A", "B", "UNKNOWN"]))
    assert dist == pytest.approx({0.0: 0.4, 4.0: 0.1, 10.0: 0.4, 14.0: 0.1})
    assert quantile(sorted(dist.items()), 0.5) == 4.0  # P(0)=0.4, P(<=4)=0.5
    assert quantile(sorted(dist.items()), 0.9) == 10.0
    assert ov.p_no_closure(["A", "B"]) == pytest.approx(0.4)


def test_from_rows_clamps_and_accepts_dicts():
    ov = RiskOverlay.from_rows([{"code": "HORMUZ", "status": "RESTRICTED", "p_close_30d": 3, "expected_duration_days": 999}])
    r = ov.get("HORMUZ")
    assert (r.p_close_30d, r.expected_duration_days) == (1.0, 120)


def test_with_status_and_stable_hash():
    ov = RiskOverlay([CorridorRisk("SUEZ", p_close_30d=0.1)])
    closed = ov.with_status("SUEZ", "CLOSED", 14)
    assert closed.closed == {"SUEZ"} and ov.closed == frozenset()
    assert ov.hash() == RiskOverlay([CorridorRisk("SUEZ", p_close_30d=0.1)]).hash() != closed.hash()
