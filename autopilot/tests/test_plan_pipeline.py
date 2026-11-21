"""plan_request: parse -> solve -> advise, offline (fallback) and with a scripted Gemini."""
import pytest

from autopilot.domain.risk import RiskOverlay
from autopilot.pipelines import plan as plan_mod
from autopilot.tests.fakes import FakeAudit, FakeLlm
from autopilot.tests.test_solver import RED_SEA_RISK, TODAY

FORM = {"origin": "Belawan", "destination": "Mersin",
        "cargo": {"description": "palm oil", "cargo_class": "LIQUID_BULK", "weight_t": 5000, "value_eur": "4500000"}}


@pytest.fixture
def fake_audit(monkeypatch):
    return FakeAudit().install(monkeypatch)


@pytest.fixture
def no_gemini(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)


@pytest.fixture
def scripted_gemini(monkeypatch):
    """Gemini 'configured', both agents answered by FakeLlm scripts."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test")

    def script(agent_name, responses):
        llm = FakeLlm(responses=list(responses))
        agent = getattr(plan_mod, agent_name)
        monkeypatch.setattr(plan_mod, agent_name, agent.model_copy(update={"model": llm}))
        return llm

    return script


def test_offline_form_uses_fallback_verdict(fake_audit, no_gemini):
    run = fake_audit.start_run("plan_routes")
    out = plan_mod.plan_request(FORM, run=run, overlay=RED_SEA_RISK, today=TODAY)
    assert [o.recommended for o in out.options].count(True) == 1 and out.options[0].recommended
    assert out.options[0].rationale and out.verdict.recommended_key == "R1"
    assert fake_audit.actions() == ["solver.run", "advisor.verdict"]
    assert "source=fallback" in fake_audit.events[-1].detail
    assert fake_audit.events[0].output["overlay_hash"] == RED_SEA_RISK.hash()


def test_free_text_needs_gemini_or_form(fake_audit, no_gemini):
    run = fake_audit.start_run("plan_routes")
    with pytest.raises(plan_mod.IntentUnavailable):
        plan_mod.plan_request({"text": "5000 t palm oil Belawan to Mersin"}, run=run, overlay=RiskOverlay(), today=TODAY)


def test_free_text_through_intent_and_advisor(fake_audit, scripted_gemini):
    intent = scripted_gemini("intent_agent", [{
        "origin": "Belawan", "destination": "Mersin", "preset": "MOST_RELIABLE",
        "cargo": {"description": "palm oil", "cargo_class": "LIQUID_BULK", "weight_t": 5000, "value_eur": 4500000},
    }])
    scripted_gemini("route_advisor", [{
        "recommended_key": "R1", "rationale": "R1 avoids the Red Sea entirely.",
        "tradeoffs": [{"option_key": "R2", "text": "Faster but exposed to closures."}],
    }])
    run = fake_audit.start_run("plan_routes")
    out = plan_mod.plan_request({"text": "5000 t palm oil Belawan->Mersin, safety first", "deadline": None},
                                run=run, overlay=RED_SEA_RISK, today=TODAY)
    assert out.request.preset == "MOST_RELIABLE"
    assert "TODAY: 2026-11-21" in intent.requests[0].contents[-1].parts[0].text
    assert "BAB_EL_MANDEB" not in {c for leg in out.options[0].legs for c in leg.corridors}  # reliability first
    assert out.options[1].rationale == "Faster but exposed to closures."
    actions = fake_audit.actions()
    assert actions[:3] == ["agent.intent_agent.start", "model.call", "agent.intent_agent.final"]
    assert "solver.run" in actions and "source=route_advisor" in fake_audit.events[-1].detail


def test_advisor_inventing_numbers_falls_back(fake_audit, scripted_gemini):
    scripted_gemini("route_advisor", [{"recommended_key": "R2", "rationale": "R2 saves exactly 77777 EUR.", "tradeoffs": []}])
    run = fake_audit.start_run("plan_routes")
    out = plan_mod.plan_request(FORM, run=run, overlay=RED_SEA_RISK, today=TODAY)
    assert out.verdict.recommended_key == "R1"
    assert "77777" in fake_audit.events[-1].detail and "source=fallback" in fake_audit.events[-1].detail


def test_plan_b_flag_is_enforced_by_code(fake_audit, scripted_gemini):
    """The advisor forgets the Plan B flag on a Red Sea route; code sets it."""
    scripted_gemini("route_advisor", [{"recommended_key": "R1", "rationale": "R1.", "plan_b_required": False}])
    run = fake_audit.start_run("plan_routes")
    out = plan_mod.plan_request({**FORM, "preset": "FASTEST"}, run=run, overlay=RED_SEA_RISK, today=TODAY)
    r1 = out.options[0]
    assert any(d.corridor == "BAB_EL_MANDEB" for d in r1.possible_disruptions)
    assert out.verdict.plan_b_required
