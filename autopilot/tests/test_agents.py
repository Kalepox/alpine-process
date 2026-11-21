"""Agents run through make_runner: AuditPlugin trail, structured output, advisor validation."""
import pytest

from autopilot.agents.advisor import advisor_payload, route_advisor, ungrounded_numbers, validate_verdict
from autopilot.domain.schemas import CargoSpec, RoutingRequest
from autopilot.runner import run_agent_sync
from autopilot.tests.fakes import FakeAudit, FakeLlm
from autopilot.tests.test_solver import RED_SEA_RISK, TODAY

from autopilot.domain.solver import RouteSolver


@pytest.fixture
def fake_audit(monkeypatch):
    return FakeAudit().install(monkeypatch)


@pytest.fixture(scope="module")
def plan():
    req = RoutingRequest(origin="Belawan", destination="Mersin",
                         cargo=CargoSpec(description="palm oil", cargo_class="LIQUID_BULK", weight_t=5000, value_eur=4_500_000))
    return req, RouteSolver().plan(req, RED_SEA_RISK, today=TODAY)


def test_runner_logs_every_step(fake_audit, plan):
    req, result = plan
    llm = FakeLlm(responses=[{"recommended_key": "R2", "rationale": "Avoids the Red Sea.", "tradeoffs": []}],
                  grounding_urls=["https://news.example/red-sea"])
    agent = route_advisor.model_copy(update={"model": llm})
    run = fake_audit.start_run("plan_routes")
    out = run_agent_sync(agent, "options...", run=run)

    assert out["recommended_key"] == "R2"
    assert fake_audit.actions() == ["agent.route_advisor.start", "model.call", "agent.route_advisor.final"]
    start, model, final = fake_audit.events
    assert all(e.run is run and e.actor_type == "agent" for e in fake_audit.events)
    assert model.parent is start and final.parent is start
    assert model.meta["tokens_in"] == 100 and model.sources == [{"title": "news", "url": "https://news.example/red-sea"}]
    assert final.output["recommended_key"] == "R2"


def test_verdict_validation(plan):
    req, result = plan
    payload = advisor_payload(req, result.options)
    days = result.options[0].transit_days
    ok = {"recommended_key": "R1", "rationale": f"R1 takes about {days} days and avoids 2 transfers.", "tradeoffs": []}
    assert validate_verdict(ok, result.options, payload)[0] is not None
    unknown = {**ok, "recommended_key": "R9"}
    assert "unknown option keys" in validate_verdict(unknown, result.options, payload)[1]
    invented = {**ok, "rationale": "R1 saves 123456 EUR."}
    assert "123456" in validate_verdict(invented, result.options, payload)[1]


def test_ungrounded_numbers_ignores_keys_and_small_counts():
    assert ungrounded_numbers("R12 and R1-B: 3 transfers, 28.6 days", {"x": 28.6}) == set()
    assert ungrounded_numbers("costs 9,041 EUR", {"x": 9040.71}) == {"9041"}
