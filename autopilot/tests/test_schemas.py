"""Contract v2 DTOs: LLM output is clamped, advisor citations are checkable."""
from decimal import Decimal

from autopilot.domain.schemas import (
    AdvisorVerdict,
    CargoClass,
    CargoSpec,
    Disruption,
    RiskAssessment,
    RoutingRequest,
    Tradeoff,
)


def test_risk_assessment_clamps_llm_values():
    ra = RiskAssessment(
        corridor="BAB_EL_MANDEB", status="CLOSED", risk_score=1.4, p_close_30d=-0.2,
        expected_duration_days=400, war_risk_pct=9,
    )
    assert (ra.risk_score, ra.p_close_30d, ra.expected_duration_days, ra.war_risk_pct) == (1.0, 0.0, 120, 5.0)
    assert not ra.verified  # no sources -> never closes a corridor on its own


def test_advisor_unknown_keys():
    v = AdvisorVerdict(recommended_key="R2", rationale="...", tradeoffs=[Tradeoff(option_key="R9", text="...")])
    assert v.unknown_keys({"R1", "R2", "WAIT"}) == {"R9"}


def test_routing_request_defaults_and_json_schema():
    req = RoutingRequest(
        origin="Antofagasta", destination="Milan",
        cargo=CargoSpec(description="copper cathodes", weight_t=40, value_eur=Decimal("380000")),
    )
    assert req.preset == "BEST" and req.cargo.cargo_class is CargoClass.CONTAINER_GENERAL
    assert "cargo" in RoutingRequest.model_json_schema()["properties"]  # usable as ADK output_schema


def test_disruption_needs_replan():
    base = dict(kind="WEATHER", alert_code="WEATHER_RISK", shipment_id=1, leg_index=0)
    assert Disruption(severity="block", **base).needs_replan
    assert Disruption(severity="warn", delay_days_est=1.5, **base).needs_replan
    assert not Disruption(severity="warn", delay_days_est=0.3, **base).needs_replan
