"""H17-18: agents are bounded in time, use retries, and pipelines degrade instead of hanging."""
import pytest

from autopilot.agents.advisor import route_advisor
from autopilot.agents.intent import intent_agent
from autopilot.pipelines import plan as plan_mod
from autopilot.runner import CALL_TIMEOUT_MS, run_agent_sync
from autopilot.tests.fakes import FakeAudit, FakeLlm
from autopilot.tests.test_plan_pipeline import FORM
from autopilot.tests.test_solver import RED_SEA_RISK, TODAY


def test_every_agent_retries_and_has_a_call_timeout():
    from autopilot.agents.assistant import monora_assistant
    from autopilot.agents.documents import doc_explainer, doc_intake
    from autopilot.agents.risk_scout import build_risk_scout

    scout = build_risk_scout()
    agents = [intent_agent, route_advisor, monora_assistant, doc_intake, doc_explainer,
              *(a for seq in scout.sub_agents for a in seq.sub_agents)]
    for a in agents:
        assert a.model.retry_options.attempts == 3, a.name
        assert a.generate_content_config.http_options.timeout == CALL_TIMEOUT_MS, a.name


def test_slow_model_hits_the_wall_clock_cap(monkeypatch):
    fake = FakeAudit().install(monkeypatch)
    slow = route_advisor.model_copy(update={"model": FakeLlm(responses=["{}"], delay_s=5)})
    with pytest.raises(TimeoutError):
        run_agent_sync(slow, "hi", run=fake.start_run("x"), timeout_s=0.2)


def test_advisor_timeout_falls_back_and_keeps_the_options(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test")
    import autopilot.runner as runner
    monkeypatch.setattr(runner, "AGENT_TIMEOUT_S", 0.2)
    fake = FakeAudit().install(monkeypatch)
    monkeypatch.setattr(plan_mod, "route_advisor",
                        route_advisor.model_copy(update={"model": FakeLlm(responses=["{}"], delay_s=5)}))
    out = plan_mod.plan_request(FORM, run=fake.start_run("plan_routes"), overlay=RED_SEA_RISK, today=TODAY)
    assert out.options and out.verdict.recommended_key == "R1"
    assert "TimeoutError" in fake.events[-1].detail and "source=fallback" in fake.events[-1].detail
