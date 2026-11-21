"""Eval harness (offline suites), assistant tools, and the assistant calling a tool through ADK."""
import pytest
from google.genai import types

from autopilot.evals import run_evals
from autopilot.tests.fakes import FakeAudit, FakeLlm


@pytest.fixture
def no_gemini(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)


def test_offline_eval_suites_pass(no_gemini):
    rows = run_evals(["routes", "monitor"])
    assert len(rows) == 11 and all(r.passed for r in rows), [(r.case, r.got) for r in rows if not r.passed]
    assert all(r.passed is None for r in run_evals(["risk"]))  # needs Gemini: skipped, not failed


@pytest.mark.django_db
def test_what_if_saves_nothing_and_reports_errors():
    from autopilot.tools import _what_if

    out = _what_if("Rotterdam", "Milan", "parts", "CONTAINER_GENERAL", 20, 200_000, "", "BEST")
    assert out["saved"] is False and 3 <= len(out["options"]) <= 5 and out["options"][0]["recommended"]
    assert "error" in _what_if("Atlantis", "Milan", "parts", "", 20, 1, "", "BEST")


@pytest.mark.django_db(transaction=True)
def test_assistant_calls_a_tool_and_the_trail_records_it(monkeypatch):
    from autopilot.agents.assistant import monora_assistant
    from autopilot.runner import run_agent_sync

    fake = FakeAudit().install(monkeypatch)
    llm = FakeLlm(responses=[
        types.Part(function_call=types.FunctionCall(name="get_risk_overlay", args={})),
        "No corridor is closed right now.",
    ])
    answer = run_agent_sync(monora_assistant.model_copy(update={"model": llm}), "Is anything closed?",
                            run=fake.start_run("ask"))
    assert answer == "No corridor is closed right now."
    tool = next(e for e in fake.events if e.action == "tool.get_risk_overlay")
    assert tool.output == {"corridors": []} and tool.meta["latency_ms"] is not None


@pytest.mark.django_db
def test_ask_without_gemini_explains_itself(monkeypatch, no_gemini, django_user_model):
    from autopilot.pipelines.assistant import ask

    fake = FakeAudit().install(monkeypatch)
    user = django_user_model.objects.create(username="elena")
    assert "needs Gemini" in ask("why not rail?", user.pk)
    assert fake.runs[0].status == "OK"
