"""risk_scout end to end (scripted Gemini) and the code that turns its output into corridor changes."""
import pytest

from autopilot.agents.risk_scout import GROUPS, build_risk_scout
from autopilot.domain.risk import CorridorRisk, RiskOverlay, assess, effective_status
from autopilot.domain.schemas import RiskSignal, SourceRef
from autopilot.pipelines.disruptions import assessments_from_state, changes
from autopilot.runner import run_agent_sync
from autopilot.tests.fakes import FakeAudit, FakeLlm

NEWS = "https://news.example/red-sea"


@pytest.fixture
def fake_audit(monkeypatch):
    return FakeAudit().install(monkeypatch)


def test_levels_become_numbers_by_code():
    a = assess(RiskSignal(corridor="SUEZ", status="RESTRICTED", level="HIGH"), [])
    assert (a.risk_score, a.p_close_30d) == (0.60, 0.25)
    closed = assess(RiskSignal(corridor="BAB_EL_MANDEB", status="CLOSED", level="SEVERE", reopening_estimate_days=14),
                    [SourceRef(url=NEWS)])
    assert closed.p_close_30d == 1.0 and closed.expected_duration_days == 14 and effective_status(closed) == "CLOSED"
    unsourced = assess(RiskSignal(corridor="HORMUZ", status="CLOSED", level="SEVERE"), [])
    assert effective_status(unsourced) == "RESTRICTED"


def test_state_parsing_rejects_unknown_corridors(fake_audit):
    run = fake_audit.start_run("scan_disruptions")
    state = {
        "risk_sea": [{"corridor": "BAB_EL_MANDEB", "status": "CLOSED", "level": "SEVERE"},
                     {"corridor": "BRENNER", "status": "OPEN", "level": "LOW"},          # wrong group
                     {"corridor": "SUEZ", "status": "MAYBE", "level": "LOW"}],           # invalid
        "sources_sea": [{"title": "news", "url": NEWS}],
    }
    out = assessments_from_state(state, run=run)
    assert [a.corridor for a in out] == ["BAB_EL_MANDEB"] and out[0].sources[0].url == NEWS
    assert fake_audit.actions() == ["risk.rejected", "risk.rejected"]


def test_only_real_changes_count():
    current = RiskOverlay([CorridorRisk("SUEZ", "RESTRICTED", 0.25), CorridorRisk("PANAMA", "OPEN", 0.02)])
    same = assess(RiskSignal(corridor="SUEZ", status="RESTRICTED", level="HIGH"), [])
    tiny = assess(RiskSignal(corridor="PANAMA", status="OPEN", level="LOW"), [])
    big = assess(RiskSignal(corridor="HORMUZ", status="RESTRICTED", level="HIGH"), [])
    assert [a.corridor for a in changes([same, tiny, big], current)] == ["HORMUZ"]


def test_parallel_scouts_run_and_keep_search_sources(fake_audit):
    llms = {}

    def model_for(name: str):
        group = name.split("_")[1]
        if name.startswith("searcher"):
            llm = FakeLlm(model="gemini-2.5-flash", responses=[f"{group}: no reported disruption"],
                          grounding_urls=[NEWS] if group == "sea" else [])
        else:
            code = GROUPS[group][0]
            llm = FakeLlm(model="gemini-2.5-flash", responses=[[{"corridor": code, "status": "OPEN", "level": "LOW"}]])
        llms[name] = llm
        return llm

    run = fake_audit.start_run("scan_disruptions")
    state = run_agent_sync(build_risk_scout(model_for), "TODAY: 2026-11-21", run=run)
    assert {f"risk_{g}" for g in GROUPS} <= state.keys() and state["sources_sea"] == [{"title": "news", "url": NEWS}]
    # the structurer saw the searcher's summary (state injected into its instruction)
    assert "sea: no reported disruption" in llms["structurer_sea"].requests[0].config.system_instruction
    actions = fake_audit.actions()
    assert actions[0] == "agent.risk_scout.start" and actions[-1] == "agent.risk_scout.final"
    assert sum(a == "model.call" for a in actions) == 8
    sea_search = next(e for e in fake_audit.events if e.action == "model.call" and e.sources)
    assert sea_search.actor_name == "searcher_sea"
